"""Remember each file's verdict between sweeps.

Size and mtime are enough to skip re-probing thousands of unchanged files at
50-200ms each. The whole cache is dropped when
:meth:`trackstarr.policy.Policy.fingerprint` changes; deleting the file forces
a full re-probe.

Verdicts carry the probed track summaries, so the cache doubles as the library
index. :func:`publish` lets an import or a ``fix`` book one verdict without a
walk's bookkeeping, and :func:`live` and :func:`live_view` let the library read
a running walk's verdicts ahead of its next checkpoint.

Nothing here outlives a rule change. What a rewrite of ours did is history and
lives in :mod:`trackstarr.rewrites` for that reason.

Which verdicts are safe to cache is the sweep's call; see
:data:`trackstarr.sweep.CACHEABLE_STATUSES`.

The file itself is :mod:`trackstarr.verdict_store`. Everything here is the
in-process half: who owns a path, which readers a mutation fences, what a
running walk may show before its next checkpoint, and which publications are
still waiting to be written. See :func:`flush`.
"""

import contextlib
import functools
import itertools
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import cast

from . import config, verdict_store
from .policy import Policy
from .status import Status

log = logging.getLogger(__name__)


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
    a pending file. ``why`` is :func:`trackstarr.planner.why`, so the library
    can list the changes and rules.
    """

    status: Status
    reasons: str = ""
    tracks: list[dict] = field(default_factory=list)
    planned: list[dict] = field(default_factory=list)
    why: dict = field(default_factory=dict)
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
    what a view has to show. Nothing to read is an empty index rather than a
    stale one, since no rules were changed out from under it. A publication
    still waiting to be written answers from memory, so deferring the write
    changes nothing a reader in this process sees.
    """
    with _batched:
        if (batch := _pending.get(path)) is not None:
            return Stored(dict(batch.entries), batch.fingerprint == fingerprint)
    document = verdict_store.load(path)
    if document is None or not (document.present and document.known):
        return Stored({}, True)
    return Stored(document.entries, document.fingerprint == fingerprint)


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


