"""Run progress and processing telemetry.

:mod:`trackstarr.events` records the past; this describes the present and dies
with the process. A run here is the run an event carries, named by
:func:`trackstarr.events.run_id`.

The registry is per-process, so a ``docker exec trackstarr sweep`` beside a
running ``serve`` is invisible here. Stop, skip and pause controls belong to
:mod:`trackstarr.work` and :mod:`trackstarr.lifecycle`; a read passes their
state in as a :class:`Control`.
"""

import collections
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from . import estimate, events, notify, paths, runlog
from .arr import source_name
from .executor import Cancel, running_count, terminate_phase, terminate_running

log = logging.getLogger(__name__)

#: The run types: a library walk, a delivery from an *arr (Radarr or
#: Sonarr), and a re-check of chosen titles.
SWEEP = "sweep"
IMPORT = "import"
RECHECK = "recheck"

#: The types that update the sweep cache. Cache maintenance waits for them;
#: title re-checks may run alongside an existing walk.
CACHE_TYPES = (SWEEP, RECHECK)

#: What the thread holding a file is doing. Waiting for a slot and encoding are
#: both minutes, so the page must be told which, or a bar at zero reads as a
#: stalled encode.
WORKING = "working"
WAITING = "waiting"
ENCODING = "encoding"

#: How many released files a run keeps, so the overview can show what each
#: came to.
_RECENT_FILES = 40

#: How many waiting files a snapshot names. Enough to reach past the ones a
#: reader can see without carrying a whole night's queue in every poll. The
#: scheduler reads it to bound the rows it hands over.
UPCOMING = 20

#: How much of a verdict's detail a row carries. The rest is in the history.
_DETAIL_MAX = 160


@dataclass
class Active:
    """One file a thread is working on, and how far along it is."""

    #: time.time() when it was picked up.
    since: float
    stage: str = WORKING
    #: The file's running time and how much of it the rewrite has written, in
    #: seconds. Zero until an encode starts.
    total: float = 0.0
    done: float = 0.0
    #: ffmpeg's multiple of realtime, which varies tenfold with machine load.
    speed: float = 0.0
    #: The estimate the file was queued under, in seconds. Stands in for the
    #: readout until ffmpeg reports one; zero where nothing estimated.
    expected: float = 0.0


@dataclass
class Done:
    """One file a run has released, and what it came to.

    Filled in by two threads: :func:`finish` on the worker, then :func:`tally`
    a moment later, which for a sweep is the walking thread.
    """

    path: str
    #: How long the worker had it. Zero for a file nothing picked up.
    seconds: float = 0.0
    #: Empty between release and booking, and for a file a stopped run dropped.
    status: str = ""
    #: What a rewrite did, or why one did not happen.
    detail: str = ""


@dataclass
class Run:
    """One run while it is going. Mutable, guarded by the module lock."""

    id: str
    type: str
    #: time.time() when it started.
    started: float
    dry_run: bool = False
    #: Stable source identity for an import; its name is resolved for the API.
    instance_id: str = ""
    #: Display text for a re-check (one title name or a count).
    label: str = ""
    total: int = 0
    done: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    #: Files being worked this second.
    active: dict[str, Active] = field(default_factory=dict)
    #: Released files, oldest first, up to :data:`_RECENT_FILES`, so a verdict
    #: reached while nobody was looking is still there to read.
    recent: collections.deque[Done] = field(
        default_factory=lambda: collections.deque(maxlen=_RECENT_FILES)
    )
    #: Still listing files. Until it clears, the queued count is a floor.
    walking: bool = False
    #: Still being handed files. Without it a delivery whose first file
    #: finished before its third was queued would close and reopen as two runs.
    filling: bool = False
    #: time.time() when this run last announced progress. See :func:`_moved`.
    told: float = 0.0


@dataclass(frozen=True)
class Queued:
    """One file a run has work for that no thread has begun, in queue order."""

    path: str
    #: Seconds the rewrite is expected to take; zero for a probe or an import.
    expected: float = 0.0
    skipped: bool = False


@dataclass(frozen=True)
class Control:
    """One run's queue state, copied from the scheduler for a read.

    ``skipped`` holds the skipped active claims; waiting rows carry their own
    flags so a reporting read need not copy a whole skipped backlog.
    ``claimed`` carries the files a worker has taken but may not have opened;
    :func:`begin` drops those. The rest of the queue is a count, a total estimate,
    and the head of it.
    """

    stopping: bool = False
    skipped: frozenset[str] = frozenset()
    claimed: tuple[Queued, ...] = ()
    queued: int = 0
    expected: float = 0.0
    upcoming: tuple[Queued, ...] = ()


