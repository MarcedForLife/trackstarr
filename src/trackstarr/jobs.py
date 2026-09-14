"""Import jobs in the shared work queue, and parking files a client hard-links.

:mod:`trackstarr.webhook` parses a delivery and queues it, so a slow *arr
never stalls the response. Imports are checked on the priority probe lane,
then join the rewrite lane only when needed. Automatic rewrite promotion
yields to a queue the operator has reordered.
"""

import json
import logging
import os
import threading
import time
from dataclasses import replace

from . import config, events, lifecycle, runs, sweep, work
from .arr import all_arrs, original_of
from .executor import Cancel
from .policy import Policy
from .processing import Job, changed_tracks, effective_dry_run, process
from .state import write_json
from .status import Status
from .sweep_cache import ObservationStoppedError, cache_key, observing

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
    with work.scheduler.condition:
        return sum(work.scheduler.queued.values())


def forget() -> None:
    """Drop what is in flight and what is parked. For tests: a job left behind
    would be picked up by the next one. The queue itself is
    :func:`trackstarr.lifecycle.reset`."""
    with _inflight_lock:
        _inflight.clear()
    with _parked_lock:
        _parked.clear()


def _resolve_lang(job: Job) -> Job:
    """Fetch the original language when the webhook body lacked it.

    Older Radarr and Sonarr omit ``originalLanguage``. Looked up on the worker
    so the HTTP handler never waits on an *arr.
    """
    if job.lang is not None or not job.arr or not job.item_id:
        return job
    return replace(job, lang=original_of(job.arr.item(job.item_id)))


def parking_enabled() -> bool:
    settings = config.current()
    return settings.SKIP_HARDLINKS and settings.HARDLINK_RECHECK > 0


def park(job: Job) -> None:
    with _parked_lock:
        _parked[job.path] = job
    _save_parked()
    log.info("parked %s until the download client releases it", job.path)


def handle(job: Job, cancel: Cancel | None = None) -> None:
    """Process a webhook job, parking it while the file is still seeded."""
    with lifecycle.import_result(job.run, job.path) as result:
        _handle(job, result, cancel)


def _handle(
    job: Job,
    accounting: lifecycle.ImportResult,
    cancel: Cancel | None = None,
    *,
    discovery: bool = False,
) -> Job | None:
    """Return the resolved job only when discovery needs a rewrite phase."""
    if work.scheduler.stopping(job.run):
        # Dropped rather than processed: "stop" must not mean "stop after the
        # twenty files already queued".
        accounting.dropped = True
        log.info("run %s is stopping, dropping %s", job.run, job.path)
        return None
    if work.scheduler.skipped(job.run, job.path):
        # Booked rather than dropped: somebody asked for this one by name and
        # the delivery's row should say what became of it.
        log.info("%s was skipped, leaving it", job.path)
        accounting.status = Status.DEFERRED
        accounting.detail = "skipped, so this delivery left it alone"
        return None
    job = _resolve_lang(job)
    if parking_enabled() and hardlinked(job.path):
        park(job)
        # Booked as deferred so the run can close; an uncounted file would keep
        # the delivery on the overview for ever.
        accounting.status = Status.DEFERRED
        accounting.detail = "a download client still has this hard-linked"
        return None
    runs.begin(job.run, job.path)
    continuing = False
    try:
        # Keep telemetry cleanup around observation entry as well as processing:
        # policy selection and cache-key acquisition can fail before the probe.
        with observing(
            job.path, policy=Policy.from_config(), stopped=cancel.stopped if cancel else None
        ) as observation:
            key = cache_key(job.path, job.lang)
            result = process(
                job,
                dry_run=discovery,
                policy=observation.policy,
                cancel=cancel,
                observation=observation,
            )
            accounting.status, accounting.detail = result.status, result.detail
            # The library reads verdicts from the cache, not the history.
            sweep.remember(job.path, key, result, observation)
            continuing = (
                discovery
                and result.status is Status.PENDING
                and not effective_dry_run(False, job.path)
            )
    except ObservationStoppedError:
        accounting.status = Status.DEFERRED
        accounting.detail = "skipped while waiting for another edit"
        return None
    finally:
        runs.finish(job.run, job.path, continuing=continuing)
    if continuing:
        return job
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
            **changed_tracks(result.plan),
        )

    return None


