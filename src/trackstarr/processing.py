"""Plan-and-rewrite for one file, shared by the webhook worker and the sweep."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from . import config, events
from .arr import Arr
from .executor import Outcome, apply_plan
from .media import ProbeError
from .media_server import refresh_servers
from .planner import Plan, build_plan, describe
from .status import Status

log = logging.getLogger(__name__)

#: Guards _running against MAX_CONCURRENT_REWRITES. A condition rather than a
#: semaphore because the limit is read at acquire time, so a test can move it
#: with monkeypatch like every other setting.
_rewrite_cv = threading.Condition()
_running = 0

#: How long a rewrite waits before re-checking the cross-process slots. Only
#: reached when other processes hold them all, and rewrites run for minutes,
#: so polling this slowly costs nothing.
_SLOT_POLL_SECONDS = 1.0


@contextlib.contextmanager
def _budget():
    """One of MAX_CONCURRENT_REWRITES slots within this process."""
    global _running
    with _rewrite_cv:
        _rewrite_cv.wait_for(lambda: _running < config.MAX_CONCURRENT_REWRITES)
        _running += 1
    try:
        yield
    finally:
        with _rewrite_cv:
            _running -= 1
            _rewrite_cv.notify()


_SLOT_PREFIX = "rewrite.lock."


def _try_lock(name: str):
    """Flock a STATE_DIR lock file and return the open handle, or None when
    another holder has it. The handle *is* the lock; closing it releases."""
    lock_file = open(os.path.join(config.STATE_DIR, name), "w")  # noqa: SIM115
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def _claim_slot():
    """Lock one of the STATE_DIR slot files and return the open handle.

    The in-process budget can't see a ``docker exec trackstarr sweep --apply``
    running beside serve, so the limit is mirrored as a pool of flock files
    every process competes for. Blocks until one frees: rewrites are a queue
    by design.
    """
    os.makedirs(config.STATE_DIR, exist_ok=True)
    while True:
        for slot in range(config.MAX_CONCURRENT_REWRITES):
            if lock_file := _try_lock(f"{_SLOT_PREFIX}{slot}"):
                return lock_file
        time.sleep(_SLOT_POLL_SECONDS)


@contextlib.contextmanager
def all_slots_held() -> Iterator[bool]:
    """Every rewrite slot on the machine, or none: yields whether it got all.

    A staged file only exists while its rewrite holds a slot, so holding them
    all proves WORK_DIR contains nothing but orphans; startup cleans it under
    this. Never waits: when any slot is taken (a sweep in another process,
    mid-rewrite), everything is released and False yielded immediately.
    """
    os.makedirs(config.STATE_DIR, exist_ok=True)
    wanted = {f"{_SLOT_PREFIX}{slot}" for slot in range(config.MAX_CONCURRENT_REWRITES)}
    # A process given a bigger budget can hold slots past our range, but a
    # held slot's file always exists, so the union covers every rewrite.
    wanted.update(
        name for name in os.listdir(config.STATE_DIR) if name.startswith(_SLOT_PREFIX)
    )
    held: list = []
    try:
        for name in sorted(wanted):
            if lock_file := _try_lock(name):
                held.append(lock_file)
                continue
            for got in held:
                got.close()
            held.clear()
            yield False
            return
        yield True
    finally:
        for lock_file in held:
            lock_file.close()


@contextlib.contextmanager
def _exclusive_rewrite():
    """A rewrite slot held against every other rewrite on the machine."""
    with _budget():
        lock_file = _claim_slot()
        try:
            yield
        finally:
            # Closing releases the flock, even if the rewrite raised.
            lock_file.close()


@dataclass(frozen=True)
class Job:
    """One file to process, with what the *arrs know about it."""

    path: str
    lang: str | None = None
    item_id: int | None = None
    arr: Arr | None = None


@dataclass(frozen=True)
class ProcessResult:
    status: Status
    plan: Plan | None = None
    #: Why the rewrite didn't happen, for deferred and failed.
    detail: str = ""


def _file_size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def downmixed_names(plan: Plan) -> list[str]:
    """Layout names of the downmixes this plan creates or rebuilds; encode
    streams carry their layout's name as the track title."""
    return [stream.title for stream in plan.streams if stream.encode]


def process(
    job: Job, dry_run: bool, source: str = "webhook", run: str | None = None
) -> ProcessResult:
    """Plan one file and, unless dry_run, rewrite it.

    ``source`` and ``run`` label the event history entry a rewrite attempt
    leaves; ``run`` ties a sweep's rewrites to its summary event. Probe
    failures leave no entry: they recur every sweep until the file is fixed,
    which would fill the history with repeats of one problem.
    """
    # The DRY_RUN latch bottoms out here rather than in each caller, so no
    # new entry point can rewrite a library its owner is still observing.
    dry_run = dry_run or config.DRY_RUN
    try:
        plan = build_plan(job.path, job.lang)
    except ProbeError as err:
        log.warning("probe failed for %s: %s", job.path, err)
        return ProcessResult(Status.FAILED, detail=str(err))

    if plan.skip:
        log.debug("skip %s: %s", job.path, plan.skip)
        return ProcessResult(Status.SKIP, plan)
    if not plan.needed:
        return ProcessResult(Status.CONFORM, plan)
    if dry_run:
        log.info("would fix %s: %s", job.path, describe(plan))
        return ProcessResult(Status.WOULD_FIX, plan)

    log.info("fixing %s: %s", job.path, describe(plan))
    # The size at plan time, which apply_plan guarantees is still the size
    # now; the fallback covers hand-built plans that carry no signature.
    bytes_before = plan.src_signature.size if plan.src_signature else _file_size(job.path)
    started = time.monotonic()
    try:
        with _exclusive_rewrite():
            outcome, detail = apply_plan(plan)
    # Everything the rewrite can raise: the verify probe on a corrupt
    # result, a source deleted mid-job, WORK_DIR or the lock file's home
    # gone. One file failing must not take the rest of a sweep down with it.
    except (ProbeError, OSError) as err:
        outcome, detail = Outcome.FAILED, str(err)
    seconds = round(time.monotonic() - started, 1)
    if outcome is Outcome.APPLIED:
        events.record(
            "fixed",
            run=run,
            source=source,
            path=plan.out_path,
            reasons=plan.reasons,
            incidental=plan.incidental,
            downmixed=downmixed_names(plan) or None,
            bytes_before=bytes_before,
            bytes_after=_file_size(plan.out_path),
            seconds=seconds,
        )
        if job.arr and job.item_id:
            job.arr.rescan(job.item_id)
        # out_path, not job.path: a remux publishes under a new extension.
        refresh_servers(plan.out_path)
        return ProcessResult(Status.FIXED, plan)
    if outcome is Outcome.DEFERRED:
        log.info("deferred %s: %s", job.path, detail)
        return ProcessResult(Status.DEFERRED, plan, detail)
    log.warning("rewrite of %s failed: %s", job.path, detail)
    events.record(
        "failed",
        run=run,
        source=source,
        path=job.path,
        reasons=plan.reasons,
        detail=detail,
        seconds=seconds,
    )
    return ProcessResult(Status.FAILED, plan, detail)
