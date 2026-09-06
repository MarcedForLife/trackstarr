"""Remember each file's verdict between sweeps.

Size and mtime are enough to skip re-probing thousands of unchanged files at
50-200ms each. The whole cache is dropped when
:meth:`trackstarr.policy.Policy.fingerprint` changes; deleting the file forces
a full re-probe.

Verdicts carry the probed track summaries, so the cache doubles as the library
index. :func:`update` lets an import or a ``fix`` book one verdict without a
walk's bookkeeping, and :func:`live` and :func:`live_view` let the library read
a running walk's verdicts ahead of its next checkpoint.

Which verdicts are safe to cache is the sweep's call; see
:data:`trackstarr.sweep.CACHEABLE_STATUSES`.
"""

import contextlib
import itertools
import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field

from . import config
from .state import write_json
from .status import Status

log = logging.getLogger(__name__)

#: The entry format. Bump it whenever a field is added, removed or changes
#: meaning: ``carry`` moves unchanged entries forward byte for byte, so an added
#: field would otherwise never arrive. A mismatch drops the cache whole; the
#: rebuild is a sweep.
FORMAT = 4


def cache_path() -> str:
    """The cache file, under STATE_DIR."""
    return os.path.join(config.STATE_DIR, "sweep-cache.json")


@dataclass(frozen=True)
class FileKey:
    """Everything file-side a verdict depends on.

    Taken before the probe, so a change landing mid-probe leaves a stale entry
    rather than filing the new file under the old verdict. The link count is
    included because a SKIP_HARDLINKS verdict changes when the download client
    releases the file, which moves neither size nor mtime.
    """

    size: int
    mtime_ns: int
    nlink: int
    lang: str | None


@dataclass(frozen=True)
class Verdict:
    """What a sweep concluded about a file.

    ``tracks`` is the probe's :func:`trackstarr.media.track_summary` dicts,
    empty when never probed. ``planned`` is what a rewrite would leave, only on
    would-fix. ``why`` is :func:`trackstarr.planner.why`, so the library can
    list the changes and rules.
    """

    status: Status
    reasons: str = ""
    tracks: list[dict] = field(default_factory=list)
    planned: list[dict] = field(default_factory=list)
    why: dict = field(default_factory=dict)
    #: What a rewrite of this file did, once it has one: when, the sizes either
    #: side, and the streams it moved. Empty on a file nothing has rewritten,
    #: which is what an entry written before this field existed also reads as.
    #: See :func:`trackstarr.processing._rejudged`.
    fixed: dict = field(default_factory=dict)
    #: Running time in seconds, zero when never probed. What
    #: :mod:`trackstarr.estimate` sizes a backlog from.
    duration: float = 0.0
    #: Consecutive failed rewrites of the file as it stands. See
    #: :data:`trackstarr.sweep.MAX_FAILURES`.
    failures: int = 0


@dataclass(frozen=True)
class Stored:
    """The cache file as a non-sweep reader sees it.

    ``current`` is False when the verdicts were judged under rules that have
    since changed. Still worth showing, with that said.
    """

    files: dict[str, dict]
    current: bool


def read(path: str, fingerprint: dict) -> Stored:
    """Every entry the cache holds, stale or not. Never raises.

    Unlike :meth:`SweepCache.load`, a stale cache is kept: it is the whole of
    what a view has to show.
    """
    try:
        with open(path) as cache_file:
            data = json.load(cache_file)
    except FileNotFoundError:
        return Stored({}, True)
    except (OSError, json.JSONDecodeError) as err:
        log.warning("ignoring unreadable sweep cache %s: %s", path, err)
        return Stored({}, True)
    # Another build's format is an empty index, not a guessed one. The next
    # sweep rebuilds it.
    if data.get("format") != FORMAT:
        return Stored({}, True)
    entries = data.get("files")
    return Stored(
        {name: entry for name, entry in entries.items() if isinstance(entry, dict)}
        if isinstance(entries, dict)
        else {},
        data.get("config") == fingerprint,
    )


def _count(value: object) -> int:
    """A stored failure count, or 0 for anything that is not one."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def cache_key(path: str, lang: str | None) -> FileKey | None:
    """The file's FileKey, or None if unreadable. None is never cached."""
    try:
        stat_result = os.stat(path)
    except OSError:
        return None
    return FileKey(stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_nlink, lang)


def _carried_fix(previous: dict | None, key: FileKey) -> dict:
    """The rewrite the stored entry remembers, where the file has not changed
    since.

    A re-check re-probes a file it rewrote earlier and reaches the same
    verdict; without this the second verdict would drop the record and the
    library would call the file untouched.
    """
    if not isinstance(previous, dict) or not asdict(key).items() <= previous.items():
        return {}
    fixed = previous.get("fixed")
    return fixed if isinstance(fixed, dict) else {}


