"""The webhook side: the listener, the work queue, and hardlink parking.

Radarr and Sonarr fire per imported file. The HTTP handler only parses and
queues; a single worker thread does the probing and rewriting, so a slow or
restarting *arr can never stall the webhook response. The credentials
callers present live in :mod:`trackstarr.auth`.
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
from .status import Status

log = logging.getLogger(__name__)


def hardlinked(path: str) -> bool:
    """More than one directory entry shares the file's inode.

    In an *arr setup that means the download client is still seeding it. An
    unreadable file counts as not hardlinked; the probe will report it.
    """
    try:
        return os.stat(path).st_nlink > 1
    except OSError:
        return False


_work_q: queue.Queue[Job] = queue.Queue()

#: Imports arrive in bursts and the *arrs fire per file, so the same path can
#: be queued twice before the first job runs. The planner would no-op the
#: second time anyway; this just avoids the wasted probe.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()

#: Webhook jobs whose file the download client still hard-links. The import
#: webhook is the last event the *arr stack ever fires for these, so a timer
#: re-stats them until the link count says the file is safe to rewrite. The
#: set is in memory only; the nightly sweep is the backstop after a restart.
_parked: dict[str, Job] = {}
_parked_lock = threading.Lock()


def _resolve_lang(job: Job) -> Job:
    """Fetch the original language from the *arr when the webhook body lacked it.

    Older Radarr and Sonarr versions don't carry ``originalLanguage`` in the
    webhook. Resolved here on the worker rather than in the HTTP handler, so
    a slow or restarting *arr can never stall the webhook response.
    """
    if job.lang is not None or not job.arr or not job.item_id:
        return job
    return replace(job, lang=original_of(job.arr.item(job.item_id)))


def parking_enabled() -> bool:
    return config.SKIP_HARDLINKS and config.HARDLINK_RECHECK > 0


def _park(job: Job) -> None:
    with _parked_lock:
        _parked[job.path] = job
    log.info("parked %s until the download client releases it", job.path)


def _handle(job: Job) -> None:
    """Process a webhook job, parking it while the file is still seeded."""
    job = _resolve_lang(job)
    if parking_enabled() and hardlinked(job.path):
        _park(job)
        return
    result = process(job, dry_run=False)
    if result.status is Status.WOULD_FIX and result.plan:
        # Only the DRY_RUN latch turns this real request into a would-fix.
        # Unlike a sweep there is no pending.tsv row or summary event, so
        # the history is the only record of what was declined.
        events.record(
            "would-fix",
            source="webhook",
            path=job.path,
            reasons=result.plan.reasons,
            incidental=result.plan.incidental,
            downmixed=downmixed_names(result.plan) or None,
        )


# No cover: a thread body. It blocks on the queue for ever, and _handle,
# which is the part with decisions in it, is covered directly.
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
    for job in parked:
        if hardlinked(job.path):
            continue
        with _parked_lock:
            _parked.pop(job.path, None)
        if not os.path.exists(job.path):
            # Upgraded or deleted; the successor has its own webhook.
            log.info("parked file disappeared, dropping %s", job.path)
        elif enqueue(job):
            log.info("hard link released, queued %s", job.path)


# No cover: a thread body around _recheck_parked, which is covered directly.
def parked_recheck_loop() -> None:  # pragma: no cover
    while True:
        time.sleep(config.HARDLINK_RECHECK)
        try:
            _recheck_parked()
        except Exception:
            log.exception("parked recheck failed")


#: Deliberately wider than the events the webhook registration subscribes to:
#: a manually configured connection can also fire Rename, and older Radarr
#: sends MovieFileImported.
_ACTIONABLE_EVENTS = frozenset({"Download", "Rename", "MovieFileImported"})


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

    An empty relativePath must not fall through to the folder itself: that
    joins to a directory, which would then be queued as though it were a file.
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


#: A full-season Sonarr import is tens of KB; anything past this is not a
#: webhook.
_MAX_BODY = 8 << 20


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, msg: str = "") -> None:
        payload = json.dumps({"status": msg or "ok"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if urllib.parse.urlparse(self.path).path in ("/health", "/ping"):
            self._reply(200, "healthy")
        else:
            self._reply(404, "not found")

    def do_POST(self) -> None:
        if not auth.authorized(self.headers.get(AUTH_HEADER) or ""):
            log.warning("rejecting POST without a valid shared secret")
            self._reply(401, "unauthorized")
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
            # Usually the *arr and this container spelling the library
            # differently, i.e. a mount mismatch.
            if not os.path.exists(job.path):
                log.warning("ignoring webhook path (does not exist): %s", job.path)
                continue
            if enqueue(job):
                queued += 1
                log.info("queued %s (original=%s)", job.path, job.lang or "unknown")
        self._reply(200, f"queued {queued}")

    def log_message(self, fmt: str, *args) -> None:
        log.debug("http %s", fmt % args)


# No cover: retries until every *arr answers, sleeping between rounds.
# Arr.register_webhook, which does the work, is covered directly.
def register_webhooks() -> None:  # pragma: no cover
    """Keep at it until every enabled *arr has the connection.

    The containers usually start together, so the first attempts can land
    before Radarr or Sonarr is answering.
    """
    pending = [arr for arr in all_arrs() if arr.enabled]
    while pending:
        pending = [arr for arr in pending if not arr.register_webhook(config.WEBHOOK_URL)]
        if pending:
            time.sleep(300)
