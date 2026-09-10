"""What a rewrite of ours left each file, kept where the verdicts cannot take
it with them.

A rewritten file passes from then on, so this record is the only thing telling
it from one the rules never touched. It used to ride the verdict in the sweep
cache, which is dropped whole whenever the rules change: an evening's setting
cost the library every rewrite it had ever made.

Each record claims the file by the size and mtime we left it at, so a rewrite
by anything else drops the claim without touching the record.
"""

import logging
import os
import threading
from dataclasses import dataclass

from . import config
from .state import read_json_records, stamp, write_json
from .sweep_cache import FileKey

log = logging.getLogger(__name__)

#: Where the records live in STATE_DIR.
REWRITES_FILE = "rewrites.json"


@dataclass(frozen=True)
class Rewrite:
    """One file as a rewrite of ours left it.

    ``made`` is :func:`trackstarr.processing._modified`, which is all a page
    reads; the size and mtime are the claim on the file.
    """

    size: int
    mtime_ns: int
    made: dict


#: Guards the memo below.
_lock = threading.Lock()

#: Makes read, change and write one step, or two rewrites landing at once would
#: lose whichever booked first.
_write_lock = threading.Lock()

#: The store as last read, with the mark of the file it came from. A library
#: page asks for it once per request, and the CLI writes the same file.
_cached: tuple[tuple[int, int], dict[str, Rewrite]] | None = None


def store_path() -> str:
    """The store itself. Named apart from the file paths it is keyed by."""
    return os.path.join(config.STATE_DIR, REWRITES_FILE)


def _numeric(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _rewrite(record: dict) -> Rewrite | None:
    """One stored record, or None for one with no claim: without the size and
    mtime nothing could tell the file from a later one."""
    if not _numeric(record.get("size")) or not _numeric(record.get("mtime_ns")):
        return None
    made = record.get("made")
    return Rewrite(
        int(record["size"]), int(record["mtime_ns"]), made if isinstance(made, dict) else {}
    )


def _read() -> dict[str, Rewrite]:
    """Every record on disk, whether or not its file still stands."""
    global _cached
    mark = stamp(store_path())
    with _lock:
        if _cached is not None and _cached[0] == mark:
            return _cached[1]
    found = {
        path: rewrite
        for path, record in read_json_records(store_path()).items()
        if path and (rewrite := _rewrite(record)) is not None
    }
    with _lock:
        _cached = (mark, found)
    return found


def _save(found: dict[str, Rewrite]) -> None:
    """Write the store and drop the memo. Never raises: a lost record is not
    worth failing the rewrite it describes."""
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        write_json(
            store_path(),
            {
                path: {"size": rewrite.size, "mtime_ns": rewrite.mtime_ns, "made": rewrite.made}
                for path, rewrite in found.items()
            },
        )
    except OSError as err:
        log.warning("could not write %s: %s", store_path(), err)
        return
    forget()


def _stands(path: str) -> bool:
    """Whether the file is still there. A path whose folder has gone is kept:
    an unmounted library must not read as a library of deleted files."""
    return os.path.exists(path) or not os.path.isdir(os.path.dirname(path))


def record(path: str, key: FileKey | None, made: dict) -> None:
    """Book what a rewrite left at ``path``, collecting records whose files
    have gone. ``key`` None books nothing: nothing could then tell the file
    from a later one."""
    if key is None:
        return
    with _write_lock:
        standing = {
            name: rewrite for name, rewrite in _read().items() if name != path and _stands(name)
        }
        _save({**standing, path: Rewrite(key.size, key.mtime_ns, made)})


def rekey(path: str, key: FileKey | None) -> None:
    """Move a record onto the file as it now stands, for an edit of ours that
    changed it. Never invents one."""
    if key is None:
        return
    with _write_lock:
        found = _read()
        rewrite = found.get(path)
        if rewrite is None or (rewrite.size, rewrite.mtime_ns) == (key.size, key.mtime_ns):
            return
        _save({**found, path: Rewrite(key.size, key.mtime_ns, rewrite.made)})


def drop(path: str) -> None:
    """Forget one file, for the source a remux published under another name."""
    with _write_lock:
        found = _read()
        if path not in found:
            return
        _save({name: rewrite for name, rewrite in found.items() if name != path})


def records() -> dict[str, Rewrite]:
    """Every record on disk. Its identity is stable while the file is, so a
    caller memoising on its inputs can key on it."""
    return _read()


def against(entries: dict[str, dict]) -> dict[str, dict]:
    """What a rewrite of ours left each of ``entries``, keyed by path.

    ``entries`` are sweep cache entries, which carry the size and mtime the
    claim is checked against, so nothing is stat'd here. A file something else
    rewrote since keeps its entry and loses its record.
    """
    found = _read()
    matched = {}
    for path, entry in entries.items():
        rewrite = found.get(path)
        if rewrite is None:
            continue
        if (rewrite.size, rewrite.mtime_ns) == (entry.get("size"), entry.get("mtime_ns")):
            matched[path] = rewrite.made
    return matched


def forget() -> None:
    """Drop the memo. Two tmp dirs can share a file mark, so the tests call
    it."""
    global _cached
    with _lock:
        _cached = None