#: What a run with no scheduler control reads as: a walk whose work has drained.
_UNCONTROLLED = Control()

_runs: dict[str, Run] = {}
_lock = threading.Lock()

#: When this process came up. A run that vanishes across a change of this went
#: with the process, not to completion.
_UP = time.time()


def reset() -> None:
    """Put reporting back to how a fresh process finds it, stamp and all."""
    global _UP
    with _lock:
        _runs.clear()
        _UP = time.time()


#: How often a run announces progress. A walk books verdicts as fast as it can
#: stat files; two seconds is as fast as any page draws.
_MOVED_SECONDS = 2.0

#: How often while only a rewrite's readout is moving. The page extrapolates
#: from ffmpeg's last speed, so these are corrections: often enough to catch a
#: slowing machine, rarely enough not to cost 120 fetches per rewrite.
_DRIFT_SECONDS = 15.0


def _moved(run_id: str, floor: float = _MOVED_SECONDS) -> None:
    """Announce a run's progress, at most once every ``floor`` seconds.

    Takes the lock and publishes outside it. A run already gone says nothing;
    the coordinator publishes retirement.
    """
    now = time.time()
    with _lock:
        run = _runs.get(run_id)
        if run is None or now - run.told < floor:
            return
        run.told = now
    notify.publish(notify.PROGRESS)


def open_run(
    run_id: str,
    run_type: str,
    *,
    dry_run: bool = False,
    label: str = "",
    instance_id: str = "",
    filling: bool = False,
) -> Run:
    """Register a run, or return the one already under that id.

    Reopening is for imports: a file parked behind a seeding download can be
    released days later under the same run id.
    """
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            run = Run(
                run_id,
                run_type,
                time.time(),
                dry_run=dry_run,
                label=label,
                instance_id=instance_id,
            )
            _runs[run_id] = run
        run.filling = run.filling or filling
    return run


def add_file(run_id: str) -> None:
    """One more file for this run to get through.

    Called inside the scheduler's admission boundary, so it never publishes;
    :func:`announce_queue` is what tells the pages, once that lock is released.
    """
    with _lock:
        if run := _runs.get(run_id):
            run.total += 1


def seal(run_id: str) -> None:
    """Report that the producer has handed over all files; never retire here."""
    with _lock:
        if run := _runs.get(run_id):
            run.filling = False


def retire(run_id: str, *, closing: bool = False) -> bool:
    """Remove drained reporting under scheduler -> registry lock order.

    The scheduler must establish that no admission remains before calling.
    Return notification information; never publish while its lock is held.
    """
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return False
        if closing or (
            run.type == IMPORT and not run.filling and not run.active and run.done >= run.total
        ):
            del _runs[run_id]
            return True
    return False


def set_total(run_id: str, total: int) -> None:
    """Set the run's file count, which a sweep only knows after listing."""
    with _lock:
        if run := _runs.get(run_id):
            run.total = total
    _moved(run_id)


def walking(run_id: str, still: bool) -> None:
    """Set whether the run is still finding files. Until it is done, the queued
    sum is a floor."""
    with _lock:
        if run := _runs.get(run_id):
            run.walking = still
    _moved(run_id)


def announce_queue(run_id: str | None) -> None:
    """Publish queue progress after the scheduler releases its condition."""
    if run_id is not None:
        _moved(run_id)


def begin(run_id: str | None, path: str, expected: float = 0.0) -> None:
    """Record that a thread has picked this file up.

    ``expected`` is the seconds the work was queued under, carried by the task
    itself. A None run is accepted throughout: the CLI's fix has none. Nothing
    is recorded for it.
    """
    if run_id is None:
        return
    runlog.attach(run_id, path)
    with _lock:
        if run := _runs.get(run_id):
            run.active[path] = Active(time.time(), expected=expected)
    _moved(run_id)


def stage(run_id: str | None, path: str, name: str, total: float = 0.0) -> None:
    """Record the stage the thread holding this file has moved to.

    ``total`` is the file's running time, known only once probed. Each stage
    resets the readout so a retry is not drawn against the last attempt.
    """
    if run_id is None:
        return
    with _lock:
        run = _runs.get(run_id)
        if run and (active := run.active.get(path)):
            active.stage = name
            active.total = total
            active.done = active.speed = 0.0
    _moved(run_id)


def progress(run_id: str | None, path: str, done: float, speed: float) -> None:
    """Record ffmpeg's progress readout for a rewrite.

    The other end of :data:`trackstarr.executor.ProgressCallback`, called once
    a second per rewrite. The page extrapolates from the last reading, so it is
    told at the slower floor, except the first reading: until then a bar at
    zero reads as stalled.
    """
    if run_id is None:
        return
    first = False
    with _lock:
        run = _runs.get(run_id)
        if run and (active := run.active.get(path)):
            first = active.speed <= 0
            active.done, active.speed = done, speed
    _moved(run_id, _MOVED_SECONDS if first else _DRIFT_SECONDS)


