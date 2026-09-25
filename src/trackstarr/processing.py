"""Plan-and-rewrite for one file, shared by the webhook worker and the sweep."""

import contextlib
import fcntl
import functools
import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

from . import config, events, mkvtag, pauses, rewrites, runs
from .arr import Arr, LibraryItem
from .executor import Cancel, Outcome, apply_plan
from .media import ProbeError
from .media_server import refresh_servers
from .planner import Plan, build_plan, changes, describe, planned_tracks, track_changes, why
from .policy import Policy
from .status import Status
from .sweep_cache import FileKey, Observation, Verdict, cache_key

log = logging.getLogger(__name__)

#: How long a rewrite waits before re-checking the slots. The flock pool is the
#: only thing enforcing the limit.
_SLOT_POLL_SECONDS = 1.0

_SLOT_PREFIX = "rewrite.lock."

#: Slot locks live in their own directory, so they do not read as state.
_LOCK_DIRNAME = "locks"

#: What a file skipped before its rewrite started reports. Deferred, not
#: failed: nothing is wrong with it and the next pass will pick it up.
STOPPED_BEFORE_START = "the rewrite was stopped before it started, nothing rewritten"


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


def _pause_for_slot(cancel: Cancel) -> None:
    """Wait out the poll interval, returning at once on a skip."""
    cancel.wait(_SLOT_POLL_SECONDS)


def _claim_slot(cancel: Cancel | None = None):
    """Block until a slot file is locked, and return the handle, or None if the
    phase was skipped while it waited.

    Flock files rather than a semaphore because MAX_CONCURRENT_REWRITES is
    machine-wide: a ``docker exec trackstarr sweep --apply`` competes with
    the running serve.
    """
    cancel = cancel or Cancel()
    os.makedirs(_lock_dir(), exist_ok=True)
    while not cancel.stopped():
        for slot in range(config.current().MAX_CONCURRENT_REWRITES):
            if lock_file := _try_lock(f"{_SLOT_PREFIX}{slot}"):
                # A skip that won the race gives the slot straight back rather
                # than spending an encode on a file nobody is waiting for.
                if cancel.stopped():
                    lock_file.close()
                    return None
                return lock_file
        _pause_for_slot(cancel)
    return None


@contextlib.contextmanager
def rewrite_slot(cancel: Cancel | None = None) -> Iterator[bool]:
    """Hold one of the machine's rewrite slots; yields whether it was claimed.

    False only where a skip arrived first, so a file taken off its run settles
    now instead of waiting out an hour of somebody else's encode.
    """
    lock_file = _claim_slot(cancel)
    try:
        yield lock_file is not None
    finally:
        if lock_file is not None:
            lock_file.close()


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
    budget = config.current().MAX_CONCURRENT_REWRITES
    wanted = {f"{_SLOT_PREFIX}{slot}" for slot in range(budget)}
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
    #: Captured client for this execution; only instance_id is persisted.
    arr: Arr | None = None
    run: str | None = None
    #: Durable identity, retained even when the connection cannot be resolved.
    instance_id: str = ""

    def __post_init__(self) -> None:
        if self.arr:
            object.__setattr__(self, "instance_id", self.arr.instance_id)

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


def _modified(
    plan: Plan, bytes_before: int | None, bytes_after: int | None, *, in_place: bool = False
) -> dict:
    """What the rewrite did: when, the sizes either side, and the streams it
    moved.

    Kept in :mod:`trackstarr.rewrites` because the file passes from here on:
    without it a rewritten file is a Passed one, indistinguishable from a file
    the rules never touched. The reasons in prose are the event's, and the
    history page reads them there; a library view has the two track lists.
    """
    record = {"at": events.timestamp(), **_before(plan)}
    if not in_place and any(stream.dv_strip for stream in plan.streams):
        record["dv_removed"] = True
    if bytes_before and bytes_after:
        record |= {"bytes_before": bytes_before, "bytes_after": bytes_after}
    return record


def _rejudged(job: Job, plan: Plan, key: FileKey | None) -> Rewritten | None:
    """The file a rewrite has just published, judged again.

    Without this a rewritten title reads as unchecked until the next sweep. One
    probe, through :func:`process` in report mode so verdicts are reached in
    one place; that call never rewrites, so it cannot recurse. None when the
    file has gone or the probe would not read it.
    """
    if key is None:
        return None
    judged = process(replace(job, path=plan.out_path), dry_run=True, policy=plan.policy)
    if judged.status is Status.FAILED:
        # The probe failed, not the rewrite, which verified its output. Storing
        # "Failed" against a checked file would be wrong.
        log.warning("could not judge %s after rewriting it: %s", plan.out_path, judged.detail)
        return None
    return Rewritten(plan.out_path, key, verdict_of(judged))


def _file_size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def changed_tracks(plan: Plan) -> dict:
    """What the rewrite comes to, in the three fields a card and a queue row
    carry. Empty tallies are dropped, so a plan that only writes a tag records
    none of them."""
    tally = changes(planned_tracks(plan), plan.tracks)
    told = {"adds": tally.adds, "rebuilds": tally.rebuilds, "drops": tally.drops}
    return {name: value for name, value in told.items() if value}


