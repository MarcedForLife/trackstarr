"""The webhook side: the listener, the work queue, and hardlink parking.

Radarr and Sonarr fire once per imported file. The handler parses and
queues, nothing more; worker threads do the probing and rewriting, so a slow
*arr can never stall the response. Caller credentials live in
:mod:`trackstarr.auth`.
"""

import json
import logging
import os
import queue
import threading
import time
import urllib.parse
from dataclasses import replace
from http.server import BaseHTTPRequestHandler

from . import auth, config, events
from .arr import AUTH_HEADER, all_arrs, original_of
from .processing import Job, downmixed_names, process
from .state import write_json
from .status import Status

log = logging.getLogger(__name__)

#: The one path POSTs are accepted on. Everything else 404s, so the rest of
#: the namespace stays free for a future API instead of every path being the
#: webhook forever.
WEBHOOK_PATH = "/webhook"


def webhook_url() -> str:
    """The URL the *arrs are registered to call: WEBHOOK_URL plus the path."""
    return config.WEBHOOK_URL + WEBHOOK_PATH


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

#: Webhook jobs whose file the download client still hard-links, re-statted
#: on a timer until the link count drops: the import is the last event the
#: *arr stack fires for these. Kept on disk too, since nothing re-fires an
#: import and a restart mid-seed would otherwise strand them.
_parked: dict[str, Job] = {}
_parked_lock = threading.Lock()

#: Where the parked set lives between runs, beside the rest of STATE_DIR.
PARKED_FILE = "parked.json"


def _parked_path() -> str:
    return os.path.join(config.STATE_DIR, PARKED_FILE)