def _entry(key: FileKey, verdict: Verdict) -> dict:
    """One stored entry, as both a sweep and a delivery write it.

    Empty extras are dropped, since most entries carry none and the file is
    rewritten whole at every checkpoint. ``judged`` is stamped here because
    :meth:`SweepCache.carry` moves entries forward untouched, so it keeps
    saying when the file was last opened.
    """
    extras = {
        "tracks": verdict.tracks,
        "planned": verdict.planned,
        "why": verdict.why,
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


#: State is never held across filesystem I/O. Writers serialize full JSON
#: transactions separately, acquiring writer -> state, never the reverse.
_update_lock = threading.RLock()
_writer = threading.RLock()

#: How long a publication waits for company, and the longest one may go
#: unwritten whatever keeps arriving. Recording a verdict costs a parse and a
#: full rewrite of the library, so a season pack or a retag across a title
#: would otherwise pay that once per file. A crash inside the window costs a
#: re-probe of what it held, not correctness.
_COALESCE_SECONDS = 0.25
_COALESCE_LIMIT = 2.0


@dataclass
class _Batch:
    """Publications the store has not been rewritten for yet.

    Read under the state mutex and only ever changed under the writer lock, so
    a reader sees a publication's whole mutation or none of it.
    """

    fingerprint: dict
    entries: dict[str, dict]
    #: When the write is due: the sooner of the quiet window and the limit.
    due: float
    limit: float


#: Unwritten batches by store path. Every reader in this process answers from
#: here first, so only another process sees the older file.
_pending: dict[str, _Batch] = {}
#: Wakes the flusher when a batch arrives and when one lands.
_batched = threading.Condition(_update_lock)
#: Runs only while something is waiting to be written, so an idle service and
#: a finished test carry no thread.
_flusher: threading.Thread | None = None


def _synchronized[**P, T](call: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(call)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        with _update_lock:
            return call(*args, **kwargs)

    return guarded


@dataclass
class _Revision:
    version: int = 0
    users: int = 0
    #: The observation changing this file. Reads wait for it and nobody else
    #: may publish a verdict for the path while it is set.
    owner: Observation | None = None


# Only observed paths occupy this map. The last observer releases its revision
# even on failure; completed files and an idle service retain no tokens.
_revisions: dict[tuple[str, str], _Revision] = {}
#: Woken when a mutation gives its paths back, for the reads queued behind it.
_released = threading.Condition(_update_lock)

#: How often a cancellable wait for another mutation looks at its skip. Nothing
#: wakes it on a skip, and contention is two threads meeting on one file.
_OWNER_POLL_SECONDS = 0.1


class ObservationStoppedError(Exception):
    """A cancelled reader never obtained a safe view of the file."""


class Observation:
    """A file observation, from before stat/probe/edit through publication.

    Revisions fence cooperating threads, including changes that preserve the
    file key. They do not serialize independent CLI/listener processes: their
    full JSON checkpoints can still overwrite one another. Rewrite slot flocks
    do not make cache writes transactional across processes.

    :meth:`changing` takes the paths a mutation is about to write, which is
    where earlier observations are fenced. Everything before that is a read.
    """

    def __init__(self, cache_file: str, policy: Policy | None = None):
        self.cache_file = cache_file
        self.policy = policy or Policy.from_config()
        self.paths: dict[str, tuple[_Revision, int]] = {}
        #: The source and output this observation is changing, once it has
        #: taken them. See :meth:`changing`.
        self.changes: tuple[str, str] | None = None
        #: Whether the file moved under us, so what is stored for it describes
        #: a file that is gone. See :meth:`changed`.
        self.wrote = False
        #: Whether a verdict of ours was accepted, so a mutation that published
        #: needs no invalidating on the way out.
        self.published = False
        self.closed = False

    @_synchronized
    def watch(self, path: str, stopped: Callable[[], bool] | None = None) -> bool:
        """Capture a destination before any filesystem mutation touches it.

        A file another observation is changing is waited for: a probe begun
        mid-edit would describe a file that no longer exists by the time it
        had a verdict to publish.
        """
        if path in self.paths:
            return True
        if not self._settle({path}, stopped):
            return False
        self._hold(path)
        return True

    @_synchronized
    def changing(
        self, path: str, output: str = "", stopped: Callable[[], bool] | None = None
    ) -> bool:
        """Take the files this observation is about to write; whether it may go
        on. False only where a skip arrived while it waited.

        Both paths at once and neither held while waiting for the other, so a
        remux cannot deadlock against one going the other way. Earlier
        observations are fenced here rather than at publication: an edit that
        preserves size and mtime is invisible to the file key, so a probe from
        before it would otherwise publish over the verdict that read it.
        """
        paths = {path, output or path}
        if not self._settle(paths, stopped):
            return False
        for name in paths:
            revision = self._hold(name)
            revision.owner = self
            revision.version += 1
            self.paths[name] = revision, revision.version
        self.changes = path, output or path
        return True

    @_synchronized
    def released(self) -> None:
        """Give the files back where the tool would not touch them after all.

        The fence stands: a reader waiting on us has been waiting for a version
        it must re-read anyway. Only the ownership goes, so whatever the caller
        does instead is not doing it holding the whole file.
        """
        for path in self.changes or ():
            revision, _ = self.paths[path]
            revision.owner = None
        self.changes = None
        _released.notify_all()

    def changed(self) -> None:
        """Say the tool has begun writing, so a verdict that never arrives
        takes the stored one with it: a refused edit leaves the entry alone,
        a half-written header cannot."""
        self.wrote = True

    def accepts(self, paths: set[str]) -> bool:
        return not self.closed and all(self._current(path) for path in paths)

    def _current(self, path: str) -> bool:
        watched = self.paths.get(path)
        if watched is None:
            return False
        revision, version = watched
        return revision.version == version and revision.owner in (None, self)

    def _hold(self, path: str) -> _Revision:
        """Reference the path's revision and stamp what it stands at."""
        revision = _revisions.setdefault((self.cache_file, path), _Revision())
        if path not in self.paths:
            revision.users += 1
        self.paths[path] = revision, revision.version
        return revision

    def _settle(self, paths: set[str], stopped: Callable[[], bool] | None = None) -> bool:
        """Wait out any other observation changing these paths; whether it
        finished rather than a skip arriving. The lock is released while it
        waits, so the owner can publish and let go."""
        while self._taken(paths):
            if stopped is not None and stopped():
                return False
            _released.wait(_OWNER_POLL_SECONDS if stopped is not None else None)
        return True

    def _taken(self, paths: set[str]) -> bool:
        return any(
            (revision := _revisions.get((self.cache_file, path))) is not None
            and revision.owner is not None
            and revision.owner is not self
            for path in paths
        )

    def close(self) -> None:
        # Invalidation may persist, so it runs before taking the state mutex.
        try:
            if self.changes and self.wrote and not self.published:
                source, output = self.changes
                publish(self, source, output, None, None, self.policy.fingerprint())
        finally:
            with _update_lock:
                self.closed = True
                for path, (revision, _) in self.paths.items():
                    revision.users -= 1
                    if revision.owner is self:
                        revision.owner = None
                    if not revision.users:
                        del _revisions[self.cache_file, path]
                _released.notify_all()


@contextlib.contextmanager
def observing(
    path: str,
    cache_file: str | None = None,
    *,
    policy: Policy | None = None,
    stopped: Callable[[], bool] | None = None,
) -> Iterator[Observation]:
    observation = Observation(cache_file or cache_path(), policy)
    try:
        if not observation.watch(path, stopped):
            raise ObservationStoppedError
        yield observation
    finally:
        observation.close()


def _advance(cache_file: str, path: str) -> None:
    if revision := _revisions.get((cache_file, path)):
        revision.version += 1


def publish(
    observation: Observation,
    path: str,
    output: str,
    key: FileKey | None,
    verdict: Verdict | None,
    fingerprint: dict,
    cache: SweepCache | None = None,
) -> bool:
    """Conditionally replace source and output in one visible mutation.

    A rejected output never drops the source. A walk only changes memory here;
    its existing checkpoints persist the full JSON, outside scheduler locks.
    Outside a walk the store is rewritten by :func:`flush` once the coalescing
    window closes, so a burst of publications costs one rewrite rather than one
    each.
    """
    with _writer:
        # Stat and disk reads may block; state readers and mutation owners must
        # still be able to progress. Recheck revisions after all slow reads.
        if verdict is not None and (key is None or cache_key(output, key.lang) != key):
            return False
        store = observation.cache_file
        entries = None if cache is not None else _for_update(fingerprint, store)
        with _update_lock:
            paths = {path, output}
            if (cache is not None and cache.path != store) or not observation.accepts(paths):
                return False
            if fingerprint != observation.policy.fingerprint():
                return False
            if cache is not None:
                if cache.fingerprint != fingerprint or cache._superseded:
                    return False
                for name in paths:
                    entry = _entry(key, verdict) if name == output and key and verdict else None
                    cache._install(name, entry)
                observation.published = True
                return True
            if entries is None:
                return False
            before = dict(entries)
            for live_cache in _live:
                if (
                    live_cache.path == store
                    and live_cache.fingerprint == fingerprint
                    and not live_cache._superseded
                ):
                    entries = live_cache._standing()
            entries.pop(path, None)
            if verdict is None:
                entries.pop(output, None)
            else:
                entries[output] = _entry(cast(FileKey, key), verdict)
            for name in paths:
                _advance(store, name)
                _share(name, entries.get(name), fingerprint, store)
            observation.published = True
        if entries != before:
            _defer(store, fingerprint, entries)
        return True


def _share(
    path: str,
    entry: dict | None,
    fingerprint: dict,
    cache_file: str,
    source: SweepCache | None = None,
) -> None:
    """Carry fresh changes into every concurrent walk before it can save."""
    for cache in _live:
        if (
            cache is not source
            and cache.fingerprint == fingerprint
            and cache.path == cache_file
        ):
            cache._external[path] = entry
            cache._changes += 1


def clear() -> int:
    """Clear persisted/live verdicts and fence every active observation.

    Delete first: a filesystem refusal must leave the accepted state intact.
    Revisions are advanced even for paths with no entry yet.
    """
    with _writer:
        path = cache_path()
        paths = set(read(path, Policy.from_config().fingerprint()).files)
        verdict_store.remove(path)
        with _update_lock:
            _pending.pop(path, None)
            for (cache_file, _), revision in _revisions.items():
                if cache_file == path:
                    revision.version += 1
            for cache in _live:
                if cache.path == path:
                    paths.update(cache._standing())
                    cache._previous.clear()
                    cache._next.clear()
                    cache._external.clear()
                    cache._dropped.clear()
                    cache._changes += 1
                    cache.publish_view()
            return len(paths)


def _for_update(fingerprint: dict, cache_file: str) -> dict[str, dict] | None:
    """The entries as :func:`publish` may change them, or None to leave the
    file alone.

    An unwritten batch is the newest the store has been, so it answers ahead of
    the file. Another build's format, or verdicts under rules since changed, is
    a cache the next sweep drops whole; a current verdict cannot be filed among
    them. Nothing swept yet is an empty set: one verdict is still worth showing.
    """
    with _batched:
        batch = _pending.get(cache_file)
        if batch is not None and batch.fingerprint == fingerprint:
            return batch.entries
    document = verdict_store.load(cache_file)
    if document is None:
        return None
    if not document.present:
        return {}
    if not document.known or document.fingerprint != fingerprint:
        return None
    return document.entries


def _defer(cache_file: str, fingerprint: dict, entries: dict[str, dict]) -> None:
    """Hold a publication for the coalescing window instead of writing it now.

    The caller holds the writer lock, so the batch being replaced is nobody's
    to read part-way through. A batch judged under other rules is dropped
    rather than merged: a load would refuse it anyway.
    """
    now = time.monotonic()
    with _batched:
        standing = _pending.get(cache_file)
        limit = (
            standing.limit
            if standing is not None and standing.fingerprint == fingerprint
            else now + _COALESCE_LIMIT
        )
        _pending[cache_file] = _Batch(
            fingerprint, entries, min(now + _COALESCE_SECONDS, limit), limit
        )
        _wake()


def _wake() -> None:
    """Start or nudge the thread that writes batches as their windows close.
    Caller holds the state mutex."""
    global _flusher
    if _flusher is None:
        _flusher = threading.Thread(target=_flushing, name="verdict-flush", daemon=True)
        _flusher.start()
    _batched.notify_all()


def _flushing() -> None:
    """Write each batch once its window closes, then retire."""
    global _flusher
    while True:
        with _batched:
            if not _pending:
                _flusher = None
                return
            waiting = min(batch.due for batch in _pending.values()) - time.monotonic()
            if waiting > 0:
                _batched.wait(waiting)
                continue
        _write_batches(due_only=True)


def _write_batches(*, due_only: bool) -> None:
    """Rewrite the stores holding unwritten publications.

    The writer lock keeps their entries still, so the state mutex is not held
    across the I/O. A window reopened while this waited for the lock is left
    for the next pass.
    """
    with _writer:
        now = time.monotonic()
        with _batched:
            ready = [
                (cache_file, batch)
                for cache_file, batch in _pending.items()
                if not due_only or batch.due <= now
            ]
        for cache_file, batch in ready:
            _persist(cache_file, batch.fingerprint, batch.entries)


def _persist(cache_file: str, fingerprint: dict, entries: dict[str, dict]) -> None:
    """Rewrite the store, and let the batch go either way.

    A refusal costs the window's verdicts until something looks at those files
    again, which is what a publication that could not write cost before.
    Holding them back would mean retrying a full volume every window.
    """
    try:
        # The store's own directory rather than STATE_DIR, which a deferred
        # write can outlive.
        os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
        verdict_store.write(cache_file, fingerprint, entries)
    except OSError as err:
        log.warning("could not write the sweep cache %s: %s", cache_file, err)
    _discard(cache_file)


def _discard(cache_file: str) -> None:
    """Forget what was waiting for this store; the file holds it now."""
    with _batched:
        _pending.pop(cache_file, None)
        _batched.notify_all()


def flush() -> None:
    """Write every deferred publication, due or not. Never raises.

    The shutdown drain and the CLI's exit call it, so a process never ends
    holding a verdict only in memory.
    """
    _write_batches(due_only=False)


def forget() -> None:
    """Drop unwritten publications without writing them. For tests, which give
    each case its own STATE_DIR."""
    with _batched:
        _pending.clear()
        _batched.notify_all()


#: Numbers the published views, process-wide and only climbing: a reader memoises
#: on the file's stamp plus the generation, and a per-walk counter could repeat.
_generations = itertools.count(1)


class SweepCache:
    """Verdicts from previous sweeps, keyed by path.

    ``carry`` and :func:`publish` build the next sweep's contents, so unvisited
    entries fall away on ``save``; ``checkpoint`` persists mid-sweep without
    pruning. ``fingerprint`` is the policy the verdicts were judged under, and
    a mismatch on load drops the cache.
    """

    def __init__(self, path: str, fingerprint: dict):
        self.path = path
        self.fingerprint = fingerprint
        self._superseded = False
        self._previous: dict[str, dict] = {}
        self._next: dict[str, dict] = {}
        self._external: dict[str, dict | None] = {}
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
        with _batched:
            if (batch := _pending.get(path)) is not None:
                if batch.fingerprint == fingerprint:
                    cache._previous = dict(batch.entries)
                else:
                    log.info("rule configuration changed, dropping the sweep cache")
                return cache
        document = verdict_store.load(path)
        if document is None or not document.present:
            return cache
        if not document.known:
            log.info("sweep cache is an older format, dropping it for a full re-probe")
            return cache
        if document.fingerprint != fingerprint:
            log.info("rule configuration changed, dropping the sweep cache")
            return cache
        cache._previous = document.entries
        return cache

    @_synchronized
    def lookup(self, path: str, key: FileKey | None) -> Verdict | None:
        """The stored verdict for an unchanged file, or None.

        Tracks are not decoded, since ``carry`` moves the entry forward whole.
        ``why``, ``planned`` and ``duration`` are: a hit out of retries must
        say what went wrong, and an estimate needs the other two.
        """
        entry = self._external.get(path, self._previous.get(path))
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

    @_synchronized
    def failures(self, path: str, key: FileKey | None) -> int:
        """Failed rewrites of this file as it now stands. Keyed like a lookup,
        so a change on disk starts the count over."""
        entry = self._external.get(path, self._previous.get(path))
        if key is None or not isinstance(entry, dict):
            return 0
        if not asdict(key).items() <= entry.items():
            return 0
        return _count(entry.get("failures"))

    def _install(self, path: str, entry: dict | None) -> None:
        _advance(self.path, path)
        self._external.pop(path, None)
        _share(path, entry, self.fingerprint, self.path, self)
        if entry is None:
            self._dropped.add(path)
            self._next.pop(path, None)
        else:
            self._next[path] = entry
            self._dropped.discard(path)
        self._dirty = True
        self._changes += 1

    @_synchronized
    def carry(self, path: str) -> None:
        """Bring a hit's stored entry forward untouched. Already on disk, so
        this does not mark the cache dirty."""
        if path not in self._dropped and (
            entry := self._external.get(path, self._next.get(path, self._previous.get(path)))
        ):
            self._next[path] = entry

    def checkpoint(self) -> None:
        """Persist mid-sweep, so an interrupted sweep keeps what it learned.
        Skipped while nothing fresh has been recorded."""
        if self._dirty:
            self.keep()

    def keep(self) -> None:
        """Persist a partial walk without holding state across the write."""
        with _writer:
            with _update_lock:
                entries = self._standing()
                self._dirty = False
            self._write(entries)

    def _standing(self) -> dict[str, dict]:
        """Every entry that still stands: the previous walk's, less this walk's
        drops, plus its verdicts. Shared by :meth:`keep` and
        :meth:`publish_view` so they cannot disagree."""
        stands = {
            path: entry for path, entry in self._previous.items() if path not in self._dropped
        }
        return self._merge_external({**stands, **self._next})

    def _merge_external(self, entries: dict[str, dict]) -> dict[str, dict]:
        entries = dict(entries)
        for path, entry in self._external.items():
            if entry is None:
                entries.pop(path, None)
            else:
                entries[path] = entry
        return entries

    @_synchronized
    def publish_view(self) -> bool:
        """Offer this walk's verdicts so far to readers; whether anything was
        new. False on a warm sweep, where ``carry`` counts as no change.

        The shared lock includes changes arriving from other walks. Readers
        receive an immutable snapshot of all verdicts known at publication.
        """
        if self._changes == self._published:
            return False
        self._published = self._changes
        self._snapshot = (next(_generations), self._standing())
        return True

    def save(self) -> None:
        """Persist a completed walk, dropping entries it never visited."""
        with _writer:
            with _update_lock:
                if self._superseded:
                    return
                entries = self._merge_external(self._next)
                pruned = self._standing().keys() - entries.keys()
                for path in pruned:
                    _advance(self.path, path)
                    _share(path, None, self.fingerprint, self.path, self)
                self._dropped.update(pruned)
                self._changes += len(pruned)
            self._write(entries)

    def _write(self, entries: dict[str, dict]) -> None:
        """Persist the walk's own view. It already carries every publication a
        batch was holding, so the file now says at least as much."""
        if self._superseded:
            return
        try:
            verdict_store.write(self.path, self.fingerprint, entries)
        except OSError as err:
            log.warning("could not write sweep cache %s: %s", self.path, err)
            return
        _discard(self.path)


#: Active walks share changes while retaining their own pruning scope.
_live: list[SweepCache] = []


@contextlib.contextmanager
def live(cache: SweepCache) -> Iterator[None]:
    """Publish concurrent walks without losing updates at their checkpoints."""
    with _writer:
        latest = read(cache.path, cache.fingerprint)
        with _update_lock:
            # Loading and registering are separate steps. Pick up any writes that
            # landed in between, plus verdicts other walks have not checkpointed.
            if latest.current:
                cache._previous = latest.files
            cache._superseded = cache.fingerprint != Policy.from_config().fingerprint()
            for other in _live:
                if other.path == cache.path and not other._superseded:
                    if other.fingerprint == cache.fingerprint:
                        cache._previous = other._standing()
                    elif not cache._superseded:
                        other._superseded = True
            _live.append(cache)
    try:
        yield
    finally:
        with _update_lock:
            _live.remove(cache)
            for other in _live:
                other.publish_view()


def live_view(fingerprint: dict) -> tuple[int, dict[str, dict]] | None:
    """The newest published view under these rules, across active walks."""
    with _update_lock:
        views = [
            cache._snapshot
            for cache in _live
            if cache.fingerprint == fingerprint
            and not cache._superseded
            and cache._snapshot is not None
        ]
        return max(views, key=lambda view: view[0], default=None)
