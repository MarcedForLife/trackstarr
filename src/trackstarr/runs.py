"""What the service is doing right now, and the pause switch.

:mod:`trackstarr.events` records the past; this describes the present and dies
with the process. A run here is the run an event carries, named by
:func:`trackstarr.events.run_id`.

The registry is per-process, so a ``docker exec trackstarr sweep`` beside a
running ``serve`` is invisible here. The pause flag is a file, so a second
process reads the same answer.
"""

import collections
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from . import config, estimate, events, notify
from .executor import running_count, terminate_running
from .state import write_json

log = logging.getLogger(__name__)

#: Where the pause survives a restart. Coming back up sweeping would undo a
#: deliberate pause at the moment nobody is watching.
PAUSED_FILE = "paused.json"

#: The kinds of run: a library walk, a delivery from an *arr (Radarr or
#: Sonarr), and a re-check of chosen titles.
SWEEP = "sweep"
IMPORT = "import"
RECHECK = "recheck"

#: The kinds that rewrite the sweep cache. Both write the whole file, so two at
#: once would undo each other; see :func:`cache_holder`.
CACHE_KINDS = (SWEEP, RECHECK)

#: How often a thread waiting out a pause checks whether its run was stopped.
_HOLD_TICK = 1.0

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
#: reader can see without carrying a whole night's queue in every poll.
_UPCOMING = 20

#: How much of a verdict's detail a row carries. The rest is in the history.
_DETAIL_MAX = 160

#: Log lines kept per file, and files kept at once. Two hundred lines covers a
#: probe, a plan, an ffmpeg command and its stderr.
_LOG_LINES = 200
_LOGGED_FILES = 200


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
    kind: str
    #: time.time() when it started.
    started: float
    dry_run: bool = False
    #: Which *arr sent it, for an import; the titles' name for a re-check.
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
    #: Files found work for that no worker has picked up, with the seconds each
    #: rewrite is expected to take. Only an applying sweep fills it.
    queued: dict[str, float] = field(default_factory=dict)
    #: Still listing files. Until it clears, ``queued`` is a floor.
    walking: bool = False
    #: Asked to stop and winding down. Stays in the registry until it closes so
    #: the page can say why it is emptying.
    stopping: bool = False
    #: Files somebody took off this run. Dies with it: a skip is "not in this
    #: pass", where :mod:`trackstarr.holds` is "not for a while".
    skipped: set[str] = field(default_factory=set)
    #: Still being handed files. Without it a delivery whose first file
    #: finished before its third was queued would close and reopen as two runs.
    filling: bool = False
    #: time.time() when this run last announced progress. See :func:`_moved`.
    told: float = 0.0


_runs: dict[str, Run] = {}
_lock = threading.Lock()

#: When this process came up. A run that vanishes across a change of this went
#: with the process, not to completion.
_UP = time.time()

#: Set while work may start; cleared is paused. An Event so a resume wakes
#: every held thread at once.
_running = threading.Event()
_running.set()

#: Who paused it and when. Written under _lock, alongside the Event.
_paused_by = ""
_paused_at = ""

#: Log lines per (run, file), and which file each thread holds. Under their own
#: lock, since a log handler waiting on the registry lock would deadlock the
#: first caller that logs while holding it.
_lines: dict[tuple[str, str], collections.deque[str]] = {}
_held: dict[int, tuple[str, str]] = {}
_log_lock = threading.Lock()

#: The console's format, so the browser shows the same line. See
#: :func:`trackstarr.cli.main`.
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_LOG_TIME = "%H:%M:%S"