def finish(run_id: str | None, path: str, *, continuing: bool = False) -> None:
    """Record that the thread has released this file. It moves to the recent
    list; the verdict lands on that row in :func:`tally`. A discovery handing
    off to rewriting only releases telemetry, without a completed row."""
    if run_id is None:
        return
    runlog.detach()
    now = time.time()
    with _lock:
        run = _runs.get(run_id)
        if run and (active := run.active.pop(path, None)) and not continuing:
            run.recent.append(Done(path, round(now - active.since, 1)))
    _moved(run_id)


def holding(run_id: str, path: str) -> Active | None:
    """A copy of the worker's progress on a file it still holds; None once
    released."""
    with _lock:
        run = _runs.get(run_id)
        active = run.active.get(path) if run else None
        return replace(active) if active else None


def tally(
    run_id: str,
    status: str,
    path: str = "",
    detail: str = "",
    cached: bool = False,
) -> None:
    """Book one file's verdict against the run's progress and onto its row.

    Separate from :func:`finish` because cached verdicts count towards progress
    without any file being picked up. ``cached`` gets no row: nothing was probed
    or logged, and a warm library is almost entirely these.
    """
    with _lock:
        if run := _runs.get(run_id):
            run.done += 1
            run.counts[status] = run.counts.get(status, 0) + 1
            if path:
                _decide(run, path, status, detail[:_DETAIL_MAX], cached)


def _decide(run: Run, path: str, status: str, detail: str, cached: bool) -> None:
    """Put the verdict on the file's row. Call under the lock.

    Oldest unverdicted row first, since verdicts land in release order. A file
    with no waiting row was never picked up; unless cached, that is a parked
    import and still worth a row.
    """
    for done in run.recent:
        if done.path == path and not done.status:
            done.status, done.detail = status, detail
            return
    if not cached:
        run.recent.append(Done(path, status=status, detail=detail))


def drop(run_id: str) -> None:
    """Take a file off a run that will never reach it.

    A stopped delivery's queued files get no verdict, since nothing opened
    them, but a run whose ``done`` never reaches ``total`` never retires.
    """
    with _lock:
        if run := _runs.get(run_id):
            run.total = max(run.done, run.total - 1)


def cache_holder() -> Run | None:
    """An active run updating the sweep cache, if any.

    Concurrent walks share their verdicts. Cache clearing still waits for all
    walks, so their checkpoints cannot restore entries after a clear.
    """
    with _lock:
        return next((run for run in _runs.values() if run.type in CACHE_TYPES), None)


def workload() -> tuple[int, int]:
    """Files still to be reached, and files being worked on, across every run.

    Counted off the runs, not the work queue, which only knows about imports.
    A job is counted into its run's total when queued, so nothing doubles up.
    """
    with _lock:
        waiting = sum(max(0, run.total - run.done - len(run.active)) for run in _runs.values())
        working = sum(len(run.active) for run in _runs.values())
    return waiting, working


def rewrites() -> int:
    """How many rewrites are encoding this second. Distinct from files being
    worked on: a report-only sweep has probes going and nothing to abort."""
    return running_count()


def abort(path: str = "") -> int:
    """Kill every rewrite under way, or only the one rewriting ``path``; how
    many were signalled.

    Rewrites are staged in WORK_DIR and published by an atomic rename once
    verified, so this costs the encode and never the library file.
    """
    killed = terminate_running(path)
    if killed:
        log.warning("aborted %d rewrite(s) under way%s", killed, f" on {path}" if path else "")
    return killed


def abort_phase(cancel: Cancel) -> int:
    """Kill the rewrite one claimed phase has going; how many were signalled.

    Zero for a phase that never reached ffmpeg, including a probe and a rewrite
    still waiting on its slot. Either way it is marked, so nothing starts after.
    """
    killed = terminate_phase(cancel)
    if killed:
        log.warning("aborted the rewrite of %s", cancel.path)
    return killed


def stamp(when: float) -> str:
    """A moment in the history's ``ts`` format."""
    return events.at(when)


def _still_to_go(active: Active, now: float) -> float:
    """Seconds the thread holding this file has left: from ffmpeg's readout
    during an encode, otherwise from the estimate it was queued under."""
    if active.stage == ENCODING and active.total and active.speed > 0:
        return max(0.0, (active.total - active.done) / active.speed)
    return max(0.0, active.expected - (now - active.since))