def _handle_queued(
    job: Job, accounting: lifecycle.ImportResult, cancel: Cancel, *, discovery: bool = False
) -> Job | None:
    # Reset the intermediate pending result so a rewrite failing before it can
    # judge is booked as failed, just like a one-phase import.
    accounting.status, accounting.detail = Status.FAILED, ""
    try:
        return _handle(job, accounting, cancel, discovery=discovery)
    except Exception:
        log.exception("unhandled error processing %s", job.path)
        return None


def _discovered(phase: work.Phase, accounting: lifecycle.ImportResult) -> None:
    # The scheduler already terminates exceptional and cancelled phases.
    if phase.cancelled() or phase.exception() is not None:
        return
    if job := phase.result():
        cancel = Cancel(job.path)
        work.scheduler.continue_file(
            phase.handle,
            lambda: _handle_queued(job, accounting, cancel),
            cancel=cancel,
            hurry=True,
        )
    else:
        work.scheduler.complete_file(phase.handle)


def _finish_import(job: Job, accounting: lifecycle.ImportResult) -> None:
    """Book once across both phases, then release the delivery's dedup claim."""
    try:
        with lifecycle.import_result(job.run, job.path) as result:
            result.status, result.detail = accounting.status, accounting.detail
            result.dropped = accounting.dropped
    finally:
        _release_import(job.path)


def _release_import(path: str) -> None:
    with _inflight_lock:
        _inflight.discard(path)


def enqueue(job: Job) -> bool:
    with _inflight_lock:
        if job.path in _inflight:
            return False
        _inflight.add(job.path)
    cancel = Cancel(job.path)
    accounting = lifecycle.ImportResult()
    try:
        phase = lifecycle.submit_import(
            job.run,
            job.path,
            lambda: _handle_queued(job, accounting, cancel, discovery=True),
            on_terminal=lambda: _finish_import(job, accounting),
            cancel=cancel,
            discovery=True,
        )
        phase.add_done_callback(lambda finished: _discovered(phase, accounting))
    except BaseException:
        _release_import(job.path)
        raise
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
    with lifecycle.producer() as allowed:
        if not allowed:
            return
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
            if not _release(job):
                break
        # Persistence belongs to the producer too.
        if released:
            _save_parked()


def _release(job: Job) -> bool:
    """Hand one parked file back to the queue. False where it stays parked.

    The record is dropped before the handoff, so a shutdown refusing it has to
    put the file back: this is the only copy of a delivery nobody will send
    again.
    """
    try:
        # Reopen the delivery's run for this one file, then seal it again.
        if job.run:
            lifecycle.open_run(job.run, runs.IMPORT, label=job.arr.name if job.arr else "")
        if enqueue(job):
            log.info("hard link released, queued %s", job.path)
        if job.run:
            lifecycle.seal(job.run)
    except ValueError as err:
        with _parked_lock:
            _parked.setdefault(job.path, job)
        log.info("%s stays parked: %s", job.path, err)
        return False
    return True


#: The recheck thread sleeps in slices this long so a HARDLINK_RECHECK change
#: from the UI is picked up without a restart.
_PARKED_TICK = 30.0


# No cover: a thread body around _recheck_parked, which is covered directly.
def parked_recheck_loop() -> None:  # pragma: no cover
    """Revisit parked files every HARDLINK_RECHECK seconds.

    Started whether or not parking is on, so switching SKIP_HARDLINKS on later
    does not park files nothing revisits. Switching it off runs the next pass
    at once. Ends with the process, since a released file must not be queued
    into a scheduler that is draining.
    """
    waited = 0.0
    while lifecycle.producing():
        time.sleep(_PARKED_TICK)
        waited += _PARKED_TICK
        if parking_enabled() and waited < config.current().HARDLINK_RECHECK:
            continue
        waited = 0.0
        try:
            recheck_parked()
        except Exception:
            log.exception("parked recheck failed")