class _FileLog(logging.Handler):
    """Keep every line a worker logs against the file it was working on.

    Keyed by thread: a file is probed, planned and rewritten on one, so the
    ``log.info`` calls across the package need not know about runs.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # None with logging.logThreads off, which leaves nothing to key on.
        if record.thread is None:
            return
        with _log_lock:
            buffer = _lines.get(_held.get(record.thread, ("", "")))
        # Formatted outside the lock; deque.append is atomic.
        if buffer is not None:
            buffer.append(self.format(record))


_capturing = False


def capture_logs() -> None:
    """Start keeping worker log lines for the overview to read back per file.

    Installed by the service, not the CLI: a ``docker exec trackstarr sweep``
    has nobody to read them.
    """
    global _capturing
    if _capturing:
        return
    handler = _FileLog()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_TIME))
    logging.getLogger().addHandler(handler)
    _capturing = True


def _hold(run_id: str, path: str) -> None:
    """Point this thread's log lines at one file, evicting the oldest file's
    lines past :data:`_LOGGED_FILES`."""
    with _log_lock:
        _held[threading.get_ident()] = (run_id, path)
        _lines[(run_id, path)] = collections.deque(maxlen=_LOG_LINES)
        while len(_lines) > _LOGGED_FILES:
            del _lines[next(iter(_lines))]


def _release() -> None:
    """Stop pointing this thread's lines at anything. The lines stay: a file's
    log is wanted after its verdict lands."""
    with _log_lock:
        _held.pop(threading.get_ident(), None)


def lines(run_id: str, path: str) -> list[str]:
    """What was logged while this file was worked on. Empty for a cached
    verdict or for lines since evicted."""
    with _log_lock:
        buffer = _lines.get((run_id, path))
        return list(buffer) if buffer else []


def forget_logs() -> None:
    """Drop every kept line. For tests."""
    with _log_lock:
        _lines.clear()
        _held.clear()


def _paused_path() -> str:
    return os.path.join(config.STATE_DIR, PAUSED_FILE)


def paused() -> bool:
    return not _running.is_set()


def _read_flag() -> dict | None:
    """The pause flag on disk, or None for no pause.

    An unreadable flag reads as none: the alternative is an install stuck
    paused with nothing able to explain why.
    """
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
    """Whether the flag on disk says paused. For the CLI, which is a second
    process and cannot see the listener's registry."""
    return _read_flag() is not None


def load_paused() -> None:
    """Restore a pause a previous run left behind. Never raises."""
    global _paused_by, _paused_at
    record = _read_flag()
    if record is None:
        return
    with _lock:
        _running.clear()
        _paused_by = str(record.get("by") or "")
        _paused_at = str(record.get("at") or "")
    log.warning(
        "processing is paused (since %s); nothing will be swept or rewritten until it resumes",
        _paused_at or "an earlier run",
    )


def _save_paused() -> None:
    """Write the flag. Never raises; a lost write costs only the restart
    memory."""
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        write_json(_paused_path(), {"paused": paused(), "by": _paused_by, "at": _paused_at})
    except OSError as err:
        log.warning("could not persist the pause flag: %s", err)


def pause(by: str = "") -> bool:
    """Stop anything new being picked up. Returns whether this changed it.

    Rewrites already running finish: throwing away a nearly finished encode is
    a bad trade. :func:`abort` is the impatient version.
    """
    global _paused_by, _paused_at
    with _lock:
        if paused():
            return False
        _running.clear()
        _paused_by, _paused_at = by, events.timestamp()
    _save_paused()
    events.record("paused", by=by or None)
    # The one registry change with no run appearing or leaving.
    notify.publish(notify.RUNS)
    log.warning("processing paused%s", f" by {by}" if by else "")
    return True


def resume(by: str = "") -> bool:
    """Let work start again. Returns whether this changed it."""
    global _paused_by, _paused_at
    with _lock:
        if not paused():
            return False
        _running.set()
        _paused_by, _paused_at = "", ""
    _save_paused()
    events.record("resumed", by=by or None)
    notify.publish(notify.RUNS)
    log.info("processing resumed%s", f" by {by}" if by else "")
    return True


def wait_for_resume(timeout: float | None = None) -> bool:
    """Block until work may start again. False if the timeout ran out first."""
    return _running.wait(timeout)


def hold(run_id: str | None = None) -> bool:
    """Wait out a pause; False if the run was stopped before or during it.

    Every thread about to start on a file passes through here, which is what
    lets one flag stop the sweep and the webhook workers alike.
    """
    while not wait_for_resume(_HOLD_TICK):
        if stopping(run_id):
            return False
    return not stopping(run_id)


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
    :func:`close_run` publishes that.
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
    kind: str,
    *,
    dry_run: bool = False,
    label: str = "",
    filling: bool = False,
) -> Run:
    """Register a run, or return the one already under that id.

    Reopening is for imports: a file parked behind a seeding download can be
    released days later under the same run id.
    """
    with _lock:
        run = _runs.get(run_id)
        created = run is None
        if run is None:
            run = Run(run_id, kind, time.time(), dry_run=dry_run, label=label)
            _runs[run_id] = run
        run.filling = run.filling or filling
    # A run appearing is what an idle page is waiting to hear.
    if created:
        notify.publish(notify.RUNS)
    return run


def add_file(run_id: str) -> None:
    """One more file for this run to get through."""
    with _lock:
        if run := _runs.get(run_id):
            run.total += 1
    _moved(run_id)


