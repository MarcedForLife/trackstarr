"""Titles and files nobody wants rewritten yet, and when that lapses.

:mod:`trackstarr.runs` has one switch for the whole service; this is the
per-title version, for the film somebody is part way through. A pause never
stops a file being probed, planned or reported, only rewritten, so the library
goes on showing the work it is pausing back.

Pauses are a file, so a ``docker exec trackstarr sweep`` reads the same answer
as the listener, and an evening's pause survives a restart in the middle of it.
"""

import fcntl
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from . import config, events, notify, paths
from .arr import innermost
from .state import read_json_records, stamp, write_json

log = logging.getLogger(__name__)

#: Where the pauses live in STATE_DIR.
PAUSES_FILE = "pauses.json"

#: Most pauses one answer carries. A pause is placed by hand, so this bounds a
#: mistake rather than ordinary use.
MAX_PAUSES = 200

#: Longest a pause may run for, so a slip of the finger cannot park a title
#: past the point anyone would remember placing it. Four weeks.
MAX_SECONDS = 28 * 86400


@dataclass(frozen=True)
class Pause:
    """One title or file left alone, and until when."""

    #: The folder or file it covers, canonical and in this container's paths,
    #: as the sweep cache's keys are. A folder covers everything under it.
    path: str
    #: time.time() when it lapses; 0 for one only a person resumes.
    until: float = 0.0
    by: str = ""
    reason: str = ""
    #: When it was placed, in the history's ``ts`` format.
    at: str = ""
    #: The library title it was placed on, and that title's name. Labels for
    #: the pages: the path is what the pipeline matches on.
    title: str = ""
    name: str = ""

    def lapsed(self, now: float | None = None) -> bool:
        return bool(self.until) and (time.time() if now is None else now) >= self.until

    def describe(self) -> str:
        """The pause in a phrase, for a run row and for pending.tsv."""
        until = f" until {events.at(self.until)}" if self.until else ""
        because = f" ({self.reason})" if self.reason else ""
        return f"paused{until}{because}"

    def as_json(self) -> dict:
        return {
            "path": self.path,
            # Seconds left rather than a stamp: a browser in another zone reads
            # a countdown the same way this one does. Null for indefinite.
            "seconds": round(max(0.0, self.until - time.time())) if self.until else None,
            "until": events.at(self.until) if self.until else None,
            "by": self.by,
            "reason": self.reason,
            "at": self.at,
            "title": self.title,
            "name": self.name,
        }


_lock = threading.Lock()

#: The store as last read, with the mark of the file it came from. Re-read when
#: that moves, since a second process writes the same file, so judging a
#: library costs a stat per file rather than a parse.
_cached: tuple[tuple[int, int], dict[str, Pause]] | None = None


_transaction_lock = threading.Lock()


class CapacityError(ValueError):
    """The complete placement would exceed the live pause limit."""


