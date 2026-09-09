"""Worker log lines kept per file, so the overview can read back what
happened to one without the whole console.

Its own module and its own lock rather than part of the registry: a log
handler waiting on the registry lock would deadlock the first caller that
logs while holding it.
"""

import collections
import logging
import threading

#: Log lines kept per file, and files kept at once. Two hundred lines covers a
#: probe, a plan, an ffmpeg command and its stderr.
_LOG_LINES = 200
_LOGGED_FILES = 200

#: Lines per (run, file), and which file each thread holds.
_lines: dict[tuple[str, str], collections.deque[str]] = {}
_held: dict[int, tuple[str, str]] = {}
_lock = threading.Lock()

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
        with _lock:
            buffer = _lines.get(_held.get(record.thread, ("", "")))
        # Formatted outside the lock; deque.append is atomic.
        if buffer is not None:
            buffer.append(self.format(record))


_capturing = False


def capture() -> None:
    """Start keeping worker log lines.

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


def attach(run_id: str, path: str) -> None:
    """Point this thread's log lines at one file, evicting the oldest file's
    lines past :data:`_LOGGED_FILES`."""
    with _lock:
        _held[threading.get_ident()] = (run_id, path)
        _lines[(run_id, path)] = collections.deque(maxlen=_LOG_LINES)
        while len(_lines) > _LOGGED_FILES:
            del _lines[next(iter(_lines))]


def detach() -> None:
    """Stop pointing this thread's lines at anything. The lines stay: a file's
    log is wanted after its verdict lands."""
    with _lock:
        _held.pop(threading.get_ident(), None)


def lines(run_id: str, path: str) -> list[str]:
    """What was logged while this file was worked on. Empty for a cached
    verdict or for lines since evicted."""
    with _lock:
        buffer = _lines.get((run_id, path))
        return list(buffer) if buffer else []


def forget() -> None:
    """Drop every kept line. For tests."""
    with _lock:
        _lines.clear()
        _held.clear()