def _entry(key: FileKey, verdict: Verdict, previous: dict | None = None) -> dict:
    """One stored entry, as both a sweep and a delivery write it.

    Empty extras are dropped, since most entries carry none and the file is
    rewritten whole at every checkpoint. ``judged`` is stamped here because
    :meth:`SweepCache.carry` moves entries forward untouched, so it keeps
    saying when the file was last opened. ``previous`` is what stands at this
    path, for :func:`_carried_fix`.
    """
    extras = {
        "tracks": verdict.tracks,
        "planned": verdict.planned,
        "why": verdict.why,
        "fixed": verdict.fixed or _carried_fix(previous, key),
        "failures": verdict.failures,
        "duration": verdict.duration,
    }
    return {
        **asdict(key),
        "status": str(verdict.status),
        "reasons": verdict.reasons,
        # Whole seconds: it is sorted on and never counted with.
        "judged": int(time.time()),
        **{name: extra for name, extra in extras.items() if extra},
    }


#: Makes read, change and write one step, or two deliveries booking at once
#: would lose whichever wrote first.
_update_lock = threading.Lock()


def update(path: str, key: FileKey | None, verdict: Verdict | None, fingerprint: dict) -> None:
    """Book one file's verdict, leaving every other entry alone. Never raises.

    For a webhook delivery, where a sweep uses :class:`SweepCache`. ``verdict``
    None drops the stored entry, as a rewritten or deferred file requires.
    Which verdicts are safe to store is the sweep's call; see
    :data:`trackstarr.sweep.CACHEABLE_STATUSES`. The whole file is rewritten,
    which is a few hundred milliseconds.
    """
    with _update_lock:
        data = _for_update(path, fingerprint)
        if data is None:
            return
        entries = data["files"]
        if verdict is None:
            if entries.pop(path, None) is None:
                # Nothing stored, so nothing to rewrite.
                return
        elif key is None:
            # Nothing to key by, so nothing could tell whether the file changed.
            return
        else:
            entries[path] = _entry(key, verdict, entries.get(path))
        try:
            # A delivery can be the first thing this install writes.
            os.makedirs(config.STATE_DIR, exist_ok=True)
            write_json(cache_path(), data)
        except OSError as err:
            log.warning("could not write the sweep cache for %s: %s", path, err)


def _for_update(path: str, fingerprint: dict) -> dict | None:
    """The cache as :func:`update` may change it, or None to leave it alone.

    Another build's format, or verdicts under rules since changed, is a cache
    the next sweep drops whole; a current verdict cannot be filed among them.
    """
    try:
        with open(cache_path()) as cache_file:
            data = json.load(cache_file)
    except FileNotFoundError:
        # Nothing has swept yet; one verdict is still worth showing.
        return {"format": FORMAT, "config": fingerprint, "files": {}}
    except (OSError, json.JSONDecodeError) as err:
        log.warning("could not read the sweep cache to book %s: %s", path, err)
        return None
    if data.get("format") != FORMAT or data.get("config") != fingerprint:
        return None
    entries = data.get("files")
    if not isinstance(entries, dict):
        return None
    data["files"] = entries
    return data


#: Numbers the published views, process-wide and only climbing: a reader memoises
#: on the file's stamp plus the generation, and a per-walk counter could repeat.
_generations = itertools.count(1)


