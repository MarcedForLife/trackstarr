"""Application coordination between scheduling, reporting and durable controls.

The scheduler owns dispatch eligibility. Service-pause commands serialize their
disk writes here, without holding the scheduler or reporting locks during I/O.
Run opening, admission, accounting, stop/skip and retirement share the scheduler
boundary; audits, kills and notifications happen once its lock is released.
Startup and shutdown are here too, so one place decides when a process may
admit work and when it has stopped.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from . import (
    config,
    events,
    library,
    notify,
    paths,
    pauses,
    queue_view,
    runlog,
    runs,
    sweep_cache,
    work,
)
from .executor import Cancel
from .state import write_json
from .status import Status

log = logging.getLogger(__name__)
PAUSED_FILE = "paused.json"


class ConflictError(Exception):
    """A queue command cannot run against files another command is holding."""


@dataclass(frozen=True)
class Pause:
    """Actor/time metadata, protected by the scheduler condition."""

    by: str = ""
    at: str = ""


_pause = Pause()
# Lock order is pause writer -> scheduler -> registry. Ordinary dispatch and
# snapshots never acquire the writer lock and remain available during disk I/O.
_pause_writer = threading.Lock()


def _paused_path() -> str:
    return os.path.join(config.STATE_DIR, PAUSED_FILE)


def _read_flag() -> dict | None:
    try:
        with open(_paused_path()) as flag_file:
            record = json.load(flag_file)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as err:
        log.warning("ignoring unreadable %s: %s", _paused_path(), err)
        return None
    if not isinstance(record, dict) or not record.get("paused"):
        return None
    return record


def paused_on_disk() -> bool:
    """Read the listener's durable flag from a separate CLI process."""
    return _read_flag() is not None


def paused() -> bool:
    with work.scheduler.condition:
        return work.scheduler.paused


def reset_paused() -> None:
    """Reset in-memory pause state at a quiescent test/process boundary."""
    global _pause
    with _pause_writer, work.scheduler.condition:
        work.scheduler.set_paused(False)
        _pause = Pause()
        work.scheduler.condition.notify_all()


def load_paused() -> None:
    """Restore pause state before starting dispatch. Never raises."""
    global _pause
    with _pause_writer:
        record = _read_flag()
        if record is None:
            return
        metadata = Pause(str(record.get("by") or ""), str(record.get("at") or ""))
        with work.scheduler.condition:
            work.scheduler.set_paused(True)
            _pause = metadata
            work.scheduler.condition.notify_all()
    log.warning(
        "processing is paused (since %s); nothing will be swept or rewritten until it resumes",
        metadata.at or "an earlier run",
    )


def _set_paused(on: bool, by: str) -> bool:
    global _pause
    with _pause_writer:
        with work.scheduler.condition:
            if not work.scheduler.set_paused(on):
                return False
            _pause = Pause(by, events.timestamp()) if on else Pause()
            record = {"paused": on, "by": _pause.by, "at": _pause.at}
            work.scheduler.condition.notify_all()
        try:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            write_json(_paused_path(), record)
        except OSError as err:
            # A failed restart-memory write must not undo the live command.
            log.warning("could not persist the pause flag: %s", err)
        if on:
            events.record("paused", by=by or None)
        else:
            events.record("resumed", by=by or None)
        notify.publish(notify.RUNS)
    log.log(
        logging.WARNING if on else logging.INFO,
        "processing %s%s",
        "paused" if on else "resumed",
        f" by {by}" if by else "",
    )
    return True


def pause(by: str = "") -> bool:
    """Stop new work; active rewrites finish. Return whether state changed."""
    return _set_paused(True, by)


def resume(by: str = "") -> bool:
    """Allow new work and wake dispatch. Return whether state changed."""
    return _set_paused(False, by)


