"""Processing, the library sweep, and the webhook listener."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .arr import Arr, all_arrs, match_path, original_of, path_index
from .executor import Outcome, apply_plan, clean_work_dir
from .media import ProbeError
from .media_server import refresh_servers, server_status
from .planner import Plan, allowed_container, build_plan, describe, hardlinked
from .sweep_cache import SweepCache, Verdict, cache_key

log = logging.getLogger(__name__)

#: One rewrite at a time across the whole process. The webhook worker and the
#: nightly sweep can otherwise overlap, and two ffmpeg jobs competing for the
#: same spinning disk take longer than running them back to back.
_rewrite_lock = threading.Lock()


@dataclass(frozen=True)
class Job:
    """One file to process, with what the *arrs know about it."""

    path: str
    lang: str | None = None
    item_id: int | None = None
    arr: Arr | None = None


#: Everything process() can report. The sweep's counts are keyed by these.
STATUSES = ("skip", "conform", "would-fix", "fixed", "deferred", "failed")

#: The statuses worth a row in pending.tsv.
REPORTED_STATUSES = ("would-fix", "fixed", "deferred", "failed")

#: Verdicts that leave the file untouched, safe for the sweep cache. Fixed
#: changes the file, and failures may be transient.
CACHEABLE_STATUSES = ("skip", "conform", "would-fix")


@dataclass(frozen=True)
class ProcessResult:
    status: str
    plan: Plan | None = None
    #: Why the rewrite didn't happen, for deferred and failed.
    detail: str = ""


def process(job: Job, dry_run: bool) -> ProcessResult:
    """Plan one file and, unless dry_run, rewrite it. Statuses: STATUSES."""
    try:
        plan = build_plan(job.path, job.lang)
    except ProbeError as err:
        log.warning("probe failed for %s: %s", job.path, err)
        return ProcessResult("failed", detail=str(err))

    if plan.skip:
        log.debug("skip %s: %s", job.path, plan.skip)
        return ProcessResult("skip", plan)
    if not plan.needed:
        return ProcessResult("conform", plan)
    if dry_run:
        log.info("would fix %s: %s", job.path, describe(plan))
        return ProcessResult("would-fix", plan)

    log.info("fixing %s: %s", job.path, describe(plan))
    with _rewrite_lock:
        try:
            outcome, detail = apply_plan(plan)
        # Everything apply_plan can raise: the verify probe on a corrupt
        # result, a source deleted mid-job, WORK_DIR gone. One file failing
        # must not take the rest of a sweep down with it.
        except (ProbeError, OSError) as err:
            outcome, detail = Outcome.FAILED, str(err)
    if outcome is Outcome.APPLIED:
        if job.arr and job.item_id:
            job.arr.rescan(job.item_id)
        # out_path, not job.path: a remux publishes under a new extension.
        refresh_servers(plan.out_path)
        return ProcessResult("fixed", plan)
    if outcome is Outcome.DEFERRED:
        log.info("deferred %s: %s", job.path, detail)
        return ProcessResult("deferred", plan, detail)
    log.warning("rewrite of %s failed: %s", job.path, detail)
    return ProcessResult("failed", plan, detail)


def walk_library() -> list[str]:
    found: list[str] = []
    for root in config.MEDIA_ROOTS:
        if not os.path.isdir(root):
            # os.walk would yield nothing, making a wrong mount or a typo in
            # MEDIA_ROOTS indistinguishable from an empty library.
            log.warning("media root %s does not exist", root)
            continue
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [child for child in dirnames if not child.startswith(".")]
            found.extend(
                os.path.join(dirpath, name) for name in names if allowed_container(name)
            )
    return found


#: Longest pending.tsv cell; a 30-track plan's reasons get cut, not the row.
_CELL_MAX = 400


def _cell(text: str) -> str:
    """One pending.tsv cell: tabs and newlines collapsed, bounded."""
    return " ".join(text.split())[:_CELL_MAX]


def sweep(dry_run: bool) -> dict[str, int]:
    idx = path_index(all_arrs())
    files = walk_library()
    log.info("sweep starting: %d files, dry_run=%s", len(files), dry_run)

    counts = dict.fromkeys(STATUSES, 0)
    cached_hits = 0
    os.makedirs(config.STATE_DIR, exist_ok=True)
    report = os.path.join(config.STATE_DIR, "pending.tsv")
    cache = SweepCache.load(os.path.join(config.STATE_DIR, "sweep-cache.json"))

    with open(report, "w") as report_file:
        report_file.write("status\toriginal_lang\tpath\treasons\tdetail\n")
        for i, path in enumerate(files, 1):
            matched = match_path(idx, path)
            job = (
                Job(path, matched.lang, matched.item_id, matched.arr) if matched else Job(path)
            )
            key = cache_key(path, job.lang)

            verdict = cache.lookup(path, key)
            # A cached would-fix only stands in for the probe while reporting;
            # an applying sweep must rewrite the file.
            if verdict and (dry_run or verdict.status != "would-fix"):
                cache.record(path, key, verdict)
                cached_hits += 1
                status, reasons, detail = verdict.status, verdict.reasons, ""
            else:
                result = process(job, dry_run)
                status, detail = result.status, result.detail
                reasons = describe(result.plan) if result.plan else ""
                if status in CACHEABLE_STATUSES:
                    cache.record(path, key, Verdict(status, reasons))

            counts[status] += 1
            if status in REPORTED_STATUSES:
                report_file.write(
                    f"{status}\t{job.lang or '-'}\t{path}\t{_cell(reasons)}\t{_cell(detail)}\n"
                )
                report_file.flush()
            if i % 500 == 0:
                log.info("  %d/%d ... %s", i, len(files), counts)
                cache.checkpoint()

    cache.save()
    log.info("sweep done: %s (%d verdicts from cache)", counts, cached_hits)
    log.info("report: %s", report)
    return counts


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


def _parking_enabled() -> bool:
    return config.SKIP_HARDLINKS and config.HARDLINK_RECHECK > 0


def _park(job: Job) -> None:
    with _parked_lock:
        _parked[job.path] = job
    log.info("parked %s until the download client releases it", job.path)


def _handle(job: Job) -> None:
    """Process a webhook job, parking it while the file is still seeded."""
    job = _resolve_lang(job)
    if _parking_enabled() and hardlinked(job.path):
        _park(job)
        return
    process(job, dry_run=False)


def worker() -> None:
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


def parked_recheck_loop() -> None:
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
#: webhook, and the listener has no auth to hide behind.
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
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._reply(413, "body too large")
                return
            body = json.loads(self.rfile.read(max(0, length)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, "bad json")
            return

        if body.get("eventType") == "Test":
            log.info("received test webhook")
            self._reply(200, "test ok")
            return

        queued = 0
        for job in jobs_from_hook(body):
            if not os.path.exists(job.path):
                log.warning("webhook path does not exist: %s", job.path)
                continue
            if enqueue(job):
                queued += 1
                log.info("queued %s (original=%s)", job.path, job.lang or "unknown")
        # Answer immediately; the *arr should never wait on ffmpeg.
        self._reply(200, f"queued {queued}")

    def log_message(self, fmt: str, *args) -> None:
        log.debug("http %s", fmt % args)


#: A target closer than this counts as just fired and rolls to tomorrow, so
#: the scheduler rescheduling right after a sweep never picks the same slot.
_MIN_LEAD_SECONDS = 30


def seconds_until(hhmm: str, now: float | None = None) -> float:
    """Seconds until the next occurrence of HH:MM local time."""
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    now = time.time() if now is None else now
    local = time.localtime(now)
    target = time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1)
    )
    delay = target - now
    return delay if delay > _MIN_LEAD_SECONDS else delay + 86400


def register_webhooks() -> None:
    """Keep at it until every enabled *arr has the connection.

    The containers usually start together, so the first attempts can land
    before Radarr or Sonarr is answering.
    """
    pending = [arr for arr in all_arrs() if arr.enabled]
    while pending:
        pending = [arr for arr in pending if not arr.register_webhook(config.WEBHOOK_URL)]
        if pending:
            time.sleep(300)


def scheduler() -> None:
    while True:
        try:
            delay = seconds_until(config.SWEEP_AT)
        except ValueError:
            log.error("SWEEP_AT=%r is not HH:MM, scheduler stopping", config.SWEEP_AT)
            return
        log.info("next sweep in %.1f hours (apply=%s)", delay / 3600, config.SWEEP_APPLY)
        time.sleep(delay)
        try:
            sweep(dry_run=not config.SWEEP_APPLY)
        except Exception:
            log.exception("sweep failed")


def serve() -> None:
    clean_work_dir()
    threading.Thread(target=worker, daemon=True, name="worker").start()
    if _parking_enabled():
        threading.Thread(target=parked_recheck_loop, daemon=True, name="parked-recheck").start()
    if config.SWEEP_AT:
        threading.Thread(target=scheduler, daemon=True, name="scheduler").start()
    srv = ThreadingHTTPServer((config.LISTEN_ADDR, config.LISTEN_PORT), Handler)
    # Saving the connection makes the *arr fire a test event at us, so this
    # only starts once the socket above is bound and listening; the test
    # request queues until serve_forever picks it up.
    threading.Thread(target=register_webhooks, daemon=True, name="register").start()
    log.info("listening on %s:%s", config.LISTEN_ADDR, config.LISTEN_PORT)
    log.info(
        "arrs=[%s] servers=[%s] always_keep=%s sweep_at=%s apply=%s",
        ", ".join(f"{arr.name}={'on' if arr.enabled else 'off'}" for arr in all_arrs()),
        ", ".join(f"{name}={'on' if on else 'off'}" for name, on in server_status().items()),
        sorted(config.ALWAYS_KEEP),
        config.SWEEP_AT or "disabled",
        config.SWEEP_APPLY,
    )
    srv.serve_forever()
