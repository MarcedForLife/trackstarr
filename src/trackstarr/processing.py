"""Plan-and-rewrite for one file, shared by the webhook worker and the sweep."""

import contextlib
import fcntl
import functools
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace

from . import config, events, runs
from .arr import Arr, LibraryItem
from .executor import Outcome, apply_plan
from .media import ProbeError
from .media_server import refresh_servers
from .planner import Plan, build_plan, describe, planned_tracks, track_changes, why
from .status import Status
from .sweep_cache import FileKey, Verdict, cache_key

log = logging.getLogger(__name__)

#: How long a rewrite waits before re-checking the slots. The flock pool is the
#: only thing enforcing the limit.
_SLOT_POLL_SECONDS = 1.0

_SLOT_PREFIX = "rewrite.lock."

#: Slot locks live in their own directory, so they do not read as state.
_LOCK_DIRNAME = "locks"


def _lock_dir() -> str:
    return os.path.join(config.STATE_DIR, _LOCK_DIRNAME)


def _try_lock(name: str):
    """Flock a slot file and return the handle, or None if held elsewhere.
    Closing the handle releases the lock."""
    lock_file = open(os.path.join(_lock_dir(), name), "w")  # noqa: SIM115
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def _claim_slot():
    """Block until a slot file is locked, and return the handle.

    Flock files rather than a semaphore because MAX_CONCURRENT_REWRITES is
    machine-wide: a ``docker exec trackstarr sweep --apply`` competes with
    the running serve.
    """
    os.makedirs(_lock_dir(), exist_ok=True)
    while True:
        for slot in range(config.MAX_CONCURRENT_REWRITES):
            if lock_file := _try_lock(f"{_SLOT_PREFIX}{slot}"):
                return lock_file
        time.sleep(_SLOT_POLL_SECONDS)


