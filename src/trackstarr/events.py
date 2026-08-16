"""Append-only history of what the service did, one JSON line per event.

The sweep report and cache describe the present and are rewritten in place;
``STATE_DIR/events.jsonl`` is the past, the raw feed stats views aggregate.
Recording starts before anything consumes it because history that was never
written down cannot be backfilled. Nothing prunes the file: at one line per
rewrite it grows slower than the logs.

The service only ever appends to that one file, but :func:`read` takes any
``events*.jsonl`` sibling too, so if the file ever grows unwieldy a chunk
can be archived by hand (``events-2026.jsonl``) and history stays whole.
"""

import json
import logging
import os
import threading
from collections.abc import Iterator
from datetime import datetime

from . import __version__, config

log = logging.getLogger(__name__)

#: Rewrites are serialized by the app's rewrite lock, but a sweep summary can
#: land while a webhook rewrite is finishing; this keeps the lines whole.
_write_lock = threading.Lock()


def path() -> str:
    return os.path.join(config.STATE_DIR, "events.jsonl")


def timestamp() -> str:
    """Local time with offset, the format every ``ts`` carries."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record(event: str, **fields) -> None:
    """Append one event, ``ts``, ``event`` and the package version first;
    which release did something is the first question after a bad one.

    None-valued fields are dropped, absence means unknown or not applicable.
    Never raises: losing a line of history is not worth failing the rewrite
    it describes.
    """
    entry = {"ts": timestamp(), "event": event, "version": __version__}
    entry.update({name: value for name, value in fields.items() if value is not None})
    try:
        with _write_lock:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            with open(path(), "a") as events_file:
                events_file.write(json.dumps(entry) + "\n")
    except OSError as err:
        log.warning("could not record %s event: %s", event, err)


def _files() -> list[str]:
    """The live file and any hand-archived chunks, oldest first.

    Filename order is time order for the documented naming: a dash sorts
    before a dot, so dated archives come before events.jsonl itself.
    """
    try:
        names = os.listdir(config.STATE_DIR)
    except OSError:
        return []
    return [
        os.path.join(config.STATE_DIR, name)
        for name in sorted(names)
        if name.startswith("events") and name.endswith(".jsonl")
    ]


def read() -> Iterator[dict]:
    """Every recorded event, oldest file first, in written order.

    A line that does not parse to an object is skipped with a warning, so a
    crash mid-append or a stray hand edit never takes the rest of the
    history with it.
    """
    for file_path in _files():
        try:
            with open(file_path) as events_file:
                for line_no, line in enumerate(events_file, 1):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            raise ValueError("not an object")
                    except ValueError as err:
                        log.warning("skipping %s line %d: %s", file_path, line_no, err)
                        continue
                    yield entry
        except OSError as err:
            log.warning("could not read %s: %s", file_path, err)
