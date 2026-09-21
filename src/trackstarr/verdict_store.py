"""The verdict file itself: its envelope, one read and one write.

Every entry says which build wrote it, so an update keeps the verdicts it can
still use and re-probes only the rest.

A leaf module. Ownership, revision fencing, live views and pruning are
:mod:`trackstarr.sweep_cache`'s, and none of them touch the disk.
"""

import contextlib
import json
import logging
import os
from dataclasses import dataclass, field

from .state import write_json

log = logging.getLogger(__name__)

#: The entry format this build writes, stamped on the document and on every
#: entry in it. Bump it whenever a field is added, removed or changes meaning,
#: since :meth:`trackstarr.sweep_cache.SweepCache.carry` moves unchanged
#: entries forward byte for byte and an added field would never arrive.
FORMAT = 6

#: The oldest entry this build will use. Raise it with FORMAT only where an
#: older entry would be wrong rather than merely thinner, so an update keeps
#: the verdicts it had instead of re-probing the library.
READS_FROM = 6


@dataclass(frozen=True)
class Document:
    """The store as one read found it.

    ``entries`` holds only what this build can use, so no caller has to guess
    which fields another build's entry carries.
    """

    entries: dict[str, dict] = field(default_factory=dict)
    #: Whether a store was there to read. Absence is not damage; the first
    #: verdict can still be written.
    present: bool = False
    #: How many entries this build is too new to use.
    dropped: int = 0
    #: The rule fingerprint they were judged under.
    fingerprint: object = None


def _stamp(value: object) -> int:
    """A format number, or 0 for anything that is not one."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _written_at(entry: dict, document: int) -> int:
    """An entry with no stamp of its own was written by the build the document
    names."""
    return _stamp(entry.get("format")) or document


def load(path: str) -> Document | None:
    """The store, or None where something is there that is not one.

    A missing file reads as an empty document: nothing has swept yet. Damaged
    JSON and a library that is not an object are the same refusal, since
    neither says where a verdict would go. An entry older than
    :data:`READS_FROM` is left behind on its own rather than costing the rest
    of the store.
    """
    try:
        with open(path) as store_file:
            data = json.load(store_file)
    except FileNotFoundError:
        return Document()
    except (OSError, json.JSONDecodeError) as err:
        log.warning("ignoring unreadable verdict store %s: %s", path, err)
        return None
    if not isinstance(data, dict):
        return None
    entries = data.get("files")
    if not isinstance(entries, dict):
        return None
    document = _stamp(data.get("format"))
    usable: dict[str, dict] = {}
    dropped = 0
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        if _written_at(entry, document) < READS_FROM:
            dropped += 1
            continue
        usable[name] = entry
    return Document(usable, present=True, dropped=dropped, fingerprint=data.get("config"))


def write(path: str, fingerprint: dict, entries: dict[str, dict]) -> None:
    """Replace the store with these entries, judged under these rules.

    Raises OSError. The directory is the caller's to make, since a write that
    cannot land is worth reporting rather than working around.
    """
    write_json(path, {"format": FORMAT, "config": fingerprint, "files": entries})


def remove(path: str) -> None:
    """Delete the store. Raises OSError for anything but its absence."""
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)