class SweepCache:
    """Verdicts from previous sweeps, keyed by path.

    ``carry`` and ``record`` build the next sweep's contents, so unvisited
    entries fall away on ``save``; ``checkpoint`` persists mid-sweep without
    pruning. ``fingerprint`` is the policy the verdicts were judged under, and
    a mismatch on load drops the cache.
    """

    def __init__(self, path: str, fingerprint: dict):
        self.path = path
        self.fingerprint = fingerprint
        self._previous: dict[str, dict] = {}
        self._next: dict[str, dict] = {}
        #: Files this walk judged and stored nothing for, so ``keep`` does not
        #: carry their old verdict forward.
        self._dropped: set[str] = set()
        #: Whether anything fresh landed since the last write; see checkpoint.
        self._dirty = False
        #: The last view offered to readers, as (generation, entries). See
        #: :meth:`publish_view`.
        self._snapshot: tuple[int, dict[str, dict]] | None = None
        #: Fresh verdicts so far, and how many the snapshot was built from.
        #: Separate from ``_dirty`` because checkpoints and publishes run at
        #: their own paces.
        self._changes = 0
        self._published = 0

    @classmethod
    def load(cls, path: str, fingerprint: dict) -> SweepCache:
        cache = cls(path, fingerprint)
        try:
            with open(path) as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            return cache
        except (OSError, json.JSONDecodeError) as err:
            log.warning("ignoring unreadable sweep cache %s: %s", path, err)
            return cache
        if data.get("format") != FORMAT:
            log.info("sweep cache is an older format, dropping it for a full re-probe")
            return cache
        if data.get("config") != fingerprint:
            log.info("rule configuration changed, dropping the sweep cache")
            return cache
        entries = data.get("files")
        if isinstance(entries, dict):
            cache._previous = entries
        return cache

    def lookup(self, path: str, key: FileKey | None) -> Verdict | None:
        """The stored verdict for an unchanged file, or None.

        Tracks and the rewrite record are not decoded, since ``carry`` moves
        the entry forward whole and :func:`_carried_fix` keeps the record on a
        verdict reached again.
        ``why``, ``planned`` and ``duration`` are: a hit out of retries must
        say what went wrong, and an estimate needs the other two.
        """
        entry = self._previous.get(path)
        if key is None or not isinstance(entry, dict):
            return None
        if not asdict(key).items() <= entry.items():
            return None
        try:
            # "" makes a missing status as invalid as a damaged one.
            stored_why = entry.get("why")
            planned = entry.get("planned")
            duration = entry.get("duration")
            return Verdict(
                Status(entry.get("status", "")),
                entry.get("reasons") or "",
                planned=planned if isinstance(planned, list) else [],
                why=stored_why if isinstance(stored_why, dict) else {},
                failures=_count(entry.get("failures")),
                duration=float(duration) if isinstance(duration, int | float) else 0.0,
            )
        except ValueError:
            # A hand-edited or damaged entry; treat it as a miss.
            return None

    def failures(self, path: str, key: FileKey | None) -> int:
        """Failed rewrites of this file as it now stands. Keyed like a lookup,
        so a change on disk starts the count over."""
        entry = self._previous.get(path)
        if key is None or not isinstance(entry, dict):
            return 0
        if not asdict(key).items() <= entry.items():
            return 0
        return _count(entry.get("failures"))

    def record(self, path: str, key: FileKey | None, verdict: Verdict) -> None:
        if key is None:
            return
        self._next[path] = _entry(key, verdict, self._previous.get(path))
        self._dropped.discard(path)
        self._dirty = True
        self._changes += 1

    def drop(self, path: str) -> None:
        """Record that this walk judged the file and stored nothing for it.

        Without this a rewritten file's old entry would survive every
        checkpoint and the library would call it Pending for the whole walk.
        """
        self._dropped.add(path)
        # This walk's own entries too: an applying sweep stores a would-fix
        # while the rewrite waits, and the rewrite then drops the file.
        self._next.pop(path, None)
        self._dirty = True
        self._changes += 1

    def carry(self, path: str) -> None:
        """Bring a hit's stored entry forward untouched. Already on disk, so
        this does not mark the cache dirty."""
        if entry := self._previous.get(path):
            self._next[path] = entry

    def checkpoint(self) -> None:
        """Persist mid-sweep, so an interrupted sweep keeps what it learned.
        Skipped while nothing fresh has been recorded."""
        if self._dirty:
            self.keep()

    def keep(self) -> None:
        """Persist without pruning, for a walk that stopped early or covered
        part of the library. Files this walk dropped still go."""
        self._write(self._standing())
        self._dirty = False

    def _standing(self) -> dict[str, dict]:
        """Every entry that still stands: the previous walk's, less this walk's
        drops, plus its verdicts. Shared by :meth:`keep` and
        :meth:`publish_view` so they cannot disagree."""
        stands = {
            path: entry for path, entry in self._previous.items() if path not in self._dropped
        }
        return {**stands, **self._next}

    def publish_view(self) -> bool:
        """Offer this walk's verdicts so far to readers; whether anything was
        new. False on a warm sweep, where ``carry`` counts as no change.

        Safe without a lock: one writer, one reference swap, and a reader takes
        the (generation, entries) pair in a single attribute load. Nothing
        inside is mutated after it is built.
        """
        if self._changes == self._published:
            return False
        self._published = self._changes
        self._snapshot = (next(_generations), self._standing())
        return True

    def save(self) -> None:
        """Persist a completed walk, dropping entries it never visited."""
        self._write(self._next)

    def _write(self, entries: dict[str, dict]) -> None:
        try:
            write_json(
                self.path,
                {"format": FORMAT, "config": self.fingerprint, "files": entries},
            )
        except OSError as err:
            log.warning("could not write sweep cache %s: %s", self.path, err)


#: The cache a walk is holding open, for readers ahead of its next checkpoint.
#: One slot, since the API and the scheduler both refuse a second walk.
_live: SweepCache | None = None


@contextlib.contextmanager
def live(cache: SweepCache) -> Iterator[None]:
    """Let readers see ``cache``'s published views for the length of a walk.

    Cleared however the walk ends. On a normal exit the file is written before
    this releases; on an exception readers are back on the last checkpoint.
    """
    global _live
    if _live is not None:
        # Logged, not raised: something upstream should have refused the second
        # walk, and the newer one is the better to read.
        log.warning("a walk is already holding the sweep cache; replacing it")
    _live = cache
    try:
        yield
    finally:
        _live = None


def live_view(fingerprint: dict) -> tuple[int, dict[str, dict]] | None:
    """A running walk's verdicts, or None to read the file: no walk, nothing
    published yet, or a walk under rules the caller is not asking about."""
    cache = _live
    if cache is None or cache.fingerprint != fingerprint:
        return None
    return cache._snapshot
