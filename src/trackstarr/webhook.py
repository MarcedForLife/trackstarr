"""The HTTP listener: the socket, the webhook intake, and how an answer is
written.

Radarr and Sonarr (the *arrs) post once per imported file. The handler parses
and queues; :mod:`trackstarr.jobs` holds the queue and the workers that probe
and rewrite, so a slow *arr never stalls the response. A file they report
removed or renamed has its stored verdict dropped or moved at once, rather
than at the next full sweep. Machine secrets are checked by
:mod:`trackstarr.auth`.

Everything else a request can reach hangs off :meth:`Handler.do_GET` and
:meth:`Handler.do_POST`: :mod:`trackstarr.api` for /api/, and
:mod:`trackstarr.assets` for the built pages. Both answer through the handler's
own :meth:`Handler.send_json` and friends, which is why those are public.

Browsers use a session cookie from /api/auth/login (:mod:`trackstarr.users`,
:mod:`trackstarr.sessions`). A webhook secret is accepted for reads only, so a
leaked *arr credential can watch the queue but not change settings.
"""

import hashlib
import json
import logging
import os
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler

from . import api, assets, auth, events, jobs, lifecycle, runs, sessions, sweep, users
from .arr import AUTH_HEADER, WEBHOOK_PATH, Arr, all_arrs, original_of
from .processing import Job

log = logging.getLogger(__name__)

#: The session cookie's name; its value is a :func:`trackstarr.sessions.create` token.
SESSION_COOKIE = "trackstarr_session"

#: Stored but revalidated every time. The library changes whenever a sweep does
#: and the loader refetches it on every visit; an ETag check is a header where
#: the body is hundreds of kilobytes.
_FRESH_CACHE = "private, no-cache"

#: Download, plus older Radarr's name for it.
_ACTIONABLE_EVENTS = frozenset({"Download", "MovieFileImported"})

#: Files moved with their tracks untouched, so their verdicts move too.
_RENAME_EVENT = "Rename"

#: A file the *arr deleted, by hand or under an upgrade. The body names it
#: under the same key an import does.
_FILE_REMOVAL_EVENTS = frozenset({"MovieFileDelete", "EpisodeFileDelete"})

#: A title deleted from the *arr. ``deletedFiles`` is a bool here: whether
#: its folder went with it.
_TITLE_REMOVAL_EVENTS = frozenset({"MovieDelete", "SeriesDelete"})


def _delivery(body: dict, caller: str) -> tuple[Arr, dict] | None:
    """The *arr a body came from and its movie or series object, or None
    where no configured *arr's key is in the body.

    ``caller`` is the instance whose secret signed the request; a shared
    secret leaves it empty and the body's keys have to tell. Raises
    ValueError for a disabled caller or a body two instances could own.
    """
    arrs = all_arrs()
    identified = next((arr for arr in arrs if arr.instance_id == caller), None)
    if (caller in ("radarr", "sonarr") or caller.startswith(("radarr-", "sonarr-"))) and (
        identified is None or not identified.enabled
    ):
        raise ValueError("this arr instance is no longer enabled")
    candidates = (
        [identified]
        if identified
        else [arr for arr in arrs if body.get(arr.body_key) is not None]
    )
    if not identified and len(candidates) > 1:
        enabled = [arr for arr in candidates if arr.enabled]
        if len(enabled) != 1:
            raise ValueError("ambiguous arr instance; use its registered webhook secret")
        candidates = enabled
    for arr in candidates:
        item = body.get(arr.body_key)
        if item is not None:
            return arr, item
    return None


def jobs_from_hook(body: dict, run: str | None = None, caller: str = "") -> list[Job]:
    """The files a webhook body names, each as a job tagged with ``run``."""
    if body.get("eventType") not in _ACTIONABLE_EVENTS:
        return []
    if (found := _delivery(body, caller)) is None:
        return []
    arr, item = found
    lang = original_of(item)
    folder = item.get(arr.folder_key) or ""
    files = [body[arr.file_key]] if arr.file_key in body else body.get(arr.files_key, [])
    return [Job(path, lang, item.get("id"), arr, run) for path in _paths(files, folder)]