@contextmanager
def _transaction() -> Iterator[None]:
    """Thread lock then stable sidecar flock; never call a scheduler here."""
    with _transaction_lock:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(_path() + ".lock", "a") as sidecar:
            fcntl.flock(sidecar, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(sidecar, fcntl.LOCK_UN)


def _path() -> str:
    return os.path.join(config.STATE_DIR, PAUSES_FILE)


def _migrate() -> None:
    """Called only inside a transaction; an existing pause store takes precedence."""
    legacy = os.path.join(config.STATE_DIR, "holds.json")
    if not os.path.exists(_path()) and os.path.exists(legacy):
        os.replace(legacy, _path())


def _disk() -> dict[str, Pause]:
    return {
        path: _pause(path, record)
        for path, record in read_json_records(_path()).items()
        if path
    }


def _pause(path: str, record: dict) -> Pause:
    """One stored record as a pause. A field that will not read takes its
    default: a damaged reason must not lose the pause itself."""
    return Pause(
        path,
        until=float(record.get("until") or 0.0) if _numeric(record.get("until")) else 0.0,
        by=str(record.get("by") or ""),
        reason=str(record.get("reason") or ""),
        at=str(record.get("at") or ""),
        title=str(record.get("title") or ""),
        name=str(record.get("name") or ""),
    )


def _numeric(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _read() -> dict[str, Pause]:
    """Every pause on disk, lapsed ones included."""
    global _cached
    if not os.path.exists(_path()) and os.path.exists(
        os.path.join(config.STATE_DIR, "holds.json")
    ):
        with _transaction():
            _migrate()
    mark = stamp(_path())
    with _lock:
        if _cached is not None and _cached[0] == mark:
            return _cached[1]
    found = _disk()
    with _lock:
        _cached = (mark, found)
    return found


def _save(found: dict[str, Pause]) -> None:
    """Write the store and drop the memo. Raises OSError, which the caller
    answers with: a pause the page believes in but nothing enforces is worse
    than a refusal."""
    global _cached
    os.makedirs(config.STATE_DIR, exist_ok=True)
    write_json(
        _path(),
        {
            pause.path: {
                "until": pause.until,
                "by": pause.by,
                "reason": pause.reason,
                "at": pause.at,
                "title": pause.title,
                "name": pause.name,
            }
            for pause in found.values()
        },
    )
    with _lock:
        _cached = None


def paused(path: str, now: float | None = None) -> Pause | None:
    """The pause covering ``path``, or None.

    The innermost live pause wins, so a lapsed pause on one episode does not
    hide a standing one on the series.
    """
    if not path:
        return None
    found = _read()
    if not found:
        return None
    return innermost(
        {at: pause for at, pause in found.items() if not pause.lapsed(now)},
        paths.canonical(path),
    )


def current(now: float | None = None) -> list[Pause]:
    """Every live pause, newest first. Reads never collect expired records."""
    return sorted(
        (pause for pause in _read().values() if not pause.lapsed(now)),
        key=lambda pause: pause.at,
        reverse=True,
    )


def _key(path: str) -> str:
    """The store's identity for a media path.

    Absolute, since a pause is matched by prefix against the paths a sweep
    walked, and canonical, so two spellings of one file cannot both be stored.
    """
    if not path.startswith("/"):
        raise ValueError("pause paths must be absolute")
    return paths.canonical(path)


def _mutate(change: Callable[[dict[str, Pause]], list[Pause]]) -> list[Pause]:
    with _transaction():
        _migrate()
        found = _disk()  # Always read disk, never a process's memo.
        now = time.time()
        live = {path: pause for path, pause in found.items() if not pause.lapsed(now)}
        changed = change(live)
        committed = bool(changed) or live != found
        if committed:
            _save(live)
    if committed:
        notify.publish(notify.RUNS)
    return changed


def place_many(
    targets: Sequence[tuple[str, str, str]],
    seconds: float = 0.0,
    by: str = "",
    reason: str = "",
) -> list[Pause]:
    """Atomically place (path, title, name) targets; last duplicate wins.

    Validate the entire batch before writing. Negative durations retain the
    single-item convention of indefinite pauses. Capacity counts distinct live
    paths once canonical, so extending a pause at the limit succeeds and two
    spellings of a file are one of them. OSError means no commit. Audit writes
    follow commit and unlock; events.record logs filesystem errors without
    raising, so a missing audit line does not turn success into failure.
    """
    if not _numeric(seconds) or not math.isfinite(seconds):
        raise ValueError("seconds must be finite")
    targets = [(_key(path), title, name) for path, title, name in targets]
    seconds = min(max(seconds, 0.0), MAX_SECONDS)

    def change(found: dict[str, Pause]) -> list[Pause]:
        now = time.time()
        placed = {
            path: Pause(
                path,
                now + seconds if seconds else 0.0,
                by,
                reason,
                events.timestamp(),
                title,
                name,
            )
            for path, title, name in targets
        }
        if len(found.keys() | placed.keys()) > MAX_PAUSES:
            raise CapacityError("pause limit reached")
        found.update(placed)
        return list(placed.values())

    placed = _mutate(change)
    for pause in placed:
        events.record(
            "item_paused",
            path=pause.path,
            title=pause.title or None,
            seconds=round(seconds) or None,
            reason=reason or None,
            by=by or None,
        )
        log.info("pausing %s: %s", pause.path, pause.describe())
    return placed


def place(
    path: str,
    seconds: float = 0.0,
    by: str = "",
    reason: str = "",
    title: str = "",
    name: str = "",
) -> Pause:
    """Place or extend one pause; delegates to the batch transaction."""
    return place_many([(path, title, name)], seconds, by, reason)[0]


def resume_many(targets: Sequence[str], by: str = "") -> list[Pause]:
    """Atomically resume distinct exact paths, returning live pauses removed.

    Exact means the canonical path, so either spelling lifts the same pause.
    """
    wanted = [_key(path) for path in targets]

    def change(found: dict[str, Pause]) -> list[Pause]:
        return [found.pop(path) for path in dict.fromkeys(wanted) if path in found]

    gone = _mutate(change)
    for pause in gone:
        events.record("item_resumed", path=pause.path, title=pause.title or None, by=by or None)
        log.info("pause resumed on %s%s", pause.path, f" by {by}" if by else "")
    return gone


def resume(path: str, by: str = "") -> Pause | None:
    """Resume one exact path, returning its live pause if present."""
    return next(iter(resume_many([path], by)), None)


def full() -> bool:
    """Whether the store is at :data:`MAX_PAUSES`, which refuses another."""
    return len(current()) >= MAX_PAUSES


def as_json() -> list[dict]:
    """Every live pause, as the pages read them."""
    return [pause.as_json() for pause in current()]


def forget() -> None:
    """Drop the memo. For tests, which move STATE_DIR between cases."""
    global _cached
    with _lock:
        _cached = None
