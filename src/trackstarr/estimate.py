"""How long the rewrites a sweep has found will take.

A rewrite costs roughly the file's running time divided by how fast this
machine gets through one. That multiple varies mostly with how many downmixes
the plan makes, so it is kept per shape: the set of layouts a rewrite
generates, which :func:`trackstarr.processing.downmixed_names` records.

The numbers below are a starting point; the install's own history replaces
them as soon as there is enough of it.
"""

import logging
import statistics
from dataclasses import dataclass, field

from . import config, events
from .status import Status

log = logging.getLogger(__name__)

#: Rewrite speed as a multiple of realtime, by number of downmixes, until the
#: history says otherwise. Guesses: one sweep's history replaces them.
BOOTSTRAP = (300.0, 120.0, 80.0)

#: For a plan making more downmixes than :data:`BOOTSTRAP` has entries.
SLOWEST = 60.0

#: Fewest rewrites of one shape before their median beats the guess.
ENOUGH = 3

#: How far back the history is read: a page at a time until there are enough.
_PAGE = 200
_PAGES = 5

#: Most rewrites of one shape kept, since the oldest describe a machine that
#: may have changed.
_KEEP = 25


def shape(planned: list[dict]) -> tuple[str, ...]:
    """The downmixes a planned rewrite makes, as sorted layout names. From the
    plan's output, so a file that only wants stereo is the one-encode shape."""
    return tuple(
        sorted(
            str(track.get("title") or "")
            for track in planned
            if "generated" in (track.get("flags") or [])
        )
    )


def _guess(made: int) -> float:
    return BOOTSTRAP[made] if made < len(BOOTSTRAP) else SLOWEST


@dataclass(frozen=True)
class Speeds:
    """What rewrites have run at on this machine, keyed by shape. Read once
    per sweep."""

    observed: dict[tuple[str, ...], float] = field(default_factory=dict)

    def of(self, made: tuple[str, ...]) -> float:
        """The multiple of realtime a rewrite of this shape runs at."""
        return self.observed.get(made) or _guess(len(made))

    def seconds(self, duration: float, planned: list[dict]) -> float:
        """Roughly how many wall seconds rewriting one file will take. Zero for
        a file nothing could time."""
        if duration <= 0:
            return 0.0
        return duration / self.of(shape(planned))


def _worked(entry: dict) -> tuple[tuple[str, ...], float] | None:
    """One rewritten file as its shape and speed, or None. The slot wait is
    taken off ``seconds``: only the work scales with the file."""
    duration = entry.get("duration")
    seconds = entry.get("seconds")
    if not isinstance(duration, int | float) or not isinstance(seconds, int | float):
        return None
    waited = entry.get("waited")
    working = seconds - (waited if isinstance(waited, int | float) else 0.0)
    if duration <= 0 or working <= 0:
        return None
    return tuple(sorted(entry.get("downmixed") or [])), duration / working


def measured(pages: int = _PAGES) -> Speeds:
    """What this machine's rewrites have run at, from the history. Shapes with
    too few samples are left to the guess. Never raises."""
    samples: dict[tuple[str, ...], list[float]] = {}
    cursor: int | None = None
    for _ in range(pages):
        entries, cursor = events.read(_PAGE, cursor)
        for entry in entries:
            if entry.get("event") != Status.MODIFIED:
                continue
            if worked := _worked(entry):
                made, speed = worked
                samples.setdefault(made, []).append(speed)
        if cursor is None or (samples and all(len(seen) >= _KEEP for seen in samples.values())):
            break
    observed = {
        made: statistics.median(seen[:_KEEP])
        for made, seen in samples.items()
        if len(seen) >= ENOUGH
    }
    if observed:
        named = ", ".join(
            f"{'+'.join(made) or 'remux'} at {speed:.0f}x" for made, speed in observed.items()
        )
        log.info("rewrite speeds from this install's history: %s", named)
    return Speeds(observed)


def backlog(seconds: float) -> float:
    """How long a queue holding that much rewriting takes to clear across the
    rewrite budget. A floor: a delivery mid-sweep competes for the slots."""
    return seconds / max(1, config.current().MAX_CONCURRENT_REWRITES)