@dataclass(frozen=True)
class Removed:
    """What a webhook body says the *arr took away: files by path, or the
    folder of a title deleted along with its files."""

    paths: tuple[str, ...] = ()
    folder: str = ""


def removed_from_hook(body: dict, caller: str = "") -> Removed | None:
    """The files a body says the *arr removed, or None where it removed nothing.

    An upgrade's Download lists what it replaced under ``deletedFiles``. A
    file deleted on its own arrives as its own event, under the import's key.
    A title deleted with its files names only its folder.
    """
    event = body.get("eventType")
    if event not in _ACTIONABLE_EVENTS | _FILE_REMOVAL_EVENTS | _TITLE_REMOVAL_EVENTS:
        return None
    if (found := _delivery(body, caller)) is None:
        return None
    arr, item = found
    folder = item.get(arr.folder_key) or ""
    if event in _TITLE_REMOVAL_EVENTS:
        gone = body.get("deletedFiles") is True and bool(folder)
        return Removed(folder=folder) if gone else None
    if event in _ACTIONABLE_EVENTS:
        files = body.get("deletedFiles")
    else:
        files = [body.get(arr.file_key)]
    if not isinstance(files, list):
        return None
    paths = _paths([entry for entry in files if isinstance(entry, dict)], folder)
    return Removed(tuple(paths)) if paths else None


def renamed_from_hook(body: dict, caller: str = "") -> list[tuple[str, str]]:
    """Each file a Rename moved, as (old path, new path)."""
    if body.get("eventType") != _RENAME_EVENT or (found := _delivery(body, caller)) is None:
        return []
    arr, item = found
    folder = item.get(arr.folder_key) or ""
    moves: list[tuple[str, str]] = []
    for entry in body.get(arr.renamed_files_key) or []:
        if not isinstance(entry, dict):
            continue
        previous = _path(entry, folder, "previousPath", "previousRelativePath")
        path = _path(entry, folder)
        if previous and path and previous != path:
            moves.append((previous, path))
    return moves


def _path(
    file_info: dict, folder: str, path_key: str = "path", rel_key: str = "relativePath"
) -> str:
    """The absolute path a file object names, preferring the one the *arr gave,
    or "" for none. An empty relative path must not fall through to the folder,
    or a directory would be queued as a file."""
    if path := file_info.get(path_key):
        return path
    rel = file_info.get(rel_key) or ""
    return os.path.join(folder, rel) if folder and rel else ""


def _paths(files: list[dict], folder: str) -> list[str]:
    return [path for file_info in files if (path := _path(file_info, folder))]


#: A full-season Sonarr import is tens of KB. Past this it is not a webhook.
_MAX_BODY = 8 << 20