def hold(run_id: str | None = None) -> bool:
    """Wait out a pause, giving up if the run was stopped in the meantime.

    Both conditions are the scheduler's, so a stop arriving mid-pause wakes the
    thread rather than being sat out until the resume.
    """
    scheduler = work.scheduler
    with scheduler.condition:
        scheduler.condition.wait_for(lambda: not scheduler.paused or scheduler.stopping(run_id))
        return not scheduler.stopping(run_id)


def under_media(path: str) -> bool:
    """Whether a path lies in a configured library, including its root.

    Both sides canonical, so a spelling that would be stored under one name
    cannot be authorized under another.
    """
    candidate = paths.canonical(path)
    return any(
        paths.within(paths.canonical(media))(candidate) for media in config.current().MEDIA_DIRS
    )


@dataclass(frozen=True)
class Outcome:
    """What a queue command did, before an endpoint writes it to the wire."""

    changed: int
    #: Given only by a reorder, and only where something moved.
    undo: str = ""


@contextmanager
def _conflicts() -> Iterator[None]:
    """Refuse in the application's own terms where a store or the scheduler does.

    Both a reservation clash and a full pause store leave the queue as it was,
    so an endpoint has one refusal to map rather than two stores' exceptions.
    """
    try:
        yield
    except (work.ConflictError, pauses.CapacityError) as err:
        raise ConflictError(str(err)) from err


def pause_selection(keys: set[tuple[str, str]], seconds: float, by: str = "") -> Outcome:
    """Reserve queue entries, persist item pauses, then commit their skips."""
    with _conflicts(), work.scheduler.pause_selection(keys) as selected:
        if any(not under_media(path) for _, path in selected):
            raise ValueError("that file is not in a swept library")
        pauses.place_many([(path, "", "") for _, path in selected], seconds, by)
    _announce_skipped(selected, by)
    return Outcome(len(selected))


def _stopped_where(where: str, held: runs.Active | None) -> str:
    """Where the file was when it was taken off, in the line's words."""
    if where != "active":
        return "taken off the run before a worker reached it"
    if held is not None and held.stage == runs.WAITING:
        return "stopped while it waited for a rewrite slot"
    if held is not None and held.stage == runs.ENCODING and held.total:
        percent = round(100 * held.done / held.total)
        return f"stopped {percent}% into the rewrite, nothing written"
    return "stopped while it was being checked"


def _record_skipped(
    run_id: str, path: str, by: str, where: str, held: runs.Active | None = None
) -> None:
    """The one line a skip leaves; the worker writes none for a file it gave
    up on request."""
    events.record(
        "skipped",
        run=run_id,
        path=path,
        by=by or None,
        where=where,
        detail=_stopped_where(where, held),
        seconds=round(time.time() - held.since, 1) if held else None,
        **library.plan_fields(path),
    )


def _announce_skipped(selected: Sequence[tuple[str, str]], by: str) -> None:
    """Publish and audit once the scheduler has committed the skips."""
    notify.publish(notify.RUNS)
    for run_id, path in selected:
        _record_skipped(run_id, path, by, "waiting")


def skip(keys: set[tuple[str, str]], by: str = "") -> Outcome:
    """Take waiting files off their runs, auditing each one that moved."""
    with _conflicts():
        selected = work.scheduler.skip(keys)
    _announce_skipped(selected, by)
    return Outcome(len(selected))


def promote(keys: set[tuple[str, str]]) -> Outcome:
    """Move waiting files to the front, with the token that puts them back."""
    with _conflicts():
        return Outcome(*work.scheduler.move_top(keys))


def restore(token: object) -> bool:
    """Put a reorder back. False where a later one replaced its token."""
    with _conflicts():
        return work.scheduler.restore(token)


def queue_page(query: str = "", offset: int = 0, limit: int = 50) -> dict:
    """One page of the whole queue, numbered against the capture it came from."""
    return work.scheduler.snapshot(query, offset, limit)


