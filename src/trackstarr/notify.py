"""Tell every open page that something changed.

A tab holds ``GET /api/stream`` open and is sent one line naming a kind
whenever that kind changes. The message carries nothing else; the page
refetches what it already fetches, which keeps the ETags working.

The runs registry publishes itself, since it lives in memory. The history and
the sweep cache are files, so :func:`watcher` stats them once a second, which
also covers a ``docker exec trackstarr sweep`` in another process.
"""

import logging
import threading
import time

from . import events, state, sweep_cache

log = logging.getLogger(__name__)

#: What a message can say changed: the registry (refetch /api/runs), the
#: history (/api/events), the stored verdicts (the shelf).
RUNS = "runs"
EVENTS = "events"
LIBRARY = "library"

#: Progress within a run already shown. Its own kind because a run appearing
#: is acted on at once, while progress is paced to what a reader can follow.
PROGRESS = "progress"

#: Every kind. tests/test_contract.py holds the browser's stream.ts to this.
KINDS = (RUNS, EVENTS, LIBRARY, PROGRESS)

#: Most streams held open at once. Each is a server thread for as long as its
#: tab lives, so unbounded, forgotten tabs would hold every thread.
MAX_STREAMS = 32


class Subscription:
    """One stream's pending news: the kinds changed since it last asked. A set,
    so a burst says each thing once."""

    def __init__(self) -> None:
        self.kinds: set[str] = set()

    def take(self, timeout: float) -> set[str]:
        """The kinds published since the last take; empty on timeout, which
        is the cue for a heartbeat."""
        with _news:
            _news.wait_for(lambda: self.kinds, timeout)
            kinds, self.kinds = self.kinds, set()
        return kinds


#: One lock for every subscription: a publish marks each set and wakes the
#: lot. Never held longer than a loop over :data:`MAX_STREAMS` sets.
_news = threading.Condition()
_subscribers: set[Subscription] = set()


def subscribe() -> Subscription | None:
    """A place on the stream, or None when every place is taken."""
    subscription = Subscription()
    with _news:
        if len(_subscribers) >= MAX_STREAMS:
            return None
        _subscribers.add(subscription)
    return subscription


def unsubscribe(subscription: Subscription) -> None:
    with _news:
        _subscribers.discard(subscription)


def publish(kind: str) -> None:
    """Tell every open stream one kind changed. Never blocks the caller."""
    with _news:
        for subscription in _subscribers:
            subscription.kinds.add(kind)
        _news.notify_all()


#: How often the watcher stats its files, which bounds how far a page lags a
#: second process's write.
_WATCH_TICK = 1.0


def _watched() -> list[tuple[str, str]]:
    """The watched files and the kind each announces. Per pass, since tests
    move STATE_DIR."""
    return [(events.path(), EVENTS), (sweep_cache.cache_path(), LIBRARY)]


def watch_once(marks: dict[str, tuple[int, int]]) -> None:
    """One pass over the watched files, publishing whichever moved. ``marks``
    is updated in place; a file not yet in it is baseline, not news."""
    for path, kind in _watched():
        mark = state.stamp(path)
        if path in marks and marks[path] != mark:
            publish(kind)
        marks[path] = mark


# No cover: a thread body around watch_once, which is covered directly.
def watcher() -> None:  # pragma: no cover
    """Stat the history and the sweep cache for ever, publishing what moved.
    The baseline pass is inside the try too, since a volume may be fixed under
    a running service."""
    marks: dict[str, tuple[int, int]] = {}
    while True:
        try:
            watch_once(marks)
        except Exception:
            log.exception("stream watcher failed")
        time.sleep(_WATCH_TICK)
