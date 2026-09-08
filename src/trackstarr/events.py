"""Append-only history, one JSON line per event, in STATE_DIR/events.jsonl.

:func:`read` walks it backwards a page at a time, and a byte offset is a
stable cursor since the file only grows at the end.

``reasons`` and ``incidental`` are prose; ``rules`` and ``incidental_rules``
say the same in :data:`trackstarr.policy.RULE_NAMES`. ``config_id`` is the
policy in force, which ``serve`` and every sweep also record in full.
"""

import json
import logging
import os
import secrets
import threading
from datetime import datetime

from . import __version__, config
from .policy import Policy

log = logging.getLogger(__name__)

#: Keeps lines whole when two threads record at once.
_write_lock = threading.Lock()

#: How much of the file one backwards step reads. A hundred events is a few
#: tens of KB, so the usual page is one read.
_CHUNK = 64 << 10

#: Most events one :func:`read` may answer with.
MAX_READ = 500


def path() -> str:
    return os.path.join(config.STATE_DIR, "events.jsonl")


def timestamp() -> str:
    """Local time with offset, the format every ``ts`` carries."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def at(when: float) -> str:
    """An epoch moment in the same format, for a stamp that is not now."""
    return datetime.fromtimestamp(when).astimezone().isoformat(timespec="seconds")


def run_id() -> str:
    """A run id: the start time, made unique by a suffix, so runs sort by when
    they began."""
    return f"{timestamp()}#{secrets.token_hex(2)}"


def record(event: str, **fields) -> None:
    """Append one event. None-valued fields are dropped. Never raises: a lost
    line is not worth failing the rewrite it describes."""
    entry = {"ts": timestamp(), "event": event, "version": __version__}
    entry.update({name: value for name, value in fields.items() if value is not None})
    try:
        with _write_lock:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            with open(path(), "a") as events_file:
                events_file.write(json.dumps(entry) + "\n")
    except OSError as err:
        log.warning("could not record %s event: %s", event, err)


#: How far back :func:`record_config` looks for the rules already in force.
#: Missing an older line costs a duplicate, not a wrong answer.
_CONFIG_LOOKBACK = 200


def record_config(policy: Policy) -> bool:
    """Record the rules in force unless the history already pins them, so a
    restart under unchanged rules adds no line. Returns whether one was
    written."""
    digest = policy.digest()
    if _pinned_config_id() == digest:
        return False
    record("config", config=policy.fingerprint(), config_id=digest)
    return True


def _pinned_config_id() -> str | None:
    """The config_id of the newest event carrying a full fingerprint. Sweep
    summaries count, so a nightly sweep keeps the digest resolvable."""
    entries, _ = read(limit=_CONFIG_LOOKBACK)
    for entry in entries:
        if isinstance(entry.get("config"), dict):
            config_id = entry.get("config_id")
            return config_id if isinstance(config_id, str) else None
    return None


def _entry(line: bytes) -> dict | None:
    """One line as an event, or None for anything that is not one."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def moment(text: str) -> datetime:
    """Parse an ISO 8601 window bound. A stamp with no offset is read in the
    service's own clock, which wrote the history. Raises ValueError."""
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.astimezone()


def _when(entry: dict) -> datetime | None:
    """When an event happened, or None. Compared as moments, not strings:
    stamps either side of a daylight-saving change carry different offsets."""
    ts = entry.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        return moment(ts)
    except ValueError:
        return None


def read(
    limit: int = 100,
    before: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[dict], int | None]:
    """A page of history, newest first, and the cursor for the next page.

    Walks backwards from ``before``, a byte offset from an earlier call, or
    from the end of the file. Only the bytes a page needs are read. The cursor
    is where the page's oldest event begins; None means nothing older is in
    range. Unparseable lines are stepped over: a read racing an append sees
    the last line half written.

    ``since`` is cheap: the first line older than it ends the walk. ``until``
    costs a scan back past everything newer, once; paging after that is
    ordinary. An event with an unparseable stamp is kept whatever the window.
    Never raises.
    """
    # Each event with its offset, since the cursor is the oldest kept.
    found: list[tuple[int, dict]] = []
    # One line past the page says whether there is another; inside a window
    # the file running out cannot.
    want = limit + 1
    # Set once the walk passes the near edge of the window.
    ran_out = False
    try:
        with open(path(), "rb") as history:
            history.seek(0, os.SEEK_END)
            size = history.tell()
            # A cursor from before a truncation, or one somebody typed.
            end = size if before is None else min(before, size)
            # The front of the last chunk, usually the tail of a line whose
            # start lies in the chunk before.
            carry = b""
            while end > 0 and len(found) < want and not ran_out:
                start = max(0, end - _CHUNK)
                history.seek(start)
                block = history.read(end - start) + carry
                end = start
                lines = []
                at = start
                for line in block.split(b"\n"):
                    lines.append((at, line))
                    at += len(line) + 1
                # The first line of a chunk that is not the file's first is a
                # tail; it goes on the front of the next block.
                carry = lines.pop(0)[1] if start else b""
                for at, line in reversed(lines):
                    if len(found) == want:
                        break
                    entry = _entry(line)
                    if entry is None:
                        continue
                    when = _when(entry)
                    if when is not None:
                        # Still walking down towards the window.
                        if until is not None and when > until:
                            continue
                        if since is not None and when < since:
                            ran_out = True
                            break
                    found.append((at, entry))
    except FileNotFoundError:
        return [], None
    except OSError as err:
        log.warning("could not read the history: %s", err)
        return [], None
    # The line past the page proves there is another.
    page = found[:limit]
    cursor = page[-1][0] if len(found) > limit else None
    return [entry for _, entry in page], cursor