def skip_file(run_id: str, path: str, by: str = "") -> tuple[str, int]:
    """Take one named file off a run, killing its rewrite if a worker has it.

    Answers where the file was and how many rewrites were signalled, or
    ``("", 0)`` where the run has no live claim on it. A skip lasts as long as
    the run; :mod:`trackstarr.pauses` is what outlives it.
    """
    with _conflicts():
        where, cancel = work.scheduler.skip_file(run_id, path)
    if cancel is None:
        return "", 0
    # Read before the signal: once told, the worker lets the file go.
    held = runs.holding(run_id, path) if where == "active" else None
    # Published before the kill, so a page refetching mid-kill is already told
    # why the file stopped.
    notify.publish(notify.RUNS)
    # By phase, not by name: this file can already be claimed again elsewhere.
    killed = runs.abort_phase(cancel) if where == "active" else 0
    _record_skipped(run_id, path, by, where, held)
    log.info("%s skipped on run %s%s", path, run_id, f" by {by}" if by else "")
    return where, killed


def stop(run_id: str) -> bool:
    """Ask a run to wind up after the file it is on. Whether it was going.

    Not a kill: a sweep still writes its cache and its summary, and the rewrite
    in flight still publishes.
    """
    if not work.scheduler.stop(run_id):
        return False
    # Published at once: every other tab shows a Stop button that no longer
    # applies.
    notify.publish(notify.RUNS)
    log.info("run %s asked to stop", run_id)
    return True


def stop_all() -> int:
    """Ask every open run to stop; how many were asked this time."""
    asked = work.scheduler.stop_all()
    if asked:
        notify.publish(notify.RUNS)
        log.info("every run asked to stop (%d)", len(asked))
    return len(asked)


@contextmanager
def group(run_id: str, priority: bool = False) -> Iterator[work.Group]:
    """Own every phase of a walk, draining them before it releases its cache.

    An exception stops the run first, so draining does not wait out the backlog
    the walk had already queued.
    """
    batch = work.Group(work.scheduler, run_id, priority)
    try:
        yield batch
    except BaseException:
        stop(run_id)
        batch.drain()
        raise
    else:
        if batch.drain():
            raise ValueError("task group left files awaiting a decision")


@dataclass(frozen=True)
class Activity:
    """Service pause, run controls, telemetry and queue rows from one boundary.

    Every projection below reads this one capture, so a claim landing between
    two of them cannot hide a file from both. Item pauses and catalogue labels
    are separate stores with their own freshness: enrich with them afterwards,
    never under the scheduler's condition.
    """

    paused: bool
    paused_by: str
    paused_at: str
    controls: Mapping[str, runs.Control]
    telemetry: runs.Capture
    queue: queue_view.Snapshot

    def overview(self) -> dict:
        """Every run, with the head of the queue and what it is owed."""
        waiting, working = self.telemetry.workload()
        return {
            "paused": self.paused,
            "paused_by": self.paused_by,
            "paused_at": self.paused_at,
            **self.telemetry.as_json(self.controls),
            "queue": waiting,
            "working": working,
            "queue_preview": self.queue.page(limit=3)["items"],
        }

    def for_folders(self, folders: Sequence[str]) -> dict:
        """One title's files: what a thread holds and what is still waiting.

        A title held in two instances has two folders; their rows merge in
        the queue's own order, so it reads as one title. A file between its
        probe and its rewrite is in neither list. It has no worker and the
        walk has not yet said whether there is a rewrite to come.
        """
        queued = [row for folder in folders for row in self.queue.for_folder(folder)]
        queued.sort(key=lambda row: row["position"])
        return {
            "queued": queued,
            "active": [
                row
                for folder in folders
                for row in self.telemetry.active_under(folder, self.controls)
            ],
        }


def activity() -> Activity:
    """Take controls, telemetry and queue rows under the one scheduler boundary.

    Lock order is scheduler -> reporting. Filtering, formatting and enrichment
    all happen on the capture, after the condition is released.
    """
    with work.scheduler.condition:
        return Activity(
            work.scheduler.paused,
            _pause.by,
            _pause.at,
            work.scheduler.control_view(),
            runs.capture(),
            work.scheduler.capture(),
        )


