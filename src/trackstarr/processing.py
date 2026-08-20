"""Plan-and-rewrite for one file, shared by the webhook worker and the sweep."""

import contextlib
import fcntl
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass

from . import config, events
from .arr import Arr, LibraryItem
from .executor import Outcome, apply_plan
from .media import ProbeError
from .media_server import refresh_servers
from .planner import Plan, build_plan, describe
from .status import Status

log = logging.getLogger(__name__)

#: How long a rewrite waits before re-checking the slots. Rewrites run for
#: minutes, so polling this slowly costs nothing. The flock pool is the only
#: thing enforcing the limit; a semaphore beside it could drift from it.
_SLOT_POLL_SECONDS = 1.0

_SLOT_PREFIX = "rewrite.lock."

#: Slot locks live in their own directory: empty lock files beside
#: pending.tsv and the history would read as state rather than scratch.
_LOCK_DIRNAME = "locks"


def _lock_dir() -> str:
    return os.path.join(config.STATE_DIR, _LOCK_DIRNAME)


def _try_lock(name: str):
    """Flock a slot file and return the open handle, or None if someone
    else holds it. The handle *is* the lock; closing it releases."""
    lock_file = open(os.path.join(_lock_dir(), name), "w")  # noqa: SIM115
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def _claim_slot():
    """Lock one of the STATE_DIR slot files and return the open handle.

    MAX_CONCURRENT_REWRITES is machine-wide, not per-process: a ``docker exec
    trackstarr sweep --apply`` beside a running serve competes for the same
    budget. Hence a pool of flock files rather than a semaphore no other
    process could see. Blocks until one frees; rewrites are a queue by design.
    """
    os.makedirs(_lock_dir(), exist_ok=True)
    while True:
        for slot in range(config.MAX_CONCURRENT_REWRITES):
            if lock_file := _try_lock(f"{_SLOT_PREFIX}{slot}"):
                return lock_file
        time.sleep(_SLOT_POLL_SECONDS)


def state_dir_errors() -> list[str]:
    """Whether STATE_DIR can hold what a rewrite needs, as messages.

    Creates it and its lock directory, then takes a slot lock, the same open
    :func:`_claim_slot` does and the first thing to fail on a root-owned
    ``/config``. Caught here it is a line in the startup report rather than
    a restart-loop traceback. A slot another process holds is fine; the open
    succeeded.
    """
    try:
        os.makedirs(_lock_dir(), exist_ok=True)
        if lock_file := _try_lock(f"{_SLOT_PREFIX}0"):
            lock_file.close()
    except OSError as err:
        return [f"STATE_DIR {config.STATE_DIR} is not usable: {err}"]
    return []


@contextlib.contextmanager
def all_slots_held() -> Iterator[bool]:
    """Every rewrite slot on the machine, or none; yields which.

    A staged file exists only while its rewrite holds a slot, so holding them
    all proves WORK_DIR has nothing but orphans in it, and startup cleans it
    under this. Never waits: if any slot is taken, everything is released and
    False yielded at once.
    """
    os.makedirs(_lock_dir(), exist_ok=True)
    wanted = {f"{_SLOT_PREFIX}{slot}" for slot in range(config.MAX_CONCURRENT_REWRITES)}
    # A bigger budget elsewhere can hold slots past our range, but a held
    # slot's file always exists, so the union covers every rewrite.
    wanted.update(name for name in os.listdir(_lock_dir()) if name.startswith(_SLOT_PREFIX))
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


@dataclass(frozen=True)
class Job:
    """One file to process, with what the *arrs know about it.

    ``run`` groups the jobs one delivery, sweep or fix invocation queued
    together; see :func:`trackstarr.events.run_id`.
    """

    path: str
    lang: str | None = None
    item_id: int | None = None
    arr: Arr | None = None
    run: str | None = None

    @classmethod
    def from_match(
        cls,
        path: str,
        item: LibraryItem | None,
        lang: str | None = None,
        run: str | None = None,
    ) -> Job:
        """A job for ``path``, carrying whatever its title was matched to.

        ``lang`` beats the matched language, for ``--original``. The item is
        still worth having: its id is what gets the *arr its rescan.
        """
        if item is None:
            return cls(path, lang, run=run)
        return cls(path, lang or item.lang, item.item_id, item.arr, run)


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
    """Layout names of the downmixes this plan makes. Encode streams carry
    their layout's name as the track title."""
    return [stream.title for stream in plan.streams if stream.encode]


def effective_dry_run(dry_run: bool) -> bool:
    """Whether a run may rewrite, given what the caller asked and DRY_RUN.

    The latch bottoms out in :func:`process`, so no new entry point can
    rewrite a library its owner is still watching. A function because the
    sweep needs the answer before it calls process().
    """
    return dry_run or config.DRY_RUN


def process(job: Job, dry_run: bool, source: str = "webhook") -> ProcessResult:
    """Plan one file and, unless dry_run, rewrite it.

    ``source`` labels the history entry a rewrite attempt leaves, and
    ``job.run`` ties it to the sweep or delivery that queued it. Probe
    failures leave no entry: they recur every sweep until the file is fixed,
    filling the history with one repeated problem.
    """
    dry_run = effective_dry_run(dry_run)
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
    # now. The fallback covers hand-built plans with no signature.
    bytes_before = plan.src_signature.size if plan.src_signature else _file_size(job.path)
    started = time.monotonic()
    try:
        # Closing the slot handle releases the flock, even if this raises.
        with contextlib.closing(_claim_slot()):
            outcome, detail = apply_plan(plan)
    # The verify probe on a corrupt result, a source deleted mid-job, WORK_DIR
    # gone. One file failing must not take the rest of a sweep with it.
    except (ProbeError, OSError) as err:
        outcome, detail = Outcome.FAILED, str(err)
    event_fields = {
        "run": job.run,
        "source": source,
        "config_id": plan.policy.digest(),
        "reasons": plan.reasons,
        "rules": sorted(plan.rules),
        "seconds": round(time.monotonic() - started, 1),
    }
    if outcome is Outcome.APPLIED:
        events.record(
            "fixed",
            path=plan.out_path,
            # Only for a remux, since None is dropped. Without it the history
            # says an .mkv was fixed and never names the .mp4 it was.
            from_path=plan.path if plan.out_path != plan.path else None,
            incidental=plan.incidental,
            incidental_rules=sorted(plan.incidental_rules),
            downmixed=downmixed_names(plan) or None,
            bytes_before=bytes_before,
            bytes_after=_file_size(plan.out_path),
            **event_fields,
        )
        if job.arr and job.item_id:
            job.arr.rescan(job.item_id)
        # out_path, not job.path: a remux publishes under a new extension.
        refresh_servers(plan.out_path)
        return ProcessResult(Status.FIXED, plan)
    if outcome is Outcome.DEFERRED:
        log.info("deferred %s: %s", job.path, detail)
        # One deferral is benign, but a file that defers every pass leaves no
        # other trace and the sweep's counts cannot name it.
        events.record("deferred", path=job.path, detail=detail, **event_fields)
        return ProcessResult(Status.DEFERRED, plan, detail)
    log.warning("rewrite of %s failed: %s", job.path, detail)
    events.record("failed", path=job.path, detail=detail, **event_fields)
    return ProcessResult(Status.FAILED, plan, detail)