def _rewriting_left(run: Run, now: float, waiting: float) -> float | None:
    """Roughly how many wall seconds this run's rewriting has left, or None
    with no rewriting in it.

    A floor: a walking sweep has not found all its work, a delivery competes
    for the same slots, and every estimate is a median.
    """
    working = [_still_to_go(active, now) for active in run.active.values()]
    if not waiting and not any(working):
        return None
    # Spread across the slots, but never under the longest file still going.
    return max(estimate.backlog(waiting + sum(working)), max(working, default=0.0))


def _by_age(run: Run) -> list[tuple[str, Active]]:
    """The files this run's threads hold, longest-running first, so a page with
    room for one shows the file holding everything up."""
    return sorted(run.active.items(), key=lambda item: item[1].since)


def _active_json(path: str, active: Active, now: float, skipped: bool) -> dict:
    """One held file's row. The encode figures are zero for anything else."""
    return {
        "path": path,
        "seconds": round(now - active.since, 1),
        "stage": active.stage,
        "duration": round(active.total, 1),
        "done": round(active.done, 1),
        "speed": round(active.speed, 2),
        # Asked for but not yet given up: the kill and the thread noticing are
        # two moments.
        "skipped": skipped,
    }


def _as_json(run: Run, now: float, control: Control) -> dict:
    # The scheduler still holds a claimed file; the thread working it is the
    # one thing it cannot see, so the rows it hands over are filtered here.
    claimed = [row for row in control.claimed if row.path not in run.active]
    queued = len(claimed) + control.queued
    waiting = sum(row.expected for row in claimed) + control.expected
    return {
        "id": run.id,
        "type": run.type,
        "started": stamp(run.started),
        "seconds": round(now - run.started, 1),
        "dry_run": run.dry_run,
        "label": source_name(run.instance_id) if run.instance_id else run.label,
        "total": run.total,
        "done": run.done,
        "counts": dict(run.counts),
        # Work found but not started, and whether the listing is done. A count
        # under a walk still going is a floor.
        "queued": queued,
        "walking": run.walking,
        # Null where the run has no rewriting in it.
        "rewrite_seconds": _rewriting_left(run, now, waiting),
        "stopping": control.stopping,
        "active": [
            _active_json(path, active, now, path in control.skipped)
            for path, active in _by_age(run)
        ],
        # The head of the queue in the order it will be reached, so a page has
        # something to name when somebody wants one of them left alone. Bounded:
        # a first-night sweep queues thousands and the page shows a handful.
        "upcoming": [
            {"path": row.path, "expected": round(row.expected, 1), "skipped": row.skipped}
            for row in (*claimed, *control.upcoming)[:UPCOMING]
        ],
        # Newest first.
        "recent": [
            {
                "path": done.path,
                "status": done.status,
                "seconds": done.seconds,
                "detail": done.detail,
            }
            for done in reversed(run.recent)
        ],
    }


@dataclass(frozen=True)
class Capture:
    """Detached bounded telemetry, copied under R and formatted after S and R."""

    up: float
    now: float
    runs: tuple[Run, ...]

    def workload(self) -> tuple[int, int]:
        return (
            sum(max(0, run.total - run.done - len(run.active)) for run in self.runs),
            sum(len(run.active) for run in self.runs),
        )

    def as_json(self, controls: Mapping[str, Control]) -> dict:
        rows = [
            _as_json(run, self.now, controls.get(run.id, _UNCONTROLLED)) for run in self.runs
        ]
        rows.sort(key=lambda run: run["started"])
        return {"up_since": stamp(self.up), "runs": rows}

    def active_under(self, folder: str, controls: Mapping[str, Control]) -> list[dict]:
        """The files threads hold inside one folder, each naming its run."""
        inside = paths.within(folder)
        rows: list[dict] = []
        for run in sorted(self.runs, key=lambda run: run.started):
            control = controls.get(run.id, _UNCONTROLLED)
            rows.extend(
                {
                    "run": run.id,
                    **_active_json(path, active, self.now, path in control.skipped),
                    "stopping": control.stopping,
                }
                for path, active in _by_age(run)
                if inside(path)
            )
        return rows


def capture() -> Capture:
    """Copy telemetry while the caller holds S when pairing it with controls."""
    with _lock:
        return Capture(
            _UP,
            time.time(),
            tuple(
                replace(
                    run,
                    counts=dict(run.counts),
                    active={path: replace(active) for path, active in run.active.items()},
                    recent=collections.deque(replace(done) for done in run.recent),
                )
                for run in _runs.values()
            ),
        )


def snapshot(controls: Mapping[str, Control] | None = None) -> dict:
    """Reporting alone; lifecycle captures telemetry and controls together."""
    return capture().as_json(controls or {})
