"""The import queue: the worker pool that drains it, and the parking of files
a download client still hard-links.

:mod:`trackstarr.webhook` parses a delivery and queues it, so a slow *arr
never stalls the response; the workers here do the probing and rewriting. The
pool follows MAX_CONCURRENT_REWRITES, which a settings save can change without
a restart.
"""

import json
import logging
import os
import queue
import threading
import time
from dataclasses import replace

from . import config, events, runs, sweep
from .arr import all_arrs, original_of
from .processing import Job, downmixed_names, process
from .state import write_json
from .status import Status
from .sweep_cache import cache_key

log = logging.getLogger(__name__)


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


def parked_count() -> int:
    """How many files are waiting on a download client, for the API's
    snapshot."""
    with _parked_lock:
        return len(_parked)


def parked_jobs() -> list[Job]:
    """Every job waiting on a download client."""
    with _parked_lock:
        return list(_parked.values())


def queued_count() -> int:
    """How many jobs are waiting for a worker."""
    return _work_q.qsize()


def reset() -> None:
    """Drop the queue, what is in flight and what is parked. For tests: a job
    left behind would be picked up by the next one."""
    with _inflight_lock:
        _inflight.clear()
    with _parked_lock:
        _parked.clear()
    while not _work_q.empty():
        _work_q.get_nowait()


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


def park(job: Job) -> None:
    with _parked_lock:
        _parked[job.path] = job
    _save_parked()
    log.info("parked %s until the download client releases it", job.path)


def handle(job: Job) -> None:
    """Process a webhook job, parking it while the file is still seeded."""
    if runs.stopping(job.run):
        # Dropped rather than processed: "stop" must not mean "stop after the
        # twenty files already queued".
        runs.drop(job.run)
        log.info("run %s is stopping, dropping %s", job.run, job.path)
        return
    if runs.skipped(job.run, job.path):
        # Booked rather than dropped: somebody asked for this one by name and
        # the delivery's row should say what became of it.
        log.info("%s was skipped, leaving it", job.path)
        runs.tally(
            job.run,
            str(Status.DEFERRED),
            path=job.path,
            detail="skipped, so this delivery left it alone",
        )
        return
    job = _resolve_lang(job)
    if parking_enabled() and hardlinked(job.path):
        park(job)
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
    if result.status is Status.PENDING and result.plan:
        # No pending.tsv row for a webhook, so the history is the only record.
        events.record(
            "pending",
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


def worker_count() -> int:
    """How many workers the pool holds."""
    with _workers_lock:
        return _workers


def retire() -> bool:
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


# No cover: a thread body. handle and retire hold the decisions and are covered.
def worker() -> None:  # pragma: no cover
    while not retire():
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
            handle(job)
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


def recheck_parked() -> None:
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
            recheck_parked()
        except Exception:
            log.exception("parked recheck failed")
