"""The HTTP listener: webhook intake, the work queue, hardlink parking, the
built web UI and the JSON API under /api/.

Radarr and Sonarr (the *arrs) post once per imported file. The handler parses
and queues; worker threads probe and rewrite, so a slow *arr never stalls the
response. Machine secrets are checked by :mod:`trackstarr.auth`.

Browsers use a session cookie from /api/auth/login (:mod:`trackstarr.users`,
:mod:`trackstarr.sessions`). A webhook secret is accepted for reads only, so a
leaked *arr credential can watch the queue but not change settings.
"""

import gzip
import hashlib
import json
import logging
import mimetypes
import os
import queue
import re
import threading
import time
import urllib.parse
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler

from . import (
    __version__,
    auth,
    config,
    connections,
    events,
    library,
    links,
    notify,
    ratings,
    runs,
    sessions,
    settings,
    sweep,
    users,
)
from .arr import AUTH_HEADER, WEBHOOK_PATH, all_arrs, original_of, webhook_url
from .client import API_ERRORS
from .processing import Job, downmixed_names, effective_dry_run, process
from .state import write_json
from .status import Status
from .sweep_cache import cache_key

log = logging.getLogger(__name__)

#: The session cookie's name; its value is a :func:`trackstarr.sessions.create` token.
SESSION_COOKIE = "trackstarr_session"

#: Posters are keyed by title id and never change, so a browser keeps one for a
#: week without revalidating.
_COVER_CACHE = "private, max-age=604800, immutable"

#: Stored but revalidated every time. The library changes whenever a sweep does
#: and the loader refetches it on every visit; an ETag check is a header where
#: the body is hundreds of kilobytes.
_FRESH_CACHE = "private, no-cache"

#: Below this a gzip header and trailer cost about what compression saves.
_GZIP_MIN = 1024

#: Where the curve flattens for JSON: a 500-title shelf shrinks 6.3x in about a
#: millisecond, and level 9 spends several more for a few hundred bytes.
_GZIP_LEVEL = 6

#: Seconds between heartbeats on an idle stream. A closed tab resets the socket,
#: but a phone that dropped off wifi says nothing; heartbeats give TCP
#: unacknowledged bytes to time out on, so the thread is shed in minutes. Also
#: short enough that a proxy's idle timeout never fires.
_STREAM_HEARTBEAT = 20.0

#: The heartbeat frame.
_PING = b'data: {"kind": "ping"}\n\n'

#: Static files worth compressing: markup, styles, bundles and SVG. Fonts and
#: pictures arrive compressed already.
_COMPRESSIBLE = re.compile(r"^(text/|application/(javascript|json|xml)|image/svg\+xml)")

#: Content Security Policy for the pages: this origin, plus `data:` for the
#: SVG placeholder posters and the woff2 faces Vite inlines. Styles allow
#: inline because Svelte's `style:` directives are inline attributes.
#:
#: `script-src` allows inline on purpose. The shell carries two inline scripts
#: (the pre-paint theme resolve and SvelteKit's boot), and omitting the
#: directive hands them to `default-src`, which blanks the app. Closing it
#: means hashing both at build time, where SvelteKit's changes every release.
#: The rest still holds: no third-party hosts, no fetch or form to anywhere
#: else, no <base> rewrite, and no framing.
_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def hardlinked(path: str) -> bool:
    """More than one directory entry shares the file's inode.

    In an *arr setup that means the download client is still seeding it. An
    unreadable file counts as not hardlinked; the probe will say so.
    """
    try:
        return os.stat(path).st_nlink > 1
    except OSError:
        return False


_work_q: queue.Queue[Job] = queue.Queue()

#: Imports arrive in bursts, so one path can be queued twice before the first
#: job runs. The planner would no-op; this saves the probe.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()

#: Webhook jobs whose file the download client still hard-links, re-checked on
#: a timer until the link count drops. Persisted, since the import is the last
#: event the *arr fires and a restart would otherwise strand them.
_parked: dict[str, Job] = {}
_parked_lock = threading.Lock()

#: The parked set's file in STATE_DIR.
PARKED_FILE = "parked.json"


def _parked_path() -> str:
    return os.path.join(config.STATE_DIR, PARKED_FILE)


def _save_parked() -> None:
    """Write the parked set atomically. Never raises.

    The lock covers the write as well as the snapshot, or two racing writes
    could leave the older one on top.
    """
    with _parked_lock:
        records = [
            {
                "path": job.path,
                "lang": job.lang,
                "item_id": job.item_id,
                "arr": job.arr.name if job.arr else None,
                "run": job.run,
            }
            for job in _parked.values()
        ]
        try:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            write_json(_parked_path(), records)
        except OSError as err:
            log.warning("could not persist the parked set: %s", err)


def load_parked() -> None:
    """Restore the parked set a previous run left behind.

    Read even with parking off: the recheck thread runs either way and is the
    only thing that will release them. Unreadable entries are dropped and wait
    for the next sweep.
    """
    try:
        with open(_parked_path()) as parked_file:
            records = json.load(parked_file)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as err:
        log.warning("ignoring unreadable parked set %s: %s", _parked_path(), err)
        return
    arrs = {arr.name: arr for arr in all_arrs()}
    restored = {
        record["path"]: Job(
            record["path"],
            record.get("lang"),
            record.get("item_id"),
            # "" matches no *arr name, for a job that had none.
            arrs.get(record.get("arr") or ""),
            record.get("run"),
        )
        for record in (records if isinstance(records, list) else [])
        if isinstance(record, dict) and record.get("path")
    }
    if not restored:
        return
    with _parked_lock:
        _parked.update(restored)
    log.info("restored %d file(s) parked by a previous run", len(restored))