def _event_fields(
    job: Job, plan: Plan, source: str, seconds: float, waited: float = 0.0
) -> dict:
    """What every outcome of one plan records, whichever tool applied it."""
    return {
        "run": job.run,
        "source": source,
        "config_id": plan.policy.digest(),
        "reasons": plan.reasons,
        "rules": sorted(plan.rules),
        "seconds": round(seconds, 1),
        # Only the time spent working scales with the file; the estimates are
        # calibrated from it.
        "waited": round(waited, 1) or None,
        "duration": round(plan.src_duration, 1) or None,
    }


def _tag_only(plan: Plan) -> tuple[int, str] | None:
    """The language tag that is the whole of this plan, as (stream, code).

    mkvpropedit writes one in a second where ffmpeg copies the file to do it,
    so a plan ordering nothing else is worth doing the cheap way. Anything else
    orders a rewrite, which carries the tag for free.
    """
    if plan.rules != {"tag_original"}:
        return None
    # A copy, so src is the index the file itself uses.
    for out in plan.streams:
        if out.kind == "audio" and not out.encode and out.lang:
            return out.src, out.lang
    return None


def _tag_in_place(
    job: Job,
    plan: Plan,
    tag: tuple[int, str],
    source: str,
    cancel: Cancel | None = None,
    observation: Observation | None = None,
) -> ProcessResult | None:
    """Write the plan's one language tag with mkvpropedit, or None to rewrite.

    None where the file cannot be edited in place at all, an MP4, a hardlink,
    or an image without mkvtoolnix. The rewrite writes the same tag, at the
    price of copying the file to do it.
    """
    index, lang = tag
    # Taken before the editor's own lock, and before the file is touched, so
    # the probe that read it cannot publish over what this writes.
    if observation is not None and not observation.changing(
        job.path, stopped=cancel.stopped if cancel else None
    ):
        return ProcessResult(Status.DEFERRED, plan, STOPPED_BEFORE_START)
    bytes_before = _file_size(job.path)
    started = time.monotonic()
    written = mkvtag.write_lang(job.path, index, lang, cancel.commit if cancel else None)
    if written.status is mkvtag.Outcome.REFUSED:
        log.info("cannot tag %s in place, rewriting instead: %s", job.path, written.detail)
        if observation is not None:
            # The rewrite can wait hours for a slot; holding the file all that
            # time would stall every read of it behind an edit that never was.
            observation.released()
        return None
    if written.status is mkvtag.Outcome.STOPPED:
        # Not a refusal: rewriting instead would write the tag the skip just
        # stopped, the slow way.
        log.info("tagging %s was stopped before it began", job.path)
        return ProcessResult(Status.DEFERRED, plan, written.detail)
    if written.status is mkvtag.Outcome.UNCHANGED:
        # Something else wrote the tag between the plan's probe and here, so
        # the file is as the plan wanted it and none of it is ours to record.
        log.info("%s already carries %s", job.path, lang)
        return ProcessResult(Status.CONFORM, plan)
    if observation is not None:
        # Written or half-written: a failure can still have reached the header.
        observation.changed()
    fields = _event_fields(job, plan, source, time.monotonic() - started)
    if written.status is mkvtag.Outcome.FAILED:
        log.warning("tagging %s failed: %s", job.path, written.detail)
        events.record("failed", path=job.path, detail=written.detail, **fields)
        return ProcessResult(Status.FAILED, plan, written.detail)
    log.info("tagged %s in place: %s", job.path, "; ".join(plan.reasons))
    bytes_after = _file_size(job.path)
    # No incidental, since nothing was rewritten for a ride-along to ride with.
    # in_place keeps the line out of the rewrite speeds, see estimate._worked.
    events.record(
        "modified",
        path=job.path,
        in_place=True,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
        **fields,
    )
    if job.arr and job.item_id:
        job.arr.rescan(job.item_id)
    refresh_servers(job.path)
    key = cache_key(job.path, job.lang)
    rewrites.record(job.path, key, _modified(plan, bytes_before, bytes_after, in_place=True))
    return ProcessResult(Status.MODIFIED, plan, became=_rejudged(job, plan, key))


def effective_dry_run(dry_run: bool, path: str = "") -> bool:
    """Whether a run is dry, given the caller, REWRITE_MODE and any pause.

    ``report`` latches over every caller, ``sweep --apply`` included, and a
    pause does the same for the one title; see :mod:`trackstarr.pauses`.
    """
    return (
        dry_run or config.current().REWRITE_MODE == "report" or pauses.paused(path) is not None
    )


def _claim(
    observation: Observation | None, plan: Plan, cancel: Cancel | None
) -> Callable[[], bool] | None:
    """What the executor asks before it publishes: ownership of both files the
    rename touches, so no older probe can book a verdict over the result."""
    if observation is None:
        return None

    def claimed() -> bool:
        if not observation.changing(
            plan.path, plan.out_path, cancel.stopped if cancel else None
        ):
            return False
        # Asked at the rename, so from here what is stored for either path
        # describes a file that is on its way out.
        observation.changed()
        return True

    return claimed


