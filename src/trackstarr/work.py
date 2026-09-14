"""One ordered queue for imports, discovery and rewrites.

Workers claim the first eligible item under the same lock used for reordering.
Probe capacity is separate so a long encode cannot stop discovery. A file keeps
its rank when discovery hands it to rewriting. Running work is never displaced.
Like the run registry, this queue belongs to the current process.
"""

import heapq
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count, islice
from typing import Any, Literal

from . import config, notify, queue_view, runs
from .executor import Cancel


class ConflictError(Exception):
    """A queue command overlaps an in-flight pause reservation."""


_identities = count(1)


@dataclass(frozen=True)
class Handle:
    """A file admission, distinct from any later admission of the same path."""

    identity: int
    run: str
    path: str

    @property
    def key(self) -> tuple[str, str]:
        return self.run, self.path


class Phase(Future):
    """A phase result plus its file identity and callback-drain boundary."""

    def __init__(self, handle: Handle):
        super().__init__()
        self.handle = handle
        self.settled = threading.Event()


@dataclass
class Task:
    run: str
    path: str
    lane: str
    call: Callable[[], Any]
    future: Phase
    state: Literal["pending", "active", "awaiting_decision", "terminal"] = "pending"
    #: Queue position, kept across the handoff from discovery to rewriting.
    #: Lower is sooner; a move to the front assigns negatives.
    rank: int = 0
    expected: float = 0.0
    priority: bool = False
    #: Taken off the run by hand. Dispatched under the abandoned budget so its
    #: callable reaches its own early exit rather than waiting for a resume.
    skipped: bool = False
    group: Group | None = None
    on_terminal: Callable[[], None] | None = None
    #: The published row for this admission while it waits, so a read has its
    #: position without looking for it. None wherever the file is not queued.
    row: queue_view.Entry | None = None
    #: This phase's kill switch, shared with the callable so a skip reaches the
    #: rewrite it chose and not a later claim on the same file.
    cancel: Cancel = field(default_factory=Cancel)

    @property
    def key(self) -> tuple[str, str]:
        return self.run, self.path


@dataclass
class RunControl:
    """Admission lifetime and the run's stop/skip state.

    ``skipped`` indexes the live tasks' own flags for reporting; it holds no
    path the scheduler has released.
    """

    closing: bool = False
    stopping: bool = False
    live: int = 0
    skipped: set[str] = field(default_factory=set)


class Group:
    """Own all phases before dispatch, and drain them before releasing a cache."""

    def __init__(self, scheduler: Scheduler, run: str, priority: bool):
        self.scheduler = scheduler
        self.run = run
        self.priority = priority
        self.phases: list[Phase] = []
        self.closed = False

    def __call__(
        self, path, lane, call, expected=0.0, *, cancel: Cancel | None = None
    ) -> Phase:
        return self.scheduler.submit(
            self.run,
            path,
            lane,
            call,
            expected,
            priority=self.priority,
            group=self,
            cancel=cancel,
        )

    def register(self, phase: Phase) -> None:
        if self.closed:
            raise ValueError("task group is closed")
        self.phases.append(phase)

    def drain(self) -> bool:
        with self.scheduler.condition:
            self.closed = True
        for phase in self.phases:
            phase.settled.wait()
        unfinished = False
        for phase in self.phases:
            unfinished = self.scheduler.complete_file(phase.handle) or unfinished
        return unfinished