def snapshot() -> dict:
    """The overview's activity: every run, the queue's head and the counts."""
    return activity().overview()


def title_work(folders: Sequence[str]) -> dict:
    """What one title has queued and in progress across its folders, and what
    is paused."""
    return {**activity().for_folders(folders), "pauses": pauses.as_json()}


@dataclass
class ImportResult:
    """One import's accounting, including failure before processing begins."""

    status: Status = Status.FAILED
    detail: str = ""
    dropped: bool = False


@contextmanager
def import_result(run: str | None, path: str) -> Iterator[ImportResult]:
    """Settle exactly once, after processing, publication and cleanup finish.

    The default covers exceptions before there is a verdict. Once processing
    returns a verdict, a later publication failure must not book another one.
    """
    result = ImportResult()
    try:
        yield result
    finally:
        if result.dropped:
            drop(run)
        else:
            tally(run, str(result.status), path=path, detail=result.detail)


def submit_import(
    run: str | None,
    path: str,
    call: Callable[[], object],
    *,
    on_terminal: Callable[[], None] | None = None,
    cancel: Cancel | None = None,
    discovery: bool = False,
) -> work.Phase:
    """Admit/count atomically; release source ownership after terminal cleanup.

    The callback runs outside the scheduler lock, even for cancelled futures,
    before phase settlement. Future cancellation alone does not release a job.
    ``cancel`` belongs to this phase. ``discovery`` admits a priority probe
    whose callback must complete the file or continue it onto the rewrite lane.
    """
    return work.scheduler.submit(
        run,
        path,
        "probe" if discovery else "work",
        call,
        priority=discovery,
        counted=True,
        on_terminal=on_terminal,
        cancel=cancel,
    )


def open_run(
    run_id: str,
    kind: str,
    *,
    dry_run: bool = False,
    label: str = "",
    filling: bool = False,
) -> runs.Run:
    """Open admission and reporting together before any worker can claim."""
    with work.scheduler.condition:
        created = work.scheduler.open_run(run_id)
        record = runs.open_run(run_id, kind, dry_run=dry_run, label=label, filling=filling)
    if created:
        notify.publish(notify.RUNS)
    return record


def seal(run_id: str) -> None:
    """Finish input; retire only once accounting and admitted tasks drain."""
    with work.scheduler.condition:
        runs.seal(run_id)
        retired = work.scheduler.retire_run(run_id)
    if retired:
        notify.publish(notify.RUNS)


def close_run(run_id: str) -> None:
    """Close admission now, retaining reporting until the last task releases."""
    with work.scheduler.condition:
        retired = work.scheduler.close_run(run_id)
    if retired:
        notify.publish(notify.RUNS)


def tally(
    run_id: str | None,
    status: str,
    path: str = "",
    detail: str = "",
    cached: bool = False,
) -> None:
    """Book a result atomically against admission and retirement."""
    if run_id is None:
        return
    with work.scheduler.condition:
        runs.tally(run_id, status, path, detail, cached)
        retired = work.scheduler.retire_run(run_id)
    if retired:
        notify.publish(notify.RUNS)
    else:
        runs.announce_queue(run_id)


def drop(run_id: str | None) -> None:
    """Settle a stopped import without releasing its still-live admission."""
    if run_id is None:
        return
    with work.scheduler.condition:
        runs.drop(run_id)
        retired = work.scheduler.retire_run(run_id)
    if retired:
        notify.publish(notify.RUNS)
    else:
        runs.announce_queue(run_id)


