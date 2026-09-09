"""IMDb scores for the shelf, from IMDb's daily ratings dump.

Radarr carries an IMDb rating but Sonarr's is unattributed and not IMDb's, so
series had no score. IMDb publishes the whole table daily, 8MB compressed and
needing no key; this fetches it once a day and keeps the few hundred rows the
library holds. IMDb licences it for personal, non-commercial use, so
IMDB_RATINGS can turn the fetch off.
"""

import gzip
import logging
import os
import threading
import time
from collections.abc import Callable

from . import config, state
from .client import API_ERRORS, stream

log = logging.getLogger(__name__)

#: Every rated title on IMDb, as ``tconst averageRating numVotes``, rebuilt
#: daily.
DATASET_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"

#: Longer than any service call: this is 8MB.
TIMEOUT = 180

#: How often to fetch it again: the source's own daily cadence.
REFRESH_EVERY = 86400.0

#: How long to leave IMDb alone after a failed fetch.
RETRY_AFTER = 3600.0

#: How often the loop wakes to check whether either interval has run out.
TICK = 300.0


def path() -> str:
    return os.path.join(config.STATE_DIR, "imdb-ratings.json")


#: The table as last read, keyed by the file's stamp like the sweep cache.
_cached: tuple[tuple[int, int], dict[str, float]] | None = None
_lock = threading.Lock()

#: When a failed fetch will be retried. Not persisted: a restart tries again.
_retry_at = 0.0


def forget() -> None:
    """Drop the memoised table and any retry backoff, so the next read goes
    back to the file and the next pass is due."""
    global _cached, _retry_at
    with _lock:
        _cached = None
    _retry_at = 0.0


def scores() -> dict[str, float]:
    """Every score we hold, by IMDb id. Empty until the first fetch lands."""
    global _cached
    store = path()
    mark = state.stamp(store)
    with _lock:
        if _cached and _cached[0] == mark:
            return _cached[1]
    found = _read(store)
    with _lock:
        _cached = (mark, found)
    return found


def _read(store: str) -> dict[str, float]:
    """The stored table, or {} if it cannot be read. Never raises."""
    held = state.read_json_records(store).get("scores")
    if not isinstance(held, dict):
        return {}
    return {
        imdb_id: float(score)
        for imdb_id, score in held.items()
        if isinstance(score, int | float) and not isinstance(score, bool)
    }


def of(imdb_id: str) -> float | None:
    """One title's score, or None for a title IMDb has no rating for."""
    return scores().get(imdb_id) if imdb_id else None


def fetched_at() -> float:
    """When the table was last rebuilt, in epoch seconds; 0 for never. The
    file's mtime, rather than the same fact stored twice."""
    written = state.stamp(path())[1]
    return written / 1e9 if written > 0 else 0.0


def summary() -> dict:
    """How many titles have a score, and when the table was last rebuilt."""
    return {"scored": len(scores()), "fetched": fetched_at()}


def due(now: float | None = None) -> bool:
    """Whether it is time to fetch the dataset again."""
    if not config.current().IMDB_RATINGS:
        return False
    now = time.time() if now is None else now
    return now >= _retry_at and now - fetched_at() >= REFRESH_EVERY


def _matching(rows, wanted: set[str]) -> dict[str, float]:
    """The scores for ``wanted`` from the dataset's rows as they stream past.
    The other 1.7 million rows are never collected."""
    found: dict[str, float] = {}
    for row in rows:
        imdb_id, _, rest = row.partition("\t")
        if imdb_id not in wanted:
            continue
        average = rest.partition("\t")[0]
        try:
            found[imdb_id] = round(float(average), 1)
        except ValueError:
            # One title without a score, not a failed refresh.
            log.debug("imdb ratings: %s carries no readable score (%r)", imdb_id, average)
    return found


def refresh(wanted: set[str]) -> int:
    """Fetch the dataset, store the scores for ``wanted``, and return how many
    were found. Raises API_ERRORS, leaving yesterday's table in place."""
    with stream(DATASET_URL, timeout=TIMEOUT) as body, gzip.open(body, "rt") as rows:
        found = _matching(rows, wanted)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    state.write_json(path(), {"scores": found})
    forget()
    log.info("imdb ratings: %d of %d titles have a score", len(found), len(wanted))
    return len(found)


def refresh_now(wanted: set[str] | None) -> bool:
    """One pass of the loop, minus the sleeping. Whether it fetched.

    ``wanted`` is None when an *arr could not be listed, which skips the pass:
    a table built from half a library would drop the other half's scores.
    """
    global _retry_at
    if not due() or not wanted:
        return False
    try:
        refresh(wanted)
    except API_ERRORS as err:
        _retry_at = time.time() + RETRY_AFTER
        log.warning("imdb ratings: could not fetch the dataset (%s), will retry", err)
        return False
    _retry_at = 0.0
    return True


def refresh_pass(ids: Callable[[], set[str] | None], refreshed: Callable[[], None]) -> bool:
    """Fetch if due. ``ids`` is asked only then, so a switched-off install never
    builds a shelf. ``refreshed`` runs after a fetch lands, since asking for
    the ids builds the shelf that the fetch then makes stale."""
    if not refresh_now(ids() if due() else None):
        return False
    refreshed()
    return True


def refresh_loop(
    ids: Callable[[], set[str] | None], refreshed: Callable[[], None]
) -> None:  # pragma: no cover
    """Keep the table within a day of IMDb's. Started whether or not
    IMDB_RATINGS is on, so turning it on needs no restart."""
    while True:
        try:
            refresh_pass(ids, refreshed)
        except Exception:
            log.exception("imdb ratings refresh failed")
        time.sleep(TICK)