def seal(run_id: str) -> None:
    """Mark a run as fully handed its files. Until then an import stays open,
    since more may still be arriving."""
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        run.filling = False
        closed = _close_if_done(run)
    if closed:
        notify.publish(notify.RUNS)


def close_run(run_id: str) -> None:
    with _lock:
        gone = _runs.pop(run_id, None) is not None
    if gone:
        notify.publish(notify.RUNS)


def _close_if_done(run: Run) -> bool:
    """Drop an import with nothing left; whether it went. Call under the lock;
    the caller publishes.

    Only imports: a sweep is closed by the function running it, or an empty
    library would retire it before the walk began.
    """
    if run.kind == IMPORT and not run.filling and not run.active and run.done >= run.total:
        _runs.pop(run.id, None)
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


def queue(run_id: str | None, path: str, seconds: float = 0.0) -> None:
    """Record a file needing work no worker has started.

    ``seconds`` is the expected duration; zero where unknown. Removed in
    :func:`begin`, or in :func:`tally` for a run stopped first.
    """
    if run_id is None:
        return
    with _lock:
        if run := _runs.get(run_id):
            run.queued[path] = seconds
    _moved(run_id)


def begin(run_id: str | None, path: str) -> None:
    """Record that a thread has picked this file up.

    A None run is accepted throughout: the CLI's fix has none. Nothing is
    recorded for it.
    """
    if run_id is None:
        return
    _hold(run_id, path)
    with _lock:
        if run := _runs.get(run_id):
            # Moved from queued to active, or the remaining work would count it
            # twice. The estimate comes with it.
            run.active[path] = Active(time.time(), expected=run.queued.pop(path, 0.0))
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


def finish(run_id: str | None, path: str) -> None:
    """Record that the thread has released this file. It moves to the recent
    list; the verdict lands on that row in :func:`tally`."""
    if run_id is None:
        return
    _release()
    now = time.time()
    with _lock:
        run = _runs.get(run_id)
        if run and (active := run.active.pop(path, None)):
            run.recent.append(Done(path, round(now - active.since, 1)))
    _moved(run_id)