def _save_parked() -> None:
    """Write the parked set out atomically. Never raises.

    The lock covers the write, not just the snapshot: two snapshots racing to
    the same name can leave the older on top, losing an entry nothing would
    ever queue again.
    """
    with _parked_lock:
        records = [
            {
                "path": job.path,
                "lang": job.lang,
                "item_id": job.item_id,
                "arr": job.arr.name if job.arr else None,
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

    Only called when parking is on; with SKIP_HARDLINKS off the file waits
    rather than filling a set no thread drains. Anything unreadable is
    dropped, costing that file a wait for the next sweep.
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
            # "" for a job that was matched to no *arr, which no name is.
            arrs.get(record.get("arr") or ""),
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

    Older Radarr and Sonarr don't send ``originalLanguage``, so it is looked
    up here, on the worker rather than in the HTTP handler.
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
    job = _resolve_lang(job)
    if parking_enabled() and hardlinked(job.path):
        _park(job)
        return
    result = process(job, dry_run=False)
    if result.status is Status.WOULD_FIX and result.plan:
        # Only DRY_RUN turns a real request into a would-fix, and there is no
        # pending.tsv row here, so the history is the only record.
        events.record(
            "would-fix",
            source="webhook",
            config_id=result.plan.policy.digest(),
            path=job.path,
            reasons=result.plan.reasons,
            rules=sorted(result.plan.rules),
            incidental=result.plan.incidental,
            incidental_rules=sorted(result.plan.incidental_rules),
            downmixed=downmixed_names(result.plan) or None,
        )


# No cover: a thread body, blocking on the queue for ever. _handle has the
# decisions in it and is covered directly.
def worker() -> None:  # pragma: no cover
    while True:
        job = _work_q.get()
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
    _work_q.put(job)
    return True


def _recheck_parked() -> None:
    """Queue parked jobs whose extra hard links have gone."""
    with _parked_lock:
        parked = list(_parked.values())
    released = False
    for job in parked:
        if hardlinked(job.path):
            continue
        with _parked_lock:
            _parked.pop(job.path, None)
        released = True
        if not os.path.exists(job.path):
            # Upgraded or deleted; the successor has its own webhook.
            log.info("parked file disappeared, dropping %s", job.path)
        elif enqueue(job):
            log.info("hard link released, queued %s", job.path)
    # Once per pass: a backlog releases together, and the set is one write.
    if released:
        _save_parked()


# No cover: a thread body around _recheck_parked, which is covered directly.
def parked_recheck_loop() -> None:  # pragma: no cover
    while True:
        time.sleep(config.HARDLINK_RECHECK)
        try:
            _recheck_parked()
        except Exception:
            log.exception("parked recheck failed")


#: Download, plus the name older Radarr sends for it. Rename is left out on
#: purpose: its body carries files only under renamed*Files keys, and a
#: rename changes no track content.
_ACTIONABLE_EVENTS = frozenset({"Download", "MovieFileImported"})


def jobs_from_hook(body: dict) -> list[Job]:
    """Pull the files to process out of a webhook body."""
    if body.get("eventType") not in _ACTIONABLE_EVENTS:
        return []
    for arr in all_arrs():
        item = body.get(arr.body_key)
        if item is None:
            continue
        lang = original_of(item)
        folder = item.get(arr.folder_key) or ""
        files = [body[arr.file_key]] if arr.file_key in body else body.get(arr.files_key, [])
        return [Job(path, lang, item.get("id"), arr) for path in _paths(files, folder)]
    return []


def _paths(files: list[dict], folder: str) -> list[str]:
    """Absolute path per file, preferring the one the *arr gave us.

    An empty relativePath must not fall through to the folder, which would
    queue a directory as though it were a file.
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


class Handler(BaseHTTPRequestHandler):
    #: Seconds a connection may go quiet before it is dropped. Python leaves
    #: this None, so a peer could hold a server thread for ever by dribbling
    #: a request, without ever authenticating: the secret is checked after
    #: the headers are read.
    timeout = 30

    def _reply(self, code: int, msg: str = "") -> None:
        payload = json.dumps({"status": msg or "ok"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if urllib.parse.urlparse(self.path).path == "/health":
            self._reply(200, "healthy")
        else:
            self._reply(404, "not found")

    def do_POST(self) -> None:
        if not auth.authorized(self.headers.get(AUTH_HEADER) or ""):
            log.warning("rejecting POST without a valid shared secret")
            self._reply(401, "unauthorized")
            return
        if urllib.parse.urlparse(self.path).path != WEBHOOK_PATH:
            self._reply(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._reply(413, "body too large")
                return
            body = json.loads(self.rfile.read(max(0, length)) or b"{}")
        # A bad Content-Length and a JSONDecodeError are both ValueErrors.
        except ValueError:
            self._reply(400, "bad json")
            return

        if body.get("eventType") == "Test":
            log.info("received test webhook")
            self._reply(200, "test ok")
            return

        queued = 0
        for job in jobs_from_hook(body):
            # Usually a mount mismatch: the *arr and this container spell
            # the library differently.
            if not os.path.exists(job.path):
                log.warning("ignoring webhook path (does not exist): %s", job.path)
                continue
            if enqueue(job):
                queued += 1
                log.info("queued %s (original=%s)", job.path, job.lang or "unknown")
        self._reply(200, f"queued {queued}")

    def log_message(self, fmt: str, *args) -> None:
        log.debug("http %s", fmt % args)


#: Registration retry delays: the containers usually start together, so the
#: first attempts land before Radarr or Sonarr is answering. Short early
#: retries catch them coming up seconds later; the cap keeps an absent one
#: from being polled hard for ever.
_REGISTER_RETRY_START = 15
_REGISTER_RETRY_CAP = 300


# No cover: retries until every *arr answers, sleeping between rounds.
# Arr.register_webhook does the work and is covered directly.
def register_webhooks() -> None:  # pragma: no cover
    """Keep at it until every enabled *arr has the connection."""
    pending = [arr for arr in all_arrs() if arr.enabled]
    delay = _REGISTER_RETRY_START
    while pending:
        pending = [arr for arr in pending if not arr.register_webhook(webhook_url())]
        if pending:
            time.sleep(delay)
            delay = min(delay * 2, _REGISTER_RETRY_CAP)
