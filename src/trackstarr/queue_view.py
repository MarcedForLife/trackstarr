"""Queue rows published for reading, so a read costs its answer and not the backlog.

A run's waiting files are a block of rows in queue order plus the few that
arrived out of it. A row leaves by being marked rather than by moving its
neighbours, so a reader takes the block under the scheduler's condition and
walks it after letting go. Visibility stamps are compared with the captured
generation, so later claims and skips cannot change an earlier response.
"""

import heapq
from bisect import insort
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from itertools import islice

from . import paths

#: Rows sitting outside the block, or stepped over inside it, before the block
#: is rebuilt around them.
_LOOSE = 64


@dataclass(frozen=True, slots=True)
class Row:
    """Immutable file payload, at the position the queue will reach it in."""

    order: tuple[bool, int, tuple[str, str]]
    run: str
    path: str
    expected: float
    skipped: bool = False

    def __lt__(self, other: Row) -> bool:
        return self.order < other.order


@dataclass(slots=True, weakref_slot=True)
class Entry:
    """Monotonic visibility transitions, written only under the scheduler lock.

    Each admission/phase gets a new entry. There is no history chain: captures
    retain just their block and these three stamps, until the reader releases it.
    """

    row: Row
    added: int = 0
    hidden: int = 0
    retired: int = 0

    def __lt__(self, other: Entry) -> bool:
        return self.row < other.row

    def queued(self, generation: int) -> bool:
        return self.added <= generation and (not self.retired or self.retired > generation)

    def skipped(self, generation: int) -> bool:
        return self.row.skipped or bool(self.hidden and self.hidden <= generation)


def _queued(entry: Entry) -> bool:
    return not entry.retired


def _shown(entry: Entry) -> bool:
    return not entry.retired and not entry.hidden and not entry.row.skipped


@dataclass(frozen=True, slots=True)
class Capture:
    """One run's rows as they stood, walkable without the condition held."""

    block: list[Entry]
    length: int
    queued_from: int
    shown_from: int
    loose: tuple[Entry, ...]
    generation: int

    def _walk(self, start: int, *, shown: bool) -> Iterator[Row]:
        block = islice(self.block, start, self.length)
        for entry in heapq.merge(block, self.loose):
            if entry.queued(self.generation):
                skipped = entry.skipped(self.generation)
                if not shown or not skipped:
                    yield replace(entry.row, skipped=True) if skipped else entry.row

    def queued(self) -> Iterator[Row]:
        """Every file waiting at capture, including ones taken off their run."""
        return self._walk(self.queued_from, shown=False)

    def shown(self) -> Iterator[Row]:
        """The files a page names and a command can still act on."""
        return self._walk(self.shown_from, shown=True)


class RunQueue:
    """One run's rows, and the counts a read would otherwise have to add up."""

    def __init__(self) -> None:
        self.block: list[Entry] = []
        self.loose: list[Entry] = []
        self.generation = 0
        #: Block positions with nothing left before them, one for waiting rows
        #: and one for shown rows. Both only ever move forward.
        self.queued_from = 0
        self.shown_from = 0
        self.waiting = 0
        #: Waiting files taken off the run, which a page still draws.
        self.hidden = 0
        self.expected = 0.0

    def add(self, row: Entry) -> None:
        self.generation += 1
        row.added = self.generation
        self.waiting += 1
        self.expected += row.row.expected
        if row.skipped(self.generation):
            self.hidden += 1
        # A file returning for its rewrite keeps the rank it was found under,
        # so it arrives before the block's tail rather than after it.
        if self.block and row < self.block[-1]:
            insort(self.loose, row)
            self._settle()
        else:
            self.block.append(row)

    def drop(self, row: Entry) -> None:
        """A worker claimed the file, or its admission ended."""
        self.generation += 1
        row.retired = self.generation
        self.waiting -= 1
        if row.skipped(self.generation):
            self.hidden -= 1
        # Summed and unsummed floats drift, and an empty queue owes nothing.
        self.expected = self.expected - row.row.expected if self.waiting else 0.0
        self._settle()

    def hide(self, row: Entry) -> bool:
        """The file was taken off its run, keeping its place until a worker reaches it.

        Asking twice is the page's to do, and counts once. Whether this call is
        the one that took a row off the pages.
        """
        if row.skipped(self.generation):
            return False
        self.generation += 1
        row.hidden = self.generation
        self.hidden += 1
        return True

    def capture(self) -> Capture:
        """Take the rows for a read; the caller holds the scheduler's condition."""
        self.queued_from = self._advance(self.queued_from, _queued)
        self.shown_from = self._advance(self.shown_from, _shown)
        return Capture(
            self.block,
            len(self.block),
            self.queued_from,
            self.shown_from,
            tuple(self.loose),
            self.generation,
        )

    def _advance(self, start: int, wanted: Callable[[Entry], bool]) -> int:
        while start < len(self.block) and not wanted(self.block[start]):
            start += 1
        return start

    def _settle(self) -> None:
        """Rebuild the block once a walk steps over more than it reads."""
        if len(self.loose) <= _LOOSE and len(self.block) <= 2 * self.waiting + _LOOSE:
            return
        ordered = heapq.merge(islice(self.block, self.queued_from, None), self.loose)
        self.block = [row for row in ordered if not row.retired]
        self.loose.clear()
        self.queued_from = 0
        self.shown_from = 0


def stream(captures: Iterable[Capture]) -> Iterator[Row]:
    """Every run's shown rows, in the one order the queue will reach them."""
    return heapq.merge(*(capture.shown() for capture in captures))


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Counts and run inclusion from one scheduler boundary."""

    #: This queue's numbering, which a restart replaces. A reader joining pages
    #: compares it with the revision, since the count starts again at zero.
    epoch: str
    revision: int
    total: int
    captures: tuple[Capture, ...]

    def page(self, query: str = "", offset: int = 0, limit: int = 50) -> dict:
        # Ranked before the filter, so a searched row keeps its place in the
        # whole queue rather than its place among the matches.
        rows = enumerate(stream(self.captures), 1)
        if query:
            wanted = query.casefold()
            found = [(position, row) for position, row in rows if wanted in row.path.casefold()]
            matched, page = len(found), found[offset : offset + limit]
        else:
            matched, page = self.total, list(islice(rows, offset, offset + limit))
        return {
            "total": self.total,
            "matched": matched,
            "offset": offset,
            "epoch": self.epoch,
            "revision": self.revision,
            "items": [
                {
                    "run": row.run,
                    "path": row.path,
                    "expected": round(row.expected, 1),
                    "position": position,
                }
                for position, row in page
            ],
        }

    def for_folder(self, folder: str) -> list[dict]:
        """Title rows retain their positions in the captured whole queue."""
        inside = paths.within(folder)
        return [
            {
                "run": row.run,
                "path": row.path,
                "expected": round(row.expected, 1),
                "position": position,
            }
            for position, row in enumerate(stream(self.captures), 1)
            if inside(row.path)
        ]