class Producers:
    """The background operations that may still hand work to the scheduler.

    A lease covers one whole operation, listing, admission, booking and the
    final cache and report writes, so the process waits for the writes and not
    only for the tasks. A walk that admits nothing holds one all the same.
    """

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.open = True
        self.held = 0

    def producing(self) -> bool:
        with self.condition:
            return self.open

    @contextmanager
    def lease(self) -> Iterator[bool]:
        """Yield whether the operation may run, holding the process open if so."""
        with self.condition:
            leased = self.open
            self.held += leased
        try:
            yield leased
        finally:
            if leased:
                with self.condition:
                    self.held -= 1
                    self.condition.notify_all()

    def close(self) -> None:
        with self.condition:
            self.open = False
            self.condition.notify_all()

    def reopen(self) -> None:
        with self.condition:
            self.open = True
            self.held = 0

    def quiet(self, timeout: float) -> bool:
        """Wait for every lease to be released. Whether they were."""
        with self.condition:
            return self.condition.wait_for(lambda: not self.held, timeout)


#: Closed while the process winds down, so the scheduled sweep and the parked
#: recheck stop opening runs the drain would then have to wait out.
_producers = Producers()

#: How long a shutdown waits for admitted work. A stopped run's queued files
#: reach their early exit at once; this is the allowance for a live rewrite.
DRAIN_SECONDS = 30.0


def producing() -> bool:
    """Whether the background loops should still hand work to the scheduler."""
    return _producers.producing()


def producer() -> AbstractContextManager[bool]:
    """Hold the process open for one background operation.

    Yields whether it may run: False once shutdown has begun, which is the
    caller's cue to do nothing at all rather than to start and be cut off. An
    operation already holding a lease can still be refused an admission, since
    a lease keeps the process waiting rather than keeping the queue open.
    """
    return _producers.lease()


def launch(call: Callable[..., object], args: tuple = (), *, name: str) -> bool:
    """Lease before launching, releasing after completion or a failed start."""
    lease = producer()
    if not lease.__enter__():
        lease.__exit__(None, None, None)
        return False

    def run() -> None:
        try:
            call(*args)
        finally:
            lease.__exit__(None, None, None)

    try:
        threading.Thread(target=run, daemon=True, name=name).start()
    except BaseException:
        lease.__exit__(None, None, None)
        raise
    return True


def startup() -> None:
    """Restore the durable pause before dispatch, then let workers claim."""
    load_paused()
    work.scheduler.start()


def wake() -> None:
    """Apply newly saved worker budgets and wake whatever the pause holds."""
    work.scheduler.start()


def shutdown(timeout: float = DRAIN_SECONDS) -> bool:
    """Stop producing work, drain what was admitted, then stop dispatch.

    Whether everything finished. Runs are asked to stop first, so a walk's
    backlog reaches its own early exit instead of being waited out. A rewrite
    already running finishes: killing one is still an explicit abort. One
    deadline covers both waits, and a false answer means work was left behind
    rather than that it was abandoned quietly. Deferred verdicts are written
    either way; a timeout is no reason to drop what did finish.
    """
    with work.scheduler.condition:
        _producers.close()
        work.scheduler.close()
    stop_all()
    deadline = time.monotonic() + timeout
    # Producers first. One of them can still be admitting, so a queue that
    # looks empty is not yet the end of the work.
    finished = _producers.quiet(_left(deadline))
    drained = work.scheduler.drain(_left(deadline))
    work.scheduler.shutdown()
    # After the drain, so the verdicts the last workers published go with it.
    sweep_cache.flush()
    if not (finished and drained):
        log.warning(
            "stopped with work in flight: %d producer(s) still going, %s",
            _producers.held,
            work.scheduler.outstanding(),
        )
    return finished and drained


def _left(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def reset(timeout: float = 0.0) -> None:
    """Install fresh state only after all workers and producers have settled.

    A timed-out shutdown leaves its owned context intact; the caller can retry
    once the outstanding operation finishes. Pending, undispatched test tasks
    can be discarded with work.reset at the test boundary.
    """
    if not shutdown(timeout):
        raise RuntimeError("cannot reset while work is still in flight")
    work.reset()
    runs.reset()
    runlog.forget()
    reset_paused()
    _producers.reopen()