def tally(
    run_id: str | None,
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
    if run_id is None:
        return
    closed = False
    with _lock:
        if run := _runs.get(run_id):
            run.done += 1
            run.counts[status] = run.counts.get(status, 0) + 1
            # Usually gone already in begin(); still here for a file a stopped
            # run never picked up.
            run.queued.pop(path, None)
            if path:
                _decide(run, path, status, detail[:_DETAIL_MAX], cached)
            closed = _close_if_done(run)
    # A run closing is a registry change, not progress; the pages treat them
    # differently.
    if closed:
        notify.publish(notify.RUNS)
    else:
        _moved(run_id)


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


def stop(run_id: str) -> bool:
    """Ask a run to stop between files. Returns whether it existed.

    A sweep still writes its cache and its summary.
    """
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return False
        run.stopping = True
    # Published at once: every other tab shows a Stop button that no longer
    # applies.
    notify.publish(notify.RUNS)
    log.info("run %s asked to stop", run_id)
    return True


def skip(run_id: str, path: str) -> str:
    """Take one file off a run: ``active`` where a thread has it this second,
    ``waiting`` where none has yet, or ``""`` where there is nothing to skip.

    Only recorded here. An active file's rewrite is killed by the caller, which
    is the whole difference between skipping it and waiting for it.
    """
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return ""
        if path in run.active:
            where = "active"
        elif path in run.queued:
            where = "waiting"
        elif any(done.path == path and done.status for done in run.recent):
            # A verdict is the run finished with it, and the skip would sit in
            # the set until the run closed. A released file with none yet is a
            # sweep between the probe and the slot, which is still ahead.
            return ""
        else:
            # A delivery's queue, which lives in the work queue rather than
            # here, or that gap between a probe and a slot.
            where = "waiting"
        run.skipped.add(path)
    notify.publish(notify.RUNS)
    log.info("%s skipped on run %s", path, run_id)
    return where


def skipped(run_id: str | None, path: str) -> bool:
    """Whether this file was taken off the run before a worker reached it."""
    if run_id is None:
        return False
    with _lock:
        run = _runs.get(run_id)
        return bool(run and path in run.skipped)


def stop_all() -> int:
    """Ask every run to stop; how many were asked. Each still stops between
    files."""
    with _lock:
        asked = [run for run in _runs.values() if not run.stopping]
        for run in asked:
            run.stopping = True
    if asked:
        notify.publish(notify.RUNS)
        log.info("every run asked to stop (%d)", len(asked))
    return len(asked)


def drop(run_id: str | None) -> None:
    """Take a file off a run that will never reach it.

    A stopped delivery's queued files get no verdict, since nothing opened
    them, but a run whose ``done`` never reaches ``total`` never retires.
    """
    if run_id is None:
        return
    closed = False
    with _lock:
        if run := _runs.get(run_id):
            run.total = max(run.done, run.total - 1)
            closed = _close_if_done(run)
    if closed:
        notify.publish(notify.RUNS)
    else:
        _moved(run_id)


def stopping(run_id: str | None) -> bool:
    if run_id is None:
        return False
    with _lock:
        run = _runs.get(run_id)
        return bool(run and run.stopping)


def running(kind: str) -> Run | None:
    """The first run of a kind, for the guard against a second sweep."""
    with _lock:
        return next((run for run in _runs.values() if run.kind == kind), None)


def cache_holder() -> Run | None:
    """The run currently rewriting the sweep cache, if any.

    A sweep and a re-check both write the file whole, so a second starting
    mid-walk would lose the first's verdicts. Clearing the cache is refused on
    the same grounds.
    """
    with _lock:
        return next((run for run in _runs.values() if run.kind in CACHE_KINDS), None)


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


def stamp(when: float) -> str:
    """A moment in the history's ``ts`` format."""
    return events.at(when)


def _still_to_go(active: Active, now: float) -> float:
    """Seconds the thread holding this file has left: from ffmpeg's readout
    during an encode, otherwise from the estimate it was queued under."""
    if active.stage == ENCODING and active.total and active.speed > 0:
        return max(0.0, (active.total - active.done) / active.speed)
    return max(0.0, active.expected - (now - active.since))


def _rewriting_left(run: Run, now: float) -> float | None:
    """Roughly how many wall seconds this run's rewriting has left, or None
    with no rewriting in it.

    A floor: a walking sweep has not found all its work, a delivery competes
    for the same slots, and every estimate is a median.
    """
    waiting = sum(run.queued.values())
    working = [_still_to_go(active, now) for active in run.active.values()]
    if not waiting and not any(working):
        return None
    # Spread across the slots, but never under the longest file still going.
    return max(estimate.backlog(waiting + sum(working)), max(working, default=0.0))


def _as_json(run: Run, now: float) -> dict:
    return {
        "id": run.id,
        "kind": run.kind,
        "started": stamp(run.started),
        "seconds": round(now - run.started, 1),
        "dry_run": run.dry_run,
        "label": run.label,
        "total": run.total,
        "done": run.done,
        "counts": dict(run.counts),
        # Work found but not started, and whether the listing is done. A count
        # under a walk still going is a floor.
        "queued": len(run.queued),
        "walking": run.walking,
        # Null where the run has no rewriting in it.
        "rewrite_seconds": _rewriting_left(run, now),
        "stopping": run.stopping,
        # Longest-running first, so a page with room for one shows the file
        # holding everything up.
        "active": [
            {
                "path": path,
                "seconds": round(now - active.since, 1),
                "stage": active.stage,
                # All three are zero for anything but an encode.
                "duration": round(active.total, 1),
                "done": round(active.done, 1),
                "speed": round(active.speed, 2),
                # Asked for but not yet given up: the kill and the thread
                # noticing are two moments.
                "skipped": path in run.skipped,
            }
            for path, active in sorted(run.active.items(), key=lambda item: item[1].since)
        ],
        # The head of the queue in the order it will be reached, so a page has
        # something to name when somebody wants one of them left alone. Bounded:
        # a first-night sweep queues thousands and the page shows a handful.
        "upcoming": [
            {"path": path, "expected": round(seconds, 1), "skipped": path in run.skipped}
            for path, seconds in list(run.queued.items())[:_UPCOMING]
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


def snapshot() -> dict:
    """Everything happening right now, as the overview reads it."""
    now = time.time()
    with _lock:
        runs = [_as_json(run, now) for run in _runs.values()]
    # Oldest first: a nightly sweep stays at the top while deliveries come and
    # go beneath it.
    runs.sort(key=lambda run: run["started"])
    return {
        "paused": paused(),
        "paused_by": _paused_by,
        "paused_at": _paused_at,
        # How a page tells a sweep that finished from one a restart cut off.
        "up_since": stamp(_UP),
        "runs": runs,
    }