class Handler(BaseHTTPRequestHandler):
    #: Keep-alive. The HTTP/1.0 default closes the socket after every answer,
    #: which made a 300-poster grid 300 TCP handshakes. Safe because every
    #: answer carries a Content-Length and no request leaves its body unread:
    #: a POST that cannot be read goes through refuse(), which closes.
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

    def send_json(
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
        ``close`` ends the connection with the answer; see :meth:`refuse`.
        """
        body = json.dumps(payload).encode()
        # Hashed before compressing, and weak: the gzipped and plain bodies are
        # the same answer. Vary stops a shared cache serving gzip to a client
        # that did not ask.
        etag = f'W/"{hashlib.blake2s(body, digest_size=8).hexdigest()}"' if tag else None
        # No Connection: close needed here; refuse() never tags.
        if etag is not None and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", _FRESH_CACHE)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        sent = assets.gzipped(body, self.headers.get("Accept-Encoding") or "")
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

    def reply(self, code: int, msg: str = "", cookie: str | None = None) -> None:
        self.send_json({"status": msg or "ok"}, code, cookie)

    def refuse(self, code: int, msg: str) -> None:
        """Refuse a POST without reading its body, and close the socket.

        On a reused connection the unread body would be parsed as the next
        request's opening line.
        """
        self.send_json({"status": msg}, code, close=True)

    def authorized(self) -> bool:
        """The machine-secret check: the webhook's, and the API's for reads."""
        return auth.authorized(self.headers.get(AUTH_HEADER) or "")

    def session_token(self) -> str:
        """The session cookie's value. Parsed by hand; SimpleCookie trips on
        other cookies in the header."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name.strip() == SESSION_COOKIE:
                return value.strip()
        return ""

    def session(self) -> users.Account | None:
        return sessions.get(self.session_token())

    def cookie(self, token: str, max_age: int) -> str:
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
            self.reply(200, "healthy")
        elif parsed.path.startswith("/api/"):
            api.serve_get(self, parsed.path, parsed.query)
        else:
            assets.serve_page(self, parsed.path)

    def read_json(self) -> dict | None:
        """The request body as one JSON object, or None having answered."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self.refuse(413, "body too large")
                return None
            body = json.loads(self.rfile.read(max(0, length)) or b"{}")
        # A bad Content-Length and a JSONDecodeError are both ValueErrors.
        # Refused, since the first leaves an unread body of unknown length.
        except ValueError:
            self.refuse(400, "bad json")
            return None
        if not isinstance(body, dict):
            self.reply(400, "bad json")
            return None
        return body

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            api.serve_post(self, path)
            return
        if not self.authorized():
            log.warning("rejecting POST without a valid shared secret")
            self.refuse(401, "unauthorized")
            return
        if path != WEBHOOK_PATH:
            self.refuse(404, "not found")
            return
        body = self.read_json()
        if body is None:
            return

        if body.get("eventType") == "Test":
            log.info("received test webhook")
            self.reply(200, "test ok")
            return

        with lifecycle.producer() as allowed:
            if not allowed:
                self.reply(503, "the service is stopping")
                return
            self._admit(body)

    def _admit(self, body: dict) -> None:
        """Queue a delivery's files under one run, drop the verdicts of files
        it says have gone, and answer with the counts."""
        queued: list[str] = []
        run = events.run_id()
        secret = self.headers.get(AUTH_HEADER) or ""
        caller = next((name for name in auth.names() if auth.matches(name, secret)), "")
        try:
            delivered = jobs_from_hook(body, run, caller)
            removed = removed_from_hook(body, caller)
            renamed = renamed_from_hook(body, caller)
        except ValueError as err:
            self.reply(400, str(err))
            return
        if delivered:
            # Opened before the first file and sealed after the last, so a
            # season import is one run rather than one per file.
            lifecycle.open_run(
                run,
                runs.IMPORT,
                instance_id=delivered[0].instance_id,
                filling=True,
            )
        for job in delivered:
            # Usually a mount mismatch: the *arr and this container spell
            # the library differently.
            if not os.path.exists(job.path):
                log.warning("ignoring webhook path (does not exist): %s", job.path)
                continue
            if jobs.enqueue(job):
                queued.append(job.path)
                log.info("queued %s (original=%s)", job.path, job.lang or "unknown")
        if delivered:
            lifecycle.seal(run)
        if queued:
            # Recorded first, so the run exists in the history before its
            # rewrites do.
            events.record(
                "webhook",
                run=run,
                arr=delivered[0].arr.instance_id if delivered[0].arr else None,
                files=len(queued),
                # Named, not just counted: a delivery whose files all conform
                # records nothing else.
                paths=queued,
            )
        answer = f"queued {len(queued)}"
        if removed is not None:
            # Or a replaced file's verdict stands beside the new file's until
            # a full sweep, as a second variant of the title.
            dropped = sweep.remove(removed.paths, (removed.folder,) if removed.folder else ())
            for path in dropped:
                log.info("dropped the stored verdict of %s, removed by the *arr", path)
            answer += f", dropped {len(dropped)}"
        if renamed:
            moved = sweep.relocate(renamed)
            for path in moved:
                log.info("relocated a stored verdict to %s, renamed by the *arr", path)
            answer += f", moved {len(moved)}"
        self.reply(200, answer)

    def log_message(self, fmt: str, *args) -> None:
        log.debug("http %s", fmt % args)