def _resolve_lang(job: Job) -> Job:
    """Fetch the original language when the webhook body lacked it.

    Older Radarr and Sonarr omit ``originalLanguage``. Looked up on the worker
    so the HTTP handler never waits on an *arr.
    """
    if job.lang is not None or not job.arr or not job.item_id:
        return job
    return replace(job, lang=original_of(job.arr.item(job.item_id)))


def parking_enabled() -> bool:
    return config.SKIP_HARDLINKS and config.HARDLINK_RECHECK > 0


def _park(job: Job) -> None:
    with _parked_lock:
        _parked[job.path] = job
    _save_parked()
    log.info("parked %s until the download client releases it", job.path)


def _handle(job: Job) -> None:
    """Process a webhook job, parking it while the file is still seeded."""
    if runs.stopping(job.run):
        # Dropped rather than processed: "stop" must not mean "stop after the
        # twenty files already queued".
        runs.drop(job.run)
        log.info("run %s is stopping, dropping %s", job.run, job.path)
        return
    job = _resolve_lang(job)
    if parking_enabled() and hardlinked(job.path):
        _park(job)
        # Booked as deferred so the run can close; an uncounted file would keep
        # the delivery on the overview for ever.
        runs.tally(
            job.run,
            str(Status.DEFERRED),
            path=job.path,
            detail="a download client still has this hard-linked",
        )
        return
    runs.begin(job.run, job.path)
    # Taken before the probe, as the sweep does: a still-settling import must
    # not have its verdict filed under a later size and mtime.
    key = cache_key(job.path, job.lang)
    result = None
    try:
        result = process(job, dry_run=False)
    finally:
        runs.finish(job.run, job.path)
        # Booked whatever happened, or a run that never reaches its total
        # never closes.
        runs.tally(
            job.run,
            str(result.status if result else Status.FAILED),
            path=job.path,
            # Only a deferral or a failure carries a detail.
            detail=result.detail if result else "",
        )
    # The library reads verdicts from the cache, not the history.
    sweep.remember(job.path, key, result)
    if result.status is Status.WOULD_FIX and result.plan:
        # No pending.tsv row for a webhook, so the history is the only record.
        events.record(
            "would-fix",
            run=job.run,
            source="webhook",
            config_id=result.plan.policy.digest(),
            path=job.path,
            reasons=result.plan.reasons,
            rules=sorted(result.plan.rules),
            incidental=result.plan.incidental,
            incidental_rules=sorted(result.plan.incidental_rules),
            downmixed=downmixed_names(result.plan) or None,
        )


#: Live worker threads, and how many have ever been named. The pool follows
#: MAX_CONCURRENT_REWRITES, which the settings page can change, so the count
#: is shared between the thread that tops it up and the workers that retire
#: themselves. Names are never reused.
_workers = 0
_worker_names = 0
_workers_lock = threading.Lock()

#: How long a worker waits on the queue before re-reading the budget.
_WORKER_POLL_SECONDS = 30.0


def start_workers() -> None:
    """Bring the worker pool up to MAX_CONCURRENT_REWRITES.

    Called at startup and after every settings save. Idempotent; a lowered
    budget is left to the workers, which retire themselves.
    """
    global _workers, _worker_names
    with _workers_lock:
        while _workers < config.MAX_CONCURRENT_REWRITES:
            _workers += 1
            _worker_names += 1
            threading.Thread(target=worker, daemon=True, name=f"worker-{_worker_names}").start()


def _retire() -> bool:
    """Whether the calling worker should stop because the budget was cut.

    Decremented here rather than by the thread that lowered the budget, so
    exactly as many workers leave as the budget dropped.
    """
    global _workers
    with _workers_lock:
        if _workers <= config.MAX_CONCURRENT_REWRITES:
            return False
        _workers -= 1
        return True


# No cover: a thread body. _handle and _retire hold the decisions and are covered.
def worker() -> None:  # pragma: no cover
    while not _retire():
        # Checked before the queue, so a paused service leaves its imports
        # queued rather than holding one the overview shows as in progress.
        # Waited in slices so a retirement still happens.
        if not runs.wait_for_resume(_WORKER_POLL_SECONDS):
            continue
        try:
            job = _work_q.get(timeout=_WORKER_POLL_SECONDS)
        except queue.Empty:
            continue
        try:
            _handle(job)
        except Exception:
            log.exception("unhandled error processing %s", job.path)
        finally:
            with _inflight_lock:
                _inflight.discard(job.path)
            _work_q.task_done()


def enqueue(job: Job) -> bool:
    with _inflight_lock:
        if job.path in _inflight:
            return False
        _inflight.add(job.path)
    # Counted before the queue, or a worker could finish the file and book it
    # against a run that has not been told to expect it.
    if job.run:
        runs.add_file(job.run)
    _work_q.put(job)
    return True


def _recheck_parked() -> None:
    """Queue parked jobs whose extra hard links have gone.

    With parking switched off since, the whole set is released: SKIP_HARDLINKS
    off means rewrite on import and take the disk cost.
    """
    parking = parking_enabled()
    with _parked_lock:
        parked = list(_parked.values())
    released = False
    for job in parked:
        if parking and hardlinked(job.path):
            continue
        with _parked_lock:
            _parked.pop(job.path, None)
        released = True
        if not os.path.exists(job.path):
            # Upgraded or deleted; the successor has its own webhook.
            log.info("parked file disappeared, dropping %s", job.path)
            continue
        # Reopen the delivery's run for this one file, then seal it again.
        if job.run:
            runs.open_run(job.run, runs.IMPORT, label=job.arr.name if job.arr else "")
        if enqueue(job):
            log.info("hard link released, queued %s", job.path)
        if job.run:
            runs.seal(job.run)
    # One write per pass, however many were released.
    if released:
        _save_parked()


