"""The atomic JSON read and write the on-disk stores share. A leaf module."""

import json
import logging
import os

log = logging.getLogger(__name__)


def write_json(path: str, payload, mode: int = 0o666) -> None:
    """Replace ``path`` with ``payload`` as JSON, atomically. Raises OSError.

    The staging name carries the pid, since serve and the CLI can rewrite the
    same store. ``mode`` is the created file's permission bits before umask.
    """
    partial = f"{path}.{os.getpid()}.tmp"
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w") as out_file:
        json.dump(payload, out_file)
    os.replace(partial, path)


def stamp(path: str) -> tuple[int, int]:
    """The file's size and mtime as a comparable mark, or (-1, -1) when
    missing, so absence is memoised too."""
    try:
        stat_result = os.stat(path)
    except OSError:
        return (-1, -1)
    return stat_result.st_size, stat_result.st_mtime_ns


def read_json_records(path: str) -> dict[str, dict]:
    """``path`` as a JSON object of objects; {} when missing or damaged.
    Non-object members are dropped. Never raises."""
    try:
        with open(path) as records_file:
            data = json.load(records_file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        log.warning("ignoring unreadable %s: %s", path, err)
        return {}
    if not isinstance(data, dict):
        log.warning("ignoring %s: it does not hold one JSON object", path)
        return {}
    return {name: record for name, record in data.items() if isinstance(record, dict)}