def process(
    job: Job,
    dry_run: bool,
    source: str = "webhook",
    *,
    policy: Policy | None = None,
    cancel: Cancel | None = None,
    observation: Observation | None = None,
) -> ProcessResult:
    """Plan one file and, unless dry_run, rewrite it.

    ``source`` labels the history entry a rewrite attempt leaves. Probe
    failures leave none: they would recur every sweep. The supplied policy
    lasts through the output recheck; direct calls snapshot current settings.
    ``cancel`` is the queued phase's kill switch, where the work came from a
    queue at all, and ``observation`` the caller's read of the file, which
    every mutation here takes ownership through.
    """
    policy = policy or Policy.from_config()
    pause = pauses.paused(job.path)
    dry_run = effective_dry_run(dry_run, job.path)
    try:
        plan = build_plan(job.path, job.lang, policy)
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
        # A paused file is a pending file nothing picked up, which is what a
        # reporting run already produces. The detail is the only difference,
        # and it is what the run row and pending.tsv say instead of the plan.
        if pause:
            log.info("not rewriting %s: %s", job.path, pause.describe())
            return ProcessResult(Status.PENDING, plan, pause.describe())
        log.info("would rewrite %s: %s", job.path, describe(plan))
        return ProcessResult(Status.PENDING, plan)

    # The plan was a read; this is where the file starts changing. A skip that
    # arrived during the probe stops here, and one arriving after this is the
    # gate's to decide.
    if cancel is not None and cancel.stopped():
        log.info("not rewriting %s: the file was skipped while it was planned", job.path)
        return ProcessResult(Status.DEFERRED, plan, STOPPED_BEFORE_START)

    if observation is not None and not observation.watch(
        plan.out_path, cancel.stopped if cancel else None
    ):
        return ProcessResult(Status.DEFERRED, plan, STOPPED_BEFORE_START)

    # Before the slot claim, since a header write is not an encode and queueing
    # it behind one would be most of an hour to spend on a second of work.
    if (tag := _tag_only(plan)) and (
        tagged := _tag_in_place(job, plan, tag, source, cancel, observation)
    ):
        return tagged

    log.info("rewriting %s: %s", job.path, describe(plan))
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
        # The slot's flock is released on the way out, even if this raises.
        with rewrite_slot(cancel) as claimed:
            waited = time.monotonic() - started
            if not claimed:
                outcome, detail = Outcome.DEFERRED, STOPPED_BEFORE_START
            else:
                runs.stage(job.run, job.path, runs.ENCODING, plan.src_duration)
                outcome, detail = apply_plan(
                    plan,
                    functools.partial(runs.progress, job.run, job.path),
                    # Verify and publish are not the encode; a bar left at full
                    # would say the file is still being written.
                    functools.partial(runs.stage, job.run, job.path, runs.FINISHING),
                    cancel,
                    _claim(observation, plan, cancel),
                )
    # A corrupt result, a source deleted mid-job, WORK_DIR gone. One file must
    # not take the rest of a sweep with it.
    except (ProbeError, OSError) as err:
        outcome, detail = Outcome.FAILED, str(err)
    finally:
        # For the paths that never reached on_encoded.
        runs.settle(job.run, job.path)
    event_fields = _event_fields(job, plan, source, time.monotonic() - started, waited)
    if outcome is Outcome.APPLIED:
        bytes_after = _file_size(plan.out_path)
        events.record(
            "modified",
            path=plan.out_path,
            # Only for a remux; None is dropped.
            from_path=plan.path if plan.out_path != plan.path else None,
            incidental=plan.incidental,
            incidental_rules=sorted(plan.incidental_rules),
            **changed_tracks(plan),
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            **event_fields,
        )
        if job.arr and job.item_id:
            job.arr.rescan(job.item_id)
        # out_path, not job.path: a remux publishes under a new extension.
        refresh_servers(plan.out_path)
        # Keyed before the re-probe, as a sweep keys its verdicts, so a change
        # landing under the probe leaves a stale record rather than a wrong one.
        written = cache_key(plan.out_path, job.lang)
        rewrites.record(plan.out_path, written, _modified(plan, bytes_before, bytes_after))
        if plan.out_path != plan.path:
            rewrites.drop(plan.path)
        return ProcessResult(Status.MODIFIED, plan, became=_rejudged(job, plan, written))
    if outcome is Outcome.DEFERRED:
        log.info("deferred %s: %s", job.path, detail)
        # Recorded because a file that defers every pass leaves no other trace.
        # A cancelled one has the skip that asked for it.
        if cancel is None or not cancel.stopped():
            events.record("deferred", path=job.path, detail=detail, **event_fields)
        return ProcessResult(Status.DEFERRED, plan, detail)
    log.warning("rewrite of %s failed: %s", job.path, detail)
    events.record("failed", path=job.path, detail=detail, **event_fields)
    return ProcessResult(Status.FAILED, plan, detail)