class Scheduler:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.controls: dict[str, RunControl] = {}
        self.tasks: dict[tuple[str, str], Task] = {}
        self.pending: dict[tuple[str, str], Task] = {}
        self.active: dict[tuple[str, str], Task] = {}
        self.reserved: dict[tuple[str, str], str] = {}
        self.occupied = {"probe": 0, "work": 0}
        #: Phases past their admission and not yet settled. A worker releases
        #: its lane and its path before the callbacks run, so this is what a
        #: drain counts over the gap between the two.
        self.settling = 0
        self.active_paths: set[str] = set()
        self.candidates: dict[tuple[str, bool], list] = {
            (lane, abandoned): [] for lane in self.occupied for abandoned in (False, True)
        }
        self.parked: dict[str, set[tuple[str, str]]] = {}
        self.entries: dict[tuple[str, str], tuple] = {}
        self.by_run: dict[str, set[tuple[str, str]]] = {}
        self.views: dict[str, queue_view.RunQueue] = {}
        #: Waiting files per lane, kept as they are admitted and claimed.
        self.queued = {"probe": 0, "work": 0}
        self.version = 0
        self.sequence = 0
        self.front = 0
        #: What a queue page would draw, versioned. A restart starts the count
        #: again, so pages carry the epoch beside it.
        self.epoch = uuid.uuid4().hex
        self.revision = 0
        self.undo: tuple[str, dict[Handle, int]] | None = None
        self.thread: threading.Thread | None = None
        #: Admission is closed. What was admitted still dispatches, so a
        #: stopped run's backlog can reach its own early exit during a drain.
        self.closed = False
        self.halted = False
        self.paused = False

    def start(self):
        with self.condition:
            if self.thread is None:
                self.thread = threading.Thread(target=self._dispatch, daemon=True, name="queue")
                self.thread.start()
            self.condition.notify_all()

    def set_paused(self, on: bool) -> bool:
        """Set dispatch eligibility; active phases retain their mutation gate."""
        with self.condition:
            changed = self.paused != on
            self.paused = on
            self.condition.notify_all()
            return changed

    def open_run(self, run_id: str) -> bool:
        """Open admission under S; whether this is a new run."""
        with self.condition:
            if self.closed:
                raise ValueError("scheduler is closed")
            control = self.controls.get(run_id)
            if control is not None and control.closing:
                raise ValueError("run is closing")
            self.controls.setdefault(run_id, RunControl())
            return control is None

    def close_run(self, run_id: str) -> bool:
        """Close admission and attempt retirement; caller announces after S."""
        with self.condition:
            if control := self.controls.get(run_id):
                control.closing = True
            return self.retire_run(run_id)

    def submit(
        self,
        run,
        path,
        lane,
        call,
        expected=0.0,
        priority=False,
        *,
        group: Group | None = None,
        counted: bool = False,
        on_terminal: Callable[[], None] | None = None,
        cancel: Cancel | None = None,
    ) -> Phase:
        with self.condition:
            if self.closed:
                raise ValueError("scheduler is closed")
            control = self.controls.get(run)
            if run and (control is None or control.closing):
                raise ValueError("run is not open for admission")
            key = (run or "", path)
            if key in self.tasks:
                raise ValueError("file already queued")
            handle = Handle(next(_identities), *key)
            phase = Phase(handle)
            if group is not None:
                group.register(phase)
            self.sequence += 1
            task = Task(
                run or "",
                path,
                lane,
                call,
                phase,
                rank=self.sequence,
                expected=expected,
                priority=priority and lane == "probe",
                group=group,
                on_terminal=on_terminal,
                cancel=cancel or Cancel(path),
            )
            self.tasks[key] = task
            if control is not None:
                control.live += 1
            if counted and run:
                runs.add_file(run)
            self._enqueue(task)
        runs.announce_queue(run)
        return phase

    def _enqueue(self, task):
        self.pending[task.key] = task
        self.by_run.setdefault(task.run, set()).add(task.key)
        self.queued[task.lane] += 1
        self._list(task)
        self._index(task)
        self._revise()
        self.condition.notify_all()

    def _revise(self) -> None:
        """Note that a page drawn before this one would describe another queue.

        Every membership, order and payload change a reader can see passes
        through here, so two pages carrying one revision can be joined.
        """
        self.revision += 1

    def _drawn(self, run: str) -> bool:
        """Whether this run has rows a page is still naming."""
        view = self.views.get(run)
        return view is not None and view.waiting > view.hidden

    def _list(self, task):
        """Publish the task's queue row, on its run's rows."""
        view = self.views.setdefault(task.run, queue_view.RunQueue())
        task.row = queue_view.Entry(
            queue_view.Row(self._order(task), task.run, task.path, task.expected, task.skipped)
        )
        view.add(task.row)

    def _unlist(self, task):
        """Retire the row of a file that has left the queue."""
        view = self.views[task.run]
        view.drop(task.row)
        task.row = None
        if not view.waiting:
            del self.views[task.run]

    def _relist(self):
        """Republish every row after a command changed queue positions."""
        self.views = {}
        for task in sorted(self.pending.values(), key=self._order):
            self._list(task)

    def continue_file(
        self, handle: Handle, call, expected=0.0, cancel: Cancel | None = None, *, hurry=False
    ) -> Phase:
        with self.condition:
            task = self.tasks.get(handle.key)
            if (
                task is None
                or task.future.handle != handle
                or task.state != "awaiting_decision"
            ):
                raise ValueError("file is not awaiting a decision")
            phase = Phase(handle)
            if task.group is not None:
                task.group.register(phase)
            task.lane = "work"
            task.state = "pending"
            task.call = call
            task.expected = expected
            task.priority = False
            # Automatic import priority yields to an outstanding manual reorder.
            # Undo restores the uncurated order and permits promotion again.
            if hurry and self.undo is None:
                self.front -= 1
                task.rank = self.front
            task.future = phase
            # The probe's switch dies with the probe: a skip now has to reach
            # the rewrite this phase is about to start.
            task.cancel = cancel or Cancel(task.path)
            self._enqueue(task)
        runs.announce_queue(handle.run)
        return phase

    def _terminal(self, task):
        task.state = "terminal"
        del self.tasks[task.key]
        if control := self.controls.get(task.run):
            control.live -= 1
            control.skipped.discard(task.path)
        # Woken for a drain as well as for dispatch: this is where the last
        # admission of a shutting-down process goes.
        self.condition.notify_all()
        return self.retire_run(task.run)

    def retire_run(self, run: str) -> bool:
        """Check retirement under the condition; caller publishes after unlock."""
        control = self.controls.get(run)
        if control is None or control.live:
            return False
        if runs.retire(run, closing=control.closing):
            del self.controls[run]
            return True
        return False

    def complete_file(self, handle: Handle) -> bool:
        """Retire an awaiting decision; stale or repeated cleanup is harmless."""
        with self.condition:
            task = self.tasks.get(handle.key)
            if (
                task is None
                or task.future.handle != handle
                or task.state != "awaiting_decision"
            ):
                return False
            retired = self._terminal(task)
            # Held over the callback below, which is this file's last accounting.
            self.settling += 1
        try:
            if retired:
                notify.publish(notify.RUNS)
            if task.on_terminal is not None:
                task.on_terminal()
        finally:
            self._settled()
        return True

    def _settled(self) -> None:
        """Release a phase's settlement claim, and wake whoever is draining."""
        with self.condition:
            self.settling -= 1
            self.condition.notify_all()

    def stopping(self, run_id: str | None) -> bool:
        """Whether this run was asked to stop. Safe to call under the condition."""
        with self.condition:
            control = self.controls.get(run_id or "")
            return control is not None and control.stopping

    def skipped(self, run_id: str | None, path: str) -> bool:
        """Whether this run's live admission of the file was taken off it."""
        with self.condition:
            task = self.tasks.get((run_id or "", path))
            return task is not None and task.skipped

    def _halting(self, run) -> bool:
        control = self.controls.get(run)
        return control is not None and control.stopping

    def _abandoned(self, task) -> bool:
        return task.skipped or self._halting(task.run)

    def _order(self, task):
        return not task.priority, task.rank, task.key

    def _index(self, task):
        abandoned = self._abandoned(task)
        self.version += 1
        entry = (*self._order(task), self.version)
        self.entries[task.key] = entry
        heapq.heappush(self.candidates[task.lane, abandoned], entry)

    def _reindex(self):
        # Reorder/undo and amortized compaction rebuild only live candidates.
        tasks = [self.pending[key] for key in self.entries]
        for heap in self.candidates.values():
            heap.clear()
        for task in tasks:
            self._index(task)

    def _refresh(self, keys):
        for key in keys:
            if key in self.entries:
                self._index(self.pending[key])
        self._compact()

    def _compact(self):
        # At most two entries per indexed task plus a small constant allowance.
        # Compaction preserves parked blockers and does not affect display revision.
        if sum(map(len, self.candidates.values())) > 2 * len(self.entries) + 64:
            self._reindex()

    def _wake_path(self, path):
        for key in self.parked.pop(path, ()):
            self._index(self.pending[key])

    def _claim(self):
        settings = config.current()
        limits = {"probe": settings.PROBE_WORKERS, "work": settings.MAX_CONCURRENT_REWRITES}
        eligible = []
        paused = self.paused
        for (lane, abandoned), heap in self.candidates.items():
            if self.occupied[lane] >= limits[lane] or (paused and not abandoned):
                continue
            while heap:
                key = heap[0][2]
                if self.entries.get(key) != heap[0]:
                    heapq.heappop(heap)
                    continue
                task = self.pending[key]
                if task.key in self.reserved or task.path in self.active_paths:
                    heapq.heappop(heap)
                    del self.entries[key]
                    self.parked.setdefault(task.path, set()).add(task.key)
                else:
                    eligible.append((heap[0], lane, abandoned))
                    break
        if not eligible:
            return None
        entry, lane, abandoned = min(eligible)
        heapq.heappop(self.candidates[lane, abandoned])
        task = self.pending.pop(entry[2])
        del self.entries[task.key]
        self.by_run[task.run].remove(task.key)
        if not self.by_run[task.run]:
            del self.by_run[task.run]
        task.state = "active"
        self.active[task.key] = task
        self.settling += 1
        self.occupied[lane] += 1
        self.active_paths.add(task.path)
        self.queued[task.lane] -= 1
        self._unlist(task)
        self._revise()
        self._compact()
        return task

    def _dispatch(self):
        with self.condition:
            while not self.halted:
                task = self._claim()
                if task is None:
                    self.condition.wait()
                    continue
                threading.Thread(
                    target=self._execute, args=(task,), daemon=True, name=f"queue-{task.lane}"
                ).start()

    def _execute(self, task):
        phase = task.future
        deliver = phase.set_running_or_notify_cancel()
        result = None
        error = None
        try:
            result = task.call()
        except BaseException as exc:
            error = exc
        finally:
            with self.condition:
                del self.active[task.key]
                self.occupied[task.lane] -= 1
                self.active_paths.remove(task.path)
                self._wake_path(task.path)
                terminal = task.lane == "work" or error is not None or not deliver
                retired = False
                if terminal:
                    retired = self._terminal(task)
                else:
                    task.state = "awaiting_decision"
                self.condition.notify_all()
        # A completion callback can enqueue the file's next phase. Publish the
        # result only after releasing its old claim and registry entry.
        try:
            if retired:
                notify.publish(notify.RUNS)
            if terminal and task.on_terminal is not None:
                try:
                    task.on_terminal()
                except BaseException as exc:
                    if error is None:
                        error = exc
            if deliver:
                if error is not None:
                    phase.set_exception(error)
                else:
                    phase.set_result(result)
        finally:
            phase.settled.set()
            self._settled()

    def _showing(self) -> int:
        """How many files a page can name, without counting them one by one."""
        return sum(
            view.waiting - view.hidden
            for run, view in self.views.items()
            if not self._halting(run)
        )

    def _capture(self) -> list[queue_view.Capture]:
        """Each run's rows as they stand; the caller holds the condition."""
        return [view.capture() for run, view in self.views.items() if not self._halting(run)]

    def control_view(self) -> dict[str, runs.Control]:
        """Each run's queue state for a reporting read; the caller holds S.

        Claimed phases and the head of the waiting rows, with counts for the
        rest of them. A file between its probe and its rewrite is nobody's
        queue row: it has no worker and the walk has not decided what to do
        with it.
        """
        claimed: dict[str, list[runs.Queued]] = {}
        for task in self.active.values():
            claimed.setdefault(task.run, []).append(
                runs.Queued(task.path, task.expected, task.skipped)
            )
        return {
            run: self._control(control, claimed.get(run, ()), self.views.get(run))
            for run, control in self.controls.items()
        }

    def _control(self, control, claimed, view) -> runs.Control:
        stopping = control.stopping
        skipped = frozenset(row.path for row in claimed if row.skipped)
        if view is None:
            return runs.Control(stopping, skipped, tuple(claimed))
        rows = islice(view.capture().queued(), runs.UPCOMING)
        return runs.Control(
            stopping,
            skipped,
            tuple(claimed),
            view.waiting,
            view.expected,
            tuple(runs.Queued(row.path, row.expected, row.skipped) for row in rows),
        )

    def capture(self) -> queue_view.Snapshot:
        """Counts and row generations taken together, without traversing the backlog."""
        with self.condition:
            return queue_view.Snapshot(
                self.epoch, self.revision, self._showing(), tuple(self._capture())
            )

    def snapshot(self, query="", offset=0, limit=50):
        return self.capture().page(query, offset, limit)

    def count(self, lane):
        with self.condition:
            return self.queued[lane]

    def _check_reservations(self, keys):
        if self.reserved.keys() & keys:
            raise ConflictError("selected files have a pause command in progress")

    def _select(self, keys):
        # Check conflicts before filtering stopped/skipped tasks: a reservation
        # remains the command boundary even when its run stops during the write.
        self._check_reservations(keys)
        chosen = [
            task
            for key in keys
            if (task := self.pending.get(key)) is not None and not self._abandoned(task)
        ]
        return [task.key for task in sorted(chosen, key=self._order)]

    def _mark_skipped(self, task) -> bool:
        """Take the file off its run. Whether a page loses a row by it."""
        task.skipped = True
        hidden = task.row is not None and self.views[task.run].hide(task.row)
        if control := self.controls.get(task.run):
            control.skipped.add(task.path)
        return hidden

    def _skip(self, selected):
        hidden = False
        for key in selected:
            hidden = self._mark_skipped(self.tasks[key]) or hidden
        if hidden:
            self._revise()
        self._refresh(selected)
        self.condition.notify_all()

    def stop(self, run_id: str) -> bool:
        """Ask a run to wind up. Whether it was open; the caller publishes."""
        with self.condition:
            control = self.controls.get(run_id)
            if control is None:
                return False
            if not control.stopping and self._drawn(run_id):
                self._revise()
            control.stopping = True
            self._refresh(self.by_run.get(run_id, ()))
            self.condition.notify_all()
        return True

    def stop_all(self) -> list[str]:
        """Ask every open run to stop; the ones this asked, for the caller."""
        with self.condition:
            asked = [run for run, control in self.controls.items() if not control.stopping]
            drawn = False
            for run in asked:
                self.controls[run].stopping = True
                drawn = self._drawn(run) or drawn
                self._refresh(self.by_run.get(run, ()))
            if drawn:
                self._revise()
            self.condition.notify_all()
        return asked

    def skip(self, keys) -> list[tuple[str, str]]:
        """Take waiting files off their runs; which ones, for the caller."""
        with self.condition:
            selected = self._select(keys)
            self._skip(selected)
        return selected

    def skip_file(self, run, path) -> tuple[str, Cancel | None]:
        """Take one named file off a run, whatever phase it is in.

        ``active`` where a worker has the phase, ``waiting`` where none has yet
        or the file is between its probe and its rewrite, and ``""`` where this
        run has no live admission of it. The kill switch comes back with the
        answer, so the caller signals the phase this chose and no successor.
        """
        with self.condition:
            self._check_reservations({(run, path)})
            task = self.tasks.get((run, path))
            if task is None:
                return "", None
            if self._mark_skipped(task):
                self._revise()
            self._refresh(((run, path),))
            self.condition.notify_all()
            return "active" if task.state == "active" else "waiting", task.cancel

    @contextmanager
    def pause_selection(self, keys):
        """Reserve waiting identities while the caller validates/persists a batch.

        Normal exit means persistence succeeded and the reserved files are
        skipped. Exceptions release every claim without skipping or changing
        rank. A stopped or closed run does not revoke its durable file pause.

        Store transactions, audit writes, notifications and caller code run
        without the condition held. Never enter the scheduler from a pause-store
        transaction.
        """
        token = uuid.uuid4().hex
        with self.condition:
            selected = tuple(self._select(keys))
            self.reserved.update(dict.fromkeys(selected, token))
        try:
            yield selected
            with self.condition:
                self._skip(selected)
        finally:
            with self.condition:
                for key in selected:
                    del self.reserved[key]
                    self._wake_path(key[1])
                self.condition.notify_all()

    def move_top(self, keys) -> tuple[int, str]:
        """Move waiting files to the front; how many, and the token to undo it."""
        with self.condition:
            selected = self._select(keys)
            if not selected:
                return 0, ""
            token = uuid.uuid4().hex
            self.undo = (
                token,
                {task.future.handle: task.rank for task in self.tasks.values()},
            )
            self._front(selected)
        notify.publish(notify.RUNS)
        return len(selected), token

    def _front(self, selected):
        self.front -= len(selected)
        for index, key in enumerate(selected):
            self.tasks[key].rank = self.front + index
        self._reindex()
        self._relist()
        self._revise()
        self.condition.notify_all()

    def waiting_work(self, run: str) -> set[str]:
        """Snapshot eligible rewrite paths; callers may stat outside the lock."""
        with self.condition:
            if self.paused:
                return set()
            return {
                key[1]
                for key in self.by_run.get(run, ())
                if self.pending[key].lane == "work"
                and not self._abandoned(self.pending[key])
                and key not in self.reserved
            }

    def hurry(self, keys) -> int:
        """Promote settled rewrites without replacing the operator's undo token.

        Recheck eligibility after the caller's stats. A concurrent pause or
        claim makes a candidate harmlessly disappear from this pass.
        """
        with self.condition:
            if self.paused:
                return 0
            selected = self._select(
                {
                    key
                    for key in keys
                    if key not in self.reserved
                    and key in self.pending
                    and self.pending[key].lane == "work"
                }
            )
            if not selected:
                return 0
            self._front(selected)
        notify.publish(notify.RUNS)
        return len(selected)

    def restore(self, token):
        with self.condition:
            if self.undo is None or self.undo[0] != token:
                return False
            self._check_reservations(
                {
                    h.key
                    for h in self.undo[1]
                    if h.key in self.tasks and self.tasks[h.key].future.handle == h
                }
            )
            for handle, rank in self.undo[1].items():
                task = self.tasks.get(handle.key)
                if task is not None and task.future.handle == handle:
                    task.rank = rank
            self.undo = None
            self._reindex()
            self._relist()
            self._revise()
            self.condition.notify_all()
        notify.publish(notify.RUNS)
        return True

    def close(self):
        """Refuse new admissions, leaving what was admitted to dispatch."""
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def quiet(self) -> bool:
        """Whether nothing is admitted, reserved or still settling. Caller holds S."""
        return not self.tasks and not self.reserved and not self.settling

    def outstanding(self) -> str:
        """What a drain is still waiting for, for the line a timeout logs."""
        with self.condition:
            return (
                f"{len(self.tasks)} queued or claimed, {len(self.reserved)} reserved, "
                f"{self.settling} settling"
            )

    def drain(self, timeout):
        """Wait for every admission, reservation and phase to settle. Whether
        they did.

        A reservation counts: its durable write must commit or roll back before
        the process stops answering for the files it holds. Settlement counts
        too: a worker releases its lane before its callbacks run, and a verdict
        is booked in one of those.
        """
        with self.condition:
            return self.condition.wait_for(self.quiet, timeout)

    def shutdown(self):
        """Close admission and stop dispatching. The join holds no lock."""
        with self.condition:
            self.closed = True
            self.halted = True
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=2)


scheduler = Scheduler()


def reset():
    global scheduler
    scheduler.shutdown()
    scheduler = Scheduler()