def state_dir_errors() -> list[str]:
    """Whether STATE_DIR is usable, as ready-to-log messages.

    Creates the lock directory and opens a slot file, the first thing to fail
    on a root-owned ``/config``. A slot another process holds is fine.
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
    """Hold every rewrite slot on the machine, or none; yields which.

    Holding them all proves WORK_DIR has only orphans, so startup cleans it
    under this. Never waits.
    """
    os.makedirs(_lock_dir(), exist_ok=True)
    wanted = {f"{_SLOT_PREFIX}{slot}" for slot in range(config.MAX_CONCURRENT_REWRITES)}
    # Another process with a bigger budget can hold slots past our range; its
    # files exist, so the union covers every rewrite.
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
    """One file to process, with what the *arrs know about it. ``run`` groups
    the jobs one delivery, sweep or fix queued; see
    :func:`trackstarr.events.run_id`."""

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
        """A job for ``path`` carrying its matched title. ``lang`` beats the
        matched language, for ``--original``; the item id is still needed for
        the rescan."""
        if item is None:
            return cls(path, lang, run=run)
        return cls(path, lang or item.lang, item.item_id, item.arr, run)


@dataclass(frozen=True)
class Rewritten:
    """The file a rewrite left behind, judged again where it published.

    The file that was read is gone, so this is the only verdict left to store.
    ``path`` is the plan's output, since a remux publishes under a new
    extension.
    """

    path: str
    key: FileKey
    verdict: Verdict


@dataclass(frozen=True)
class ProcessResult:
    status: Status
    plan: Plan | None = None
    #: Why the rewrite didn't happen, for deferred and failed.
    detail: str = ""
    #: What a published rewrite wrote; see :class:`Rewritten`. None otherwise,
    #: and when the result could not be judged again.
    became: Rewritten | None = None


def verdict_of(result: ProcessResult, failures: int = 0) -> Verdict:
    """The verdict a processed file stores as, shared by the sweep and the
    webhook so the two agree.

    ``failures`` is the sweep's count of consecutive broken rewrites; see
    :data:`trackstarr.sweep.MAX_FAILURES`.
    """
    plan = result.plan
    # The failure's words go in `why`, where the library looks for reasons.
    told = why(plan) if plan else {}
    if result.status is Status.FAILED and result.detail:
        told = told | {"failed": result.detail}
    return Verdict(
        result.status,
        describe(plan) if plan else "",
        plan.tracks if plan else [],
        # planned only where there is a rewrite to describe.
        planned_tracks(plan) if plan and plan.needed else [],
        told,
        # Named, since both are numbers and a swap would be silent.
        duration=plan.src_duration if plan else 0.0,
        failures=failures,
    )


def _before(plan: Plan) -> dict:
    """The file as it stood, and what the rewrite made of its streams.

    The after is the file itself, which is probed again on the way out, so this
    is the one half a library view could not go and read. On the verdict alone:
    the history's own line says what changed in prose, and a page reading it
    has the whole event.

    Nothing at all where no track moved, which is most rewrites: a remux or a
    cleared title leaves the same streams in the same order, so the before is
    the after and neither the room in the cache nor a second column is worth
    it.
    """
    changes = track_changes(plan)
    return {"was": plan.tracks, **changes} if changes and plan.tracks else {}


def _fixed(plan: Plan, bytes_before: int | None, bytes_after: int | None) -> dict:
    """What the rewrite did: when, the sizes either side, and the streams it
    moved.

    Kept on the verdict because the file passes from here on: without it a
    rewritten file is a Passed one, indistinguishable from a file the rules
    never touched. The reasons in prose are the event's, and the history page
    reads them there; a library view has the two track lists instead.
    """
    record = {"at": events.timestamp(), **_before(plan)}
    if bytes_before and bytes_after:
        record |= {"bytes_before": bytes_before, "bytes_after": bytes_after}
    return record


def _rejudged(job: Job, plan: Plan, fixed: dict) -> Rewritten | None:
    """The file a rewrite has just published, judged again.

    Without this a fixed title reads as unchecked until the next sweep. One
    probe, through :func:`process` in report mode so verdicts are reached in
    one place; that call never rewrites, so it cannot recurse. None when the
    file has gone or the probe would not read it. ``fixed`` is :func:`_fixed`,
    which the fresh verdict has no way of knowing.
    """
    key = cache_key(plan.out_path, job.lang)
    if key is None:
        return None
    judged = process(replace(job, path=plan.out_path), dry_run=True)
    if judged.status is Status.FAILED:
        # The probe failed, not the rewrite, which verified its output. Storing
        # "Failed" against a checked file would be wrong.
        log.warning("could not judge %s after rewriting it: %s", plan.out_path, judged.detail)
        return None
    return Rewritten(plan.out_path, key, replace(verdict_of(judged), fixed=fixed))


def _file_size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def downmixed_names(plan: Plan) -> list[str]:
    """Layout names of the downmixes this plan makes."""
    return [stream.title for stream in plan.streams if stream.encode]


def effective_dry_run(dry_run: bool) -> bool:
    """Whether a run is dry, given the caller and REWRITE_MODE. ``report``
    latches over every caller, ``sweep --apply`` included."""
    return dry_run or config.REWRITE_MODE == "report"


def process(job: Job, dry_run: bool, source: str = "webhook") -> ProcessResult:
    """Plan one file and, unless dry_run, rewrite it.

    ``source`` labels the history entry a rewrite attempt leaves. Probe
    failures leave none: they would recur every sweep.
    """
    dry_run = effective_dry_run(dry_run)
    try:
        plan = build_plan(job.path, job.lang)
    except ProbeError as err:
        log.warning("probe failed for %s: %s", job.path, err)
        return ProcessResult(Status.FAILED, detail=str(err))

    if plan.skip:
        log.debug("skip %s: %s", job.path, plan.skip)
        # The plan says which status its skip is: an unsupported container is
        # not filed with the hardlinked and the silent.
        return ProcessResult(plan.skip_status, plan)
    if not plan.needed:
        return ProcessResult(Status.CONFORM, plan)
    if dry_run:
        log.info("would fix %s: %s", job.path, describe(plan))
        return ProcessResult(Status.WOULD_FIX, plan)

    log.info("fixing %s: %s", job.path, describe(plan))
    # The size at plan time, which apply_plan guarantees still holds. The
    # fallback is for hand-built plans.
    bytes_before = plan.src_signature.size if plan.src_signature else _file_size(job.path)
    started = time.monotonic()
    # The wait for a slot, kept apart from the work: only the work says anything
    # about the file; see :mod:`trackstarr.estimate`.
    waited = 0.0
    try:
        # So the overview does not draw a bar that has not started moving.
        runs.stage(job.run, job.path, runs.WAITING)
        # Closing the slot handle releases the flock, even if this raises.
        with contextlib.closing(_claim_slot()):
            waited = time.monotonic() - started
            runs.stage(job.run, job.path, runs.ENCODING, plan.src_duration)
            outcome, detail = apply_plan(
                plan,
                functools.partial(runs.progress, job.run, job.path),
                # Verify and publish are not the encode; a bar left at full
                # would say the file is still being written.
                functools.partial(runs.stage, job.run, job.path, runs.WORKING),
            )
    # A corrupt result, a source deleted mid-job, WORK_DIR gone. One file must
    # not take the rest of a sweep with it.
    except (ProbeError, OSError) as err:
        outcome, detail = Outcome.FAILED, str(err)
    finally:
        # For the paths that never reached on_encoded.
        runs.stage(job.run, job.path, runs.WORKING)
    event_fields = {
        "run": job.run,
        "source": source,
        "config_id": plan.policy.digest(),
        "reasons": plan.reasons,
        "rules": sorted(plan.rules),
        "seconds": round(time.monotonic() - started, 1),
        # Only the time spent working scales with the file; the estimates are
        # calibrated from it.
        "waited": round(waited, 1) or None,
        "duration": round(plan.src_duration, 1) or None,
    }
    if outcome is Outcome.APPLIED:
        bytes_after = _file_size(plan.out_path)
        events.record(
            "fixed",
            path=plan.out_path,
            # Only for a remux; None is dropped.
            from_path=plan.path if plan.out_path != plan.path else None,
            incidental=plan.incidental,
            incidental_rules=sorted(plan.incidental_rules),
            downmixed=downmixed_names(plan) or None,
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            **event_fields,
        )
        if job.arr and job.item_id:
            job.arr.rescan(job.item_id)
        # out_path, not job.path: a remux publishes under a new extension.
        refresh_servers(plan.out_path)
        fixed = _fixed(plan, bytes_before, bytes_after)
        return ProcessResult(Status.FIXED, plan, became=_rejudged(job, plan, fixed))
    if outcome is Outcome.DEFERRED:
        log.info("deferred %s: %s", job.path, detail)
        # Recorded because a file that defers every pass leaves no other trace.
        events.record("deferred", path=job.path, detail=detail, **event_fields)
        return ProcessResult(Status.DEFERRED, plan, detail)
    log.warning("rewrite of %s failed: %s", job.path, detail)
    events.record("failed", path=job.path, detail=detail, **event_fields)
    return ProcessResult(Status.FAILED, plan, detail)