#: The recheck thread sleeps in slices this long so a HARDLINK_RECHECK change
#: from the UI is picked up without a restart.
_PARKED_TICK = 30.0


# No cover: a thread body around _recheck_parked, which is covered directly.
def parked_recheck_loop() -> None:  # pragma: no cover
    """Revisit parked files every HARDLINK_RECHECK seconds.

    Started whether or not parking is on, so switching SKIP_HARDLINKS on later
    does not park files nothing revisits. Switching it off runs the next pass
    at once.
    """
    waited = 0.0
    while True:
        time.sleep(_PARKED_TICK)
        waited += _PARKED_TICK
        if parking_enabled() and waited < config.HARDLINK_RECHECK:
            continue
        waited = 0.0
        try:
            _recheck_parked()
        except Exception:
            log.exception("parked recheck failed")


#: Download, plus older Radarr's name for it. Rename is left out: it changes
#: no track content.
_ACTIONABLE_EVENTS = frozenset({"Download", "MovieFileImported"})


def jobs_from_hook(body: dict, run: str | None = None) -> list[Job]:
    """The files a webhook body names, each as a job tagged with ``run``."""
    if body.get("eventType") not in _ACTIONABLE_EVENTS:
        return []
    for arr in all_arrs():
        item = body.get(arr.body_key)
        if item is None:
            continue
        lang = original_of(item)
        folder = item.get(arr.folder_key) or ""
        files = [body[arr.file_key]] if arr.file_key in body else body.get(arr.files_key, [])
        return [Job(path, lang, item.get("id"), arr, run) for path in _paths(files, folder)]
    return []


def _paths(files: list[dict], folder: str) -> list[str]:
    """Absolute path per file, preferring the one the *arr gave.

    An empty relativePath must not fall through to the folder, or a directory
    would be queued as a file.
    """
    out = []
    for file_info in files:
        path = file_info.get("path")
        if not path:
            rel = file_info.get("relativePath") or ""
            path = os.path.join(folder, rel) if folder and rel else ""
        if path:
            out.append(path)
    return out


#: A full-season Sonarr import is tens of KB. Past this it is not a webhook.
_MAX_BODY = 8 << 20


def _accepts_gzip(header: str) -> bool:
    """Whether an Accept-Encoding header asks for gzip.

    Not a substring test: ``gzip;q=0`` is a refusal.
    """
    for coding in header.split(","):
        name, _, params = coding.strip().partition(";")
        if name.strip().lower() != "gzip":
            continue
        quality = params.strip().lower()
        if not quality.startswith("q="):
            return True
        try:
            return float(quality[2:]) > 0
        # An unparseable quality on a coding the client named: take it as yes.
        except ValueError:
            return True
    return False


def _static_file(url_path: str) -> str | None:
    """The file WEB_DIR serves for a URL, or None to 404.

    Paths resolving outside WEB_DIR are refused. Extensionless misses fall
    back to index.html for the client-side router; a missing asset is a 404.
    """
    if not config.WEB_DIR:
        return None
    root = os.path.abspath(config.WEB_DIR)
    candidate = os.path.normpath(os.path.join(root, url_path.lstrip("/")))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "index.html")
    if os.path.isfile(candidate):
        return candidate
    if "." not in os.path.basename(candidate):
        fallback = os.path.join(root, "index.html")
        if os.path.isfile(fallback):
            return fallback
    return None


def _queue_status() -> dict:
    """What is waiting, what is in progress, and whether a start may rewrite.

    ``queue`` counts every file still owed, from any source: queued deliveries,
    a sweep's unwalked remainder, a re-check's folders. See
    :func:`trackstarr.runs.workload`.

    ``may_rewrite`` tells the page whether REWRITE_MODE would downgrade an
    apply to a report; nothing else it can read says so.
    """
    with _parked_lock:
        parked = len(_parked)
    waiting, working = runs.workload()
    return {
        "queue": waiting,
        "working": working,
        # Encodes in flight, as distinct from probes: what a stop would waste.
        "rewrites": runs.rewrites(),
        "parked": parked,
        "may_rewrite": not effective_dry_run(False),
        "next_sweep": sweep.next_scheduled(),
    }


def _subject(found: library.Title) -> links.Subject:
    """The few facts about a title that :mod:`trackstarr.links` needs."""
    return links.Subject(
        folder=found.folder,
        name=found.name,
        year=found.year,
        arr=found.arr.name if found.arr else "",
        slug=found.slug,
        imdb_id=found.imdb_id,
    )


def _event_files(entry: dict) -> list[str]:
    """The library files one history line names.

    A verdict names one file; a delivery names everything it queued; other
    kinds name none.
    """
    if isinstance(queued := entry.get("paths"), list):
        return [path for path in queued if isinstance(path, str) and path]
    path = entry.get("path")
    return [path] if isinstance(path, str) and path else []


def _attach_titles(entries: list[dict]) -> dict[str, dict]:
    """Tag each line with its title id and return the cards for those titles.

    A delivery spanning two titles is left untagged: a poster naming half of
    it is worse than none. One library call per page, and it never raises,
    since the history must draw without the *arrs.
    """
    owners, cards = library.cards_for_paths(
        path for entry in entries for path in _event_files(entry)
    )
    if not owners:
        return {}
    kept: dict[str, dict] = {}
    for entry in entries:
        found = {owners[path] for path in _event_files(entry) if path in owners}
        if len(found) == 1:
            title_id = found.pop()
            entry["title"] = title_id
            kept[title_id] = cards[title_id]
    return kept


def _whoami(signed_in: users.Account) -> dict:
    """The account shape /api/auth/me and a sign-in answer with."""
    return {
        "name": signed_in.name,
        "role": signed_in.role,
        "must_change": signed_in.must_change,
    }


