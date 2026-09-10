"""Titles and files nobody wants rewritten yet, and when that lapses.

:mod:`trackstarr.runs` has one switch for the whole service; this is the
per-title version, for the film somebody is part way through. A hold never
stops a file being probed, planned or reported, only rewritten, so the library
goes on showing the work it is holding back.

Holds are a file, so a ``docker exec trackstarr sweep`` reads the same answer
as the listener, and an evening's hold survives a restart in the middle of it.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass

from . import config, events, notify
from .arr import innermost
from .state import read_json_records, stamp, write_json

log = logging.getLogger(__name__)

#: Where the holds live in STATE_DIR.
HOLDS_FILE = "holds.json"

#: Most holds one answer carries. A hold is placed by hand, so this bounds a
#: mistake rather than ordinary use.
MAX_HOLDS = 200

#: Longest a hold may run for, so a slip of the finger cannot park a title
#: past the point anyone would remember placing it. Four weeks.
MAX_SECONDS = 28 * 86400


@dataclass(frozen=True)
class Hold:
    """One title or file left alone, and until when."""

    #: The folder or file it covers, in this container's paths, as the sweep
    #: cache's keys are. A folder covers everything under it.
    path: str
    #: time.time() when it lapses; 0 for one only a person lifts.
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
        """The hold in a phrase, for a run row and for pending.tsv."""
        who = f" by {self.by}" if self.by else ""
        until = f" until {events.at(self.until)}" if self.until else ""
        because = f" ({self.reason})" if self.reason else ""
        return f"held{who}{until}{because}"

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
_cached: tuple[tuple[int, int], dict[str, Hold]] | None = None


def _path() -> str:
    return os.path.join(config.STATE_DIR, HOLDS_FILE)


def _hold(path: str, record: dict) -> Hold:
    """One stored record as a hold. A field that will not read takes its
    default: a damaged reason must not lose the hold itself."""
    return Hold(
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


def _read() -> dict[str, Hold]:
    """Every hold on disk, lapsed ones included."""
    global _cached
    mark = stamp(_path())
    with _lock:
        if _cached is not None and _cached[0] == mark:
            return _cached[1]
    found = {
        path: _hold(path, record) for path, record in read_json_records(_path()).items() if path
    }
    with _lock:
        _cached = (mark, found)
    return found


def _save(found: dict[str, Hold]) -> None:
    """Write the store and drop the memo. Raises OSError, which the caller
    answers with: a hold the page believes in but nothing enforces is worse
    than a refusal."""
    global _cached
    os.makedirs(config.STATE_DIR, exist_ok=True)
    write_json(
        _path(),
        {
            hold.path: {
                "until": hold.until,
                "by": hold.by,
                "reason": hold.reason,
                "at": hold.at,
                "title": hold.title,
                "name": hold.name,
            }
            for hold in found.values()
        },
    )
    with _lock:
        _cached = None
    # Holds ride the runs snapshot, which every open page already polls.
    notify.publish(notify.RUNS)


def held(path: str, now: float | None = None) -> Hold | None:
    """The hold covering ``path``, or None.

    The innermost live hold wins, so a lapsed hold on one episode does not
    hide a standing one on the series.
    """
    if not path:
        return None
    found = _read()
    if not found:
        return None
    return innermost({at: hold for at, hold in found.items() if not hold.lapsed(now)}, path)


def current(now: float | None = None) -> list[Hold]:
    """Every live hold, newest first. Lapsed ones are dropped from the file as
    they are found, which is the only thing that ever collects them."""
    found = _read()
    live = {path: hold for path, hold in found.items() if not hold.lapsed(now)}
    if len(live) != len(found):
        try:
            _save(live)
        except OSError as err:
            # The lapsed entries answer as gone either way.
            log.warning("could not drop %d lapsed hold(s): %s", len(found) - len(live), err)
    return sorted(live.values(), key=lambda hold: hold.at, reverse=True)


def place(
    path: str,
    seconds: float = 0.0,
    by: str = "",
    reason: str = "",
    title: str = "",
    name: str = "",
) -> Hold:
    """Leave ``path`` alone, for ``seconds`` or until somebody lifts it.

    Replaces any hold already on that exact path, so extending one is the same
    call. Raises OSError where the store cannot be written.
    """
    seconds = min(max(seconds, 0.0), MAX_SECONDS)
    hold = Hold(
        path,
        until=time.time() + seconds if seconds else 0.0,
        by=by,
        reason=reason,
        at=events.timestamp(),
        title=title,
        name=name,
    )
    found = dict(_read())
    found[path] = hold
    _save(found)
    events.record(
        "held",
        path=path,
        title=title or None,
        seconds=round(seconds) or None,
        reason=reason or None,
        by=by or None,
    )
    log.info("holding %s: %s", path, hold.describe())
    return hold


def lift(path: str, by: str = "") -> Hold | None:
    """Take the hold off ``path``; the hold that went, or None for none there.

    The exact path, not the innermost: lifting a series from one episode would
    do more than the press said. Raises OSError where the store cannot be
    written.
    """
    found = dict(_read())
    gone = found.pop(path, None)
    if gone is None:
        return None
    _save(found)
    events.record("lifted", path=path, title=gone.title or None, by=by or None)
    log.info("hold lifted on %s%s", path, f" by {by}" if by else "")
    return gone


def full() -> bool:
    """Whether the store is at :data:`MAX_HOLDS`, which refuses another."""
    return len(current()) >= MAX_HOLDS


def as_json() -> list[dict]:
    """Every live hold, as the pages read them."""
    return [hold.as_json() for hold in current()]


def forget() -> None:
    """Drop the memo. For tests, which move STATE_DIR between cases."""
    global _cached
    with _lock:
        _cached = None