class Handler(BaseHTTPRequestHandler):
    #: Keep-alive. The HTTP/1.0 default closes the socket after every answer,
    #: which made a 300-poster grid 300 TCP handshakes. Safe because every
    #: answer carries a Content-Length and no request leaves its body unread:
    #: a POST that cannot be read goes through _refuse, which closes.
    protocol_version = "HTTP/1.1"

    #: TCP_NODELAY. An answer is two writes, headers then body, and Nagle's
    #: algorithm holds the second until the first is acknowledged while the
    #: peer's delayed ACK waits too. About 40ms per answer: 300 posters on one
    #: connection took 12.3s without this and 116ms with it.
    disable_nagle_algorithm = True

    #: Seconds a connection may sit idle. The default is None, so an
    #: unauthenticated peer could hold a thread for ever by dribbling a
    #: request. Also how long an idle kept-alive connection holds a thread.
    timeout = 30

    def handle(self) -> None:
        """Serve the connection, treating a peer reset as routine.

        A browser closing a tab or leaving a loading grid resets kept-alive
        connections rather than closing them, and socketserver would print a
        traceback for each.
        """
        try:
            super().handle()
        except BrokenPipeError, ConnectionResetError:
            log.debug("http client went away")
            self.close_connection = True

    def _send_json(
        self,
        payload: dict,
        code: int = 200,
        cookie: str | None = None,
        *,
        tag: bool = False,
        close: bool = False,
    ) -> None:
        """One JSON answer, gzipped when the client accepts it.

        ``tag`` adds an ETag so a repeat request with the same body is a 304;
        worth it only for large, often-refetched answers like the library.
        ``close`` ends the connection with the answer; see :meth:`_refuse`.
        """
        body = json.dumps(payload).encode()
        # Hashed before compressing, and weak: the gzipped and plain bodies are
        # the same answer. Vary stops a shared cache serving gzip to a client
        # that did not ask.
        etag = f'W/"{hashlib.blake2s(body, digest_size=8).hexdigest()}"' if tag else None
        # No Connection: close needed here; _refuse never tags.
        if etag is not None and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", _FRESH_CACHE)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        sent = self._gzipped(body)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(sent)))
        self.send_header("Vary", "Accept-Encoding")
        if sent is not body:
            self.send_header("Content-Encoding", "gzip")
        if etag is not None:
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", _FRESH_CACHE)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(sent)

    def _gzipped(self, body: bytes) -> bytes:
        """The body gzipped, or as it was when that would not help.

        ``mtime=0`` keeps the output deterministic; the default stamps the
        clock into the header.
        """
        if len(body) < _GZIP_MIN:
            return body
        if not _accepts_gzip(self.headers.get("Accept-Encoding") or ""):
            return body
        return gzip.compress(body, _GZIP_LEVEL, mtime=0)

    def _reply(self, code: int, msg: str = "", cookie: str | None = None) -> None:
        self._send_json({"status": msg or "ok"}, code, cookie)

    def _refuse(self, code: int, msg: str) -> None:
        """Refuse a POST without reading its body, and close the socket.

        On a reused connection the unread body would be parsed as the next
        request's opening line.
        """
        self._send_json({"status": msg}, code, close=True)

    def _authorized(self) -> bool:
        """The machine-secret check: the webhook's, and the API's for reads."""
        return auth.authorized(self.headers.get(AUTH_HEADER) or "")

    def _session_token(self) -> str:
        """The session cookie's value. Parsed by hand; SimpleCookie trips on
        other cookies in the header."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name.strip() == SESSION_COOKIE:
                return value.strip()
        return ""

    def _session(self) -> users.Account | None:
        return sessions.get(self._session_token())

    def _cookie(self, token: str, max_age: int) -> str:
        """The Set-Cookie value for a session, or for clearing one.

        Secure is set only behind a proxy that says https: LAN deploys are
        plain http, where the flag would drop the cookie.
        """
        attrs = f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
        if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https":
            attrs += "; Secure"
        return attrs

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._reply(200, "healthy")
        elif parsed.path.startswith("/api/"):
            self._serve_api(parsed.path, parsed.query)
        else:
            self._serve_page(parsed.path)

    def _serve_api(self, path: str, query: str) -> None:
        """The API's reads, for a session of either role or a machine secret.

        A session that still owes a password change may only ask who it is:
        the bootstrap password sits in the docker log.
        """
        signed_in = self._session()
        if path == "/api/auth/me":
            if signed_in:
                self._send_json(_whoami(signed_in))
            else:
                self._reply(401, "unauthorized")
            return
        if not signed_in and not self._authorized():
            self._reply(401, "unauthorized")
            return
        if signed_in and signed_in.must_change:
            self._reply(403, "password change required")
            return
        if path == "/api/status":
            self._send_json({"version": __version__, **_queue_status()})
        elif path == "/api/runs":
            # Readable by a viewer; only the buttons are an admin's.
            self._send_json({**runs.snapshot(), **_queue_status()})
        elif path == "/api/runs/log":
            self._serve_run_log(query)
        elif path == "/api/settings":
            self._send_json(settings.snapshot())
        elif path == "/api/events":
            self._serve_events(query)
        elif path == "/api/library":
            # Tagged: the loader refetches this on every visit to the grid, and
            # an unchanged library then crosses the wire as a header.
            self._send_json(library.shelf(), tag=True)
            # Prefetch posters never seen before the grid asks for them, even
            # behind a 304.
            library.warm()
        elif path == "/api/library/summary":
            # The overview's strip: a tally and a dozen posters. No warm-up.
            order = urllib.parse.parse_qs(query).get("sort", [""])[0]
            self._send_json(library.summary(order=order), tag=True)
        elif path == "/api/library/title":
            self._serve_title(query)
        elif path == "/api/library/links":
            self._serve_links(query)
        elif path == "/api/library/cover":
            self._serve_cover(query)
        elif path == "/api/library/ratings":
            # One small file, so the sweep page need not load the shelf to count.
            self._send_json(ratings.summary())
        elif path == "/api/stream":
            self._serve_stream()
        else:
            self._reply(404, "not found")

    def _serve_stream(self) -> None:
        """Server-sent events: a ``data:`` line per kind :mod:`notify`
        publishes, and a heartbeat while nothing does.

        Frames carry no payload; the page refetches through the endpoints
        above, which keeps the ETags working. The one answer without a
        Content-Length, so the connection must close with it. X-Accel-Buffering
        asks nginx to pass each line on as written.
        """
        subscription = notify.subscribe()
        if subscription is None:
            # The page falls back to polling.
            self._reply(503, "too many open streams")
            return
        try:
            # Inside the try: a peer gone before the headers must still
            # unsubscribe, or one of MAX_STREAMS is lost until a restart.
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                kinds = subscription.take(_STREAM_HEARTBEAT)
                frames = "".join(f"data: {json.dumps({'kind': kind})}\n\n" for kind in kinds)
                # One write per wake, since Nagle is off.
                self.wfile.write(frames.encode() if frames else _PING)
        # A closed tab, a sleeping phone or a proxy giving up all surface as a
        # failed write. Wider than handle()'s pair: a peer that stopped reading
        # ends as a socket timeout, not a reset.
        except OSError:
            log.debug("stream client went away")
        finally:
            notify.unsubscribe(subscription)

    def _serve_run_log(self, query: str) -> None:
        """What one file's worker logged.

        Separate from the snapshot every tab polls; fetched for the one row
        somebody opened. Empty is normal for a cached verdict or for lines
        since dropped.
        """
        asked = urllib.parse.parse_qs(query)
        run = asked.get("run", [""])[0]
        path = asked.get("path", [""])[0]
        if not run or not path:
            self._reply(400, "run and path are required")
            return
        self._send_json({"run": run, "path": path, "lines": runs.lines(run, path)})

    def _serve_title(self, query: str) -> None:
        """One title's files, with what each is and what each would become."""
        wanted = urllib.parse.parse_qs(query).get("id", [""])[0]
        titles = library.selected([wanted]) if wanted else []
        found = library.title(wanted) if titles else None
        if found is None:
            self._reply(404, "no such title")
            return
        # Which services could offer a link is a settings read, so the sheet
        # draws its buttons at once. Resolving them means calling the media
        # servers, which is the second request.
        self._send_json({**found, "servers": links.offered(_subject(titles[0]))})

    def _serve_links(self, query: str) -> None:
        """Where a title lives in Plex, Jellyfin and its *arr, for the sheet's
        Open in buttons.

        Its own request because it calls each media server. One that is off,
        unreachable or has no such item contributes no button.
        """
        wanted = urllib.parse.parse_qs(query).get("id", [""])[0]
        found = library.selected([wanted]) if wanted else []
        if not found:
            self._reply(404, "no such title")
            return
        self._send_json({"links": links.for_title(_subject(found[0]))})

    def _serve_cover(self, query: str) -> None:
        """A title's poster, from our copy of the *arr's cache.

        Private and immutable in the browser: a grid asks for hundreds at once
        and a title's poster never changes. The ETag serves caches in between
        and the day the week runs out.
        """
        wanted = urllib.parse.parse_qs(query).get("id", [""])[0]
        art = library.cover(wanted) if wanted else None
        if art is None:
            # The grid draws its own tile. Cached briefly: the title is on
            # every reload.
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "private, max-age=600")
            self.end_headers()
            return
        body, content_type = art
        tag = f'"{hashlib.blake2s(body, digest_size=8).hexdigest()}"'
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", _COVER_CACHE)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", _COVER_CACHE)
        self.send_header("ETag", tag)
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self, query: str) -> None:
        """A page of the history, newest first, with the cursor for the next.

        ``before`` is a byte offset from a previous answer: the file is
        append-only, so an offset names the same line for ever and resuming
        costs no scan. ``next`` is null once the oldest event is handed over.
        ``since`` and ``until`` are ISO 8601 bounds on the window and page
        with ``before`` like the whole history does.
        """
        params = urllib.parse.parse_qs(query)
        try:
            limit = int(params.get("limit", ["100"])[0])
            before = int(params["before"][0]) if "before" in params else None
        except ValueError:
            self._reply(400, "limit and before must be whole numbers")
            return
        if not 1 <= limit <= events.MAX_READ or (before is not None and before < 0):
            self._reply(400, f"limit must be 1-{events.MAX_READ}, before cannot be negative")
            return
        try:
            since = events.moment(params["since"][0]) if "since" in params else None
            until = events.moment(params["until"][0]) if "until" in params else None
        except ValueError:
            self._reply(400, "since and until must be ISO 8601 moments")
            return
        # Crossed bounds are momentary while somebody types: answer empty,
        # not 400.
        if since is not None and until is not None and since > until:
            self._send_json({"events": [], "titles": {}, "next": None}, tag=True)
            return
        entries, cursor = events.read(limit, before, since, until)
        # Tagged like the shelf: the page refetches on every visit and the
        # lines rarely change. The cards let the feed draw a poster and raise
        # the library's title sheet.
        cards = _attach_titles(entries)
        self._send_json({"events": entries, "titles": cards, "next": cursor}, tag=True)

    def _serve_page(self, path: str) -> None:
        """A file from the built UI, or the shell for client-side routes.

        Served without a secret; everything the pages show comes through /api/.
        """
        file_path = _static_file(path)
        if not file_path:
            self._reply(404, "not found")
            return
        try:
            with open(file_path, "rb") as static:
                body = static.read()
        except OSError:
            self._reply(404, "not found")
            return
        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        # 330KB of bundles gzips to 124KB. Fonts and images are compressed
        # already.
        sent = self._gzipped(body) if _COMPRESSIBLE.match(content_type) else body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(sent)))
        self.send_header("Vary", "Accept-Encoding")
        # nosniff stops a bundle running as another type; DENY and the CSP's
        # frame-ancestors stop clickjacking; same-origin keeps paths out of
        # outbound referers.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", _CSP)
        if sent is not body:
            self.send_header("Content-Encoding", "gzip")
        if "/immutable/" in file_path:
            # SvelteKit hashes these names, so a change is a new URL.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            # The shell keeps its name across releases and names the hashed
            # bundles. A browser reusing a stored shell after an update loads
            # bundle URLs this image no longer has and gets a blank page, with
            # nothing running to notice. no-cache stores it but asks first.
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(sent)

    def _read_json(self) -> dict | None:
        """The request body as one JSON object, or None having answered."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._refuse(413, "body too large")
                return None
            body = json.loads(self.rfile.read(max(0, length)) or b"{}")
        # A bad Content-Length and a JSONDecodeError are both ValueErrors.
        # Refused, since the first leaves an unread body of unknown length.
        except ValueError:
            self._refuse(400, "bad json")
            return None
        if not isinstance(body, dict):
            self._reply(400, "bad json")
            return None
        return body

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self._serve_api_post(path)
            return
        if not self._authorized():
            log.warning("rejecting POST without a valid shared secret")
            self._refuse(401, "unauthorized")
            return
        if path != WEBHOOK_PATH:
            self._refuse(404, "not found")
            return
        body = self._read_json()
        if body is None:
            return

        if body.get("eventType") == "Test":
            log.info("received test webhook")
            self._reply(200, "test ok")
            return

        queued: list[str] = []
        run = events.run_id()
        jobs = jobs_from_hook(body, run)
        if jobs:
            # Opened before the first file and sealed after the last, so a
            # season import is one run rather than one per file.
            runs.open_run(
                run,
                runs.IMPORT,
                label=jobs[0].arr.name if jobs[0].arr else "",
                filling=True,
            )
        for job in jobs:
            # Usually a mount mismatch: the *arr and this container spell
            # the library differently.
            if not os.path.exists(job.path):
                log.warning("ignoring webhook path (does not exist): %s", job.path)
                continue
            if enqueue(job):
                queued.append(job.path)
                log.info("queued %s (original=%s)", job.path, job.lang or "unknown")
        if jobs:
            runs.seal(run)
        if queued:
            # Recorded first, so the run exists in the history before its
            # rewrites do.
            events.record(
                "webhook",
                run=run,
                arr=jobs[0].arr.name if jobs[0].arr else None,
                files=len(queued),
                # Named, not just counted: a delivery whose files all conform
                # records nothing else.
                paths=queued,
            )
        self._reply(200, f"queued {len(queued)}")

    def _serve_api_post(self, path: str) -> None:
        """The API's POSTs, for sessions only.

        Each demands a JSON content type, which a cross-site form cannot send;
        with SameSite that is the whole CSRF (cross-site request forgery)
        defence. Everything past the auth endpoints is admin-only, gated here
        so new endpoints inherit it.
        """
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._refuse(415, "expected application/json")
            return
        if path == "/api/auth/login":
            self._login()
            return
        signed_in = self._session()
        if not signed_in:
            self._refuse(401, "unauthorized")
        elif path == "/api/auth/logout":
            self._logout()
        elif path == "/api/auth/password":
            self._change_password(signed_in)
        elif signed_in.role != "admin" or signed_in.must_change:
            self._refuse(403, "forbidden")
        elif path == "/api/settings":
            self._update_settings(signed_in)
        elif path == "/api/connections/test":
            self._test_connection()
        elif path == "/api/sweep/check":
            self._check_sweep()
        elif path == "/api/runs/start":
            self._start_sweep(signed_in)
        elif path == "/api/runs/stop":
            self._stop_run()
        elif path == "/api/runs/pause":
            self._pause(signed_in, True)
        elif path == "/api/runs/resume":
            self._pause(signed_in, False)
        elif path == "/api/runs/abort":
            self._abort()
        elif path == "/api/library/run":
            self._recheck_titles(signed_in)
        elif path == "/api/library/clear":
            self._clear_library(signed_in)
        elif path == "/api/library/ratings":
            self._refresh_ratings(signed_in)
        else:
            self._refuse(404, "not found")

    def _refresh_ratings(self, signed_in: users.Account) -> None:
        """Fetch IMDb's ratings dataset now rather than at the next tick.

        For the settings page after the scores are switched on. Skips the
        loop's two intervals: this was asked for.
        """
        if self._read_json() is None:
            return
        if not config.IMDB_RATINGS:
            self._reply(409, "title scores are switched off")
            return
        wanted = library.imdb_ids()
        if wanted is None:
            self._reply(409, "an *arr could not be listed; its titles would lose their scores")
            return
        if not wanted:
            self._reply(409, "no title in the library carries an IMDb id")
            return
        try:
            ratings.refresh(wanted)
        except API_ERRORS as err:
            log.warning("imdb ratings: fetch from the web UI failed (%s)", err)
            self._reply(502, "could not fetch the dataset from IMDb")
            return
        # The shelf built for imdb_ids() predates the new table. See
        # ratings.refresh_pass.
        library.forget()
        log.info("imdb ratings fetched from the web UI by %s", signed_in.name)
        # "676 scored" only means something beside how many were looked for.
        self._send_json({**ratings.summary(), "titles": len(wanted)})

    def _recheck_titles(self, signed_in: users.Account) -> None:
        """Re-probe the chosen titles now, ignoring the cache.

        A sweep over picked titles: same modes, REWRITE_MODE latch, pause and
        one-walk-at-a-time rule, but every file is re-probed since the stored
        verdict is usually what is in question. The body names title ids,
        resolved against the library, so it can reach nothing else.
        """
        body = self._read_json()
        if body is None:
            return
        mode = str(body.get("mode") or "report")
        if mode not in ("report", "apply"):
            self._reply(400, "mode is report or apply")
            return
        wanted = body.get("ids")
        ids = [str(entry) for entry in wanted] if isinstance(wanted, list) else []
        if not ids:
            self._reply(400, "name the titles to run")
            return
        if runs.paused():
            self._reply(409, "processing is paused; resume it first")
            return
        if existing := runs.cache_holder():
            self._send_json(
                {"status": f"a {existing.kind} is already running", "run": existing.id}, 409
            )
            return
        chosen = library.selected(ids)
        if not chosen:
            self._reply(404, "no such title")
            return
        run = events.run_id()
        # The run card's label: one title by name, more by count.
        label = chosen[0].name if len(chosen) == 1 else f"{len(chosen)} titles"
        log.info(
            "re-check of %d title(s) started from the web UI by %s (mode=%s)",
            len(chosen),
            signed_in.name,
            mode,
        )
        threading.Thread(
            target=_recheck_thread,
            args=([title.folder for title in chosen], mode != "apply", run, label),
            daemon=True,
            name="recheck",
        ).start()
        self._send_json({"status": "started", "run": run, "titles": len(chosen)})

    def _clear_library(self, signed_in: users.Account) -> None:
        """Drop every stored verdict so the next sweep re-probes.

        Refused while a sweep or re-check runs: they hold the entries in memory
        and rewrite the file at each checkpoint, so a delete would be undone
        or would lose their work. Nothing else in STATE_DIR is touched.
        """
        # The body is unused but must leave the socket; the connection is
        # reused.
        if self._read_json() is None:
            return
        if existing := runs.cache_holder():
            self._send_json(
                {"status": f"a {existing.kind} is running; stop it first", "run": existing.id},
                409,
            )
            return
        dropped = library.clear()
        log.info("stored verdicts cleared from the web UI by %s", signed_in.name)
        self._send_json({"status": "cleared", "dropped": dropped})

    def _login(self) -> None:
        """Trade a name and password for a session cookie.

        Wrong name and wrong password answer alike, and an unknown name still
        costs one scrypt in verify(), so nothing reveals which accounts exist.
        The lock is checked first, or it would confirm a guess.
        """
        body = self._read_json()
        if body is None:
            return
        name = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if users.locked(name):
            self._reply(429, "too many attempts; wait a minute")
            return
        signed_in = users.verify(name, password) if name and password else None
        if not signed_in:
            if name:
                users.note_failure(name)
            log.warning("failed web sign-in for %r", name)
            self._reply(401, "wrong username or password")
            return
        users.note_success(name)
        try:
            token = sessions.create(signed_in)
        except OSError as err:
            log.error("could not store a session: %s", err)
            self._reply(500, "could not store the session")
            return
        self._send_json(_whoami(signed_in), cookie=self._cookie(token, sessions.TTL))

    def _logout(self) -> None:
        # The body is unused but must leave the socket; the connection is
        # reused.
        if self._read_json() is None:
            return
        try:
            sessions.revoke(self._session_token())
        except OSError as err:
            log.error("could not revoke a session: %s", err)
            self._reply(500, "could not revoke the session")
            return
        self._reply(200, "signed out", cookie=self._cookie("", 0))

    def _update_settings(self, signed_in: users.Account) -> None:
        """Write the body's NAME -> value changes to settings.json and apply
        them live.

        null unsets a name. A change that would refuse startup is rolled back
        whole and answered with the CLI's messages. The account name goes to
        the history.
        """
        body = self._read_json()
        if body is None:
            return
        try:
            problems = settings.update(body, by=signed_in.name)
        except OSError as err:
            log.error("could not write the settings: %s", err)
            self._reply(500, "could not write the settings")
            return
        if problems:
            self._send_json({"status": "invalid", "problems": problems}, 400)
            return
        log.info("settings updated: %s", ", ".join(sorted(body)))
        # A raised budget needs threads; a no-op when the pool already matches.
        start_workers()
        # The library memoises for minutes, and a corrected address must not
        # wait that long. Cost: one refetch.
        library.forget()
        # Likewise the media servers' answers about where titles live.
        links.forget()
        if not _ARR_CONNECTION.isdisjoint(body):
            # Off the request thread: two round trips per *arr.
            threading.Thread(target=_reregister, daemon=True, name="reregister").start()
        self._send_json(settings.snapshot())

    def _test_connection(self) -> None:
        """Ask one service whether it answers, using the page's unsaved values.

        A missing address or key falls back to the stored one, which is how an
        untouched password field travels. Admin-only matters here: the body
        names an address this container will fetch.
        """
        body = self._read_json()
        if body is None:
            return
        name = str(body.get("service") or "")
        if name not in connections.BY_NAME:
            self._reply(404, "no such service")
            return
        result = connections.check(name, str(body.get("url") or ""), str(body.get("key") or ""))
        self._send_json(asdict(result))

    def _check_sweep(self) -> None:
        """When a schedule would next fire, and whether its dirs exist.

        A POST so the admin gate covers it, since the body names paths this
        container stats. Checks the page's unsaved values.
        """
        body = self._read_json()
        if body is None:
            return
        dirs = body.get("dirs")
        result = sweep.check(
            str(body.get("at") or ""),
            [str(entry) for entry in dirs] if isinstance(dirs, list) else [],
            str(body.get("tz") or ""),
        )
        self._send_json(asdict(result))

    def _start_sweep(self, signed_in: users.Account) -> None:
        """Sweep now on a thread, answering with the run id.

        ``mode`` is report or apply, like the CLI's ``--apply``; REWRITE_MODE
        still latches over it. The id lets the page follow this sweep even
        when a delivery lands beside it.
        """
        body = self._read_json()
        if body is None:
            return
        mode = str(body.get("mode") or "report")
        if mode not in ("report", "apply"):
            self._reply(400, "mode is report or apply")
            return
        if runs.paused():
            self._reply(409, "processing is paused; resume it first")
            return
        if existing := runs.cache_holder():
            # Two walks would fight over the cache and pending.tsv. A re-check
            # counts as a walk.
            self._send_json(
                {"status": f"a {existing.kind} is already running", "run": existing.id}, 409
            )
            return
        run = events.run_id()
        log.info("sweep started from the web UI by %s (mode=%s)", signed_in.name, mode)
        threading.Thread(
            target=_sweep_thread, args=(run, mode != "apply"), daemon=True, name="sweep-now"
        ).start()
        self._send_json({"status": "started", "run": run})

    def _stop_run(self) -> None:
        """Ask a run to wind up after the file it is on.

        Not a kill: the sweep still writes its cache and its summary, and the
        rewrite in flight still publishes. :func:`_abort` is the one that
        takes the machine back immediately.
        """
        body = self._read_json()
        if body is None:
            return
        run = str(body.get("run") or "")
        if not run:
            self._reply(400, "name the run to stop")
            return
        if not runs.stop(run):
            # Almost always a page acting on a run that has since finished.
            self._reply(404, "no such run is going")
            return
        self._reply(200, "stopping")

    def _pause(self, signed_in: users.Account, on: bool) -> None:
        """Pause or resume the sweep and the import workers.

        Answers with the next snapshot so the button's state comes from the
        service, not a guess.
        """
        if self._read_json() is None:
            return
        (runs.pause if on else runs.resume)(signed_in.name)
        self._send_json({**runs.snapshot(), **_queue_status()})

    def _abort(self) -> None:
        """Stop every run and kill the rewrites in flight.

        Rewrites are staged and published only once verified, so this costs
        the encode and never the library file. Unlike :func:`_pause`, nothing
        stops the next delivery starting.
        """
        if self._read_json() is None:
            return
        # Runs first, or a killed rewrite's run would pick up the next file.
        # Keyed "stopped", not "runs": the page takes any answer with a `runs`
        # key for a snapshot.
        stopped = runs.stop_all()
        self._send_json({"status": "stopping", "stopped": stopped, "rewrites": runs.abort()})

    def _change_password(self, signed_in: users.Account) -> None:
        """Replace the caller's password after proving the current one.

        Every other session is revoked; the answer carries this browser's new
        one.
        """
        body = self._read_json()
        if body is None:
            return
        if len(str(body.get("new") or "")) < users.MIN_PASSWORD_LEN:
            self._reply(400, f"the new password needs {users.MIN_PASSWORD_LEN}+ characters")
            return
        if not users.verify(signed_in.name, str(body.get("current") or "")):
            self._reply(403, "wrong password")
            return
        changed = replace(signed_in, must_change=False)
        try:
            users.set_password(signed_in.name, str(body["new"]))
            sessions.revoke_user(signed_in.name)
            token = sessions.create(changed)
        # ValueError: the account vanished mid-request (a concurrent `user rm`).
        except (OSError, ValueError) as err:
            log.error("could not change the password: %s", err)
            self._reply(500, "could not change the password")
            return
        self._send_json(_whoami(changed), cookie=self._cookie(token, sessions.TTL))

    def log_message(self, fmt: str, *args) -> None:
        log.debug("http %s", fmt % args)


# No cover: a thread body around sweep(), which is covered directly.
def _sweep_thread(run: str, dry_run: bool) -> None:  # pragma: no cover
    """One sweep, off the request thread. The page follows it through /api/runs."""
    try:
        sweep.sweep(dry_run=dry_run, run=run)
    except Exception:
        log.exception("sweep started from the web UI failed")


# No cover: a thread body around recheck(), which is covered directly.
def _recheck_thread(  # pragma: no cover
    folders: list[str], dry_run: bool, run: str, label: str
) -> None:
    """One re-check, off the request thread. The page follows it through /api/runs."""
    try:
        sweep.recheck(folders, dry_run=dry_run, run=run, label=label)
    except Exception:
        log.exception("re-check started from the web UI failed")


#: Settings whose change re-registers the webhooks.
_ARR_CONNECTION = frozenset(
    {"RADARR_URL", "RADARR_API_KEY", "SONARR_URL", "SONARR_API_KEY", "WEBHOOK_URL"}
)


def _reregister() -> None:
    """One pass of webhook registration after the addresses change.

    No retry loop, unlike :func:`register_webhooks`: each save naming an
    unreachable *arr would leave another loop polling for ever.
    """
    for arr in all_arrs():
        if arr.enabled and not arr.register_webhook(webhook_url()):
            log.warning("%s: no webhook connection after the settings change", arr.name)


#: Registration retry delays. The containers usually start together, so early
#: attempts land before the *arrs answer; the cap protects an absent one.
_REGISTER_RETRY_START = 15
_REGISTER_RETRY_CAP = 300


# No cover: a retry loop. Arr.register_webhook does the work and is covered.
def register_webhooks() -> None:  # pragma: no cover
    """Retry until every enabled *arr has the webhook."""
    pending = [arr for arr in all_arrs() if arr.enabled]
    delay = _REGISTER_RETRY_START
    while pending:
        pending = [arr for arr in pending if not arr.register_webhook(webhook_url())]
        if pending:
            time.sleep(delay)
            delay = min(delay * 2, _REGISTER_RETRY_CAP)
