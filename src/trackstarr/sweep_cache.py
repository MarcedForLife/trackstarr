"""Remember each file's verdict between sweeps.

On a settled library the nightly sweep re-probes thousands of unchanged
files to reach the same verdicts, at 50-200ms of ffprobe each. Size and
mtime are enough to skip that. The whole cache is dropped when
:meth:`trackstarr.policy.Policy.fingerprint` changes, and deleting the file
forces a full re-probe.

Verdicts carry the probed track summaries too, so the cache doubles as the
library index a media view can read; a dropped cache just means the next
sweep rebuilds it.

Which verdicts are safe to cache is the sweep's call; see
:data:`trackstarr.sweep.CACHEABLE_STATUSES`.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field

from .state import write_json
from .status import Status

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileKey:
    """Everything file-side a verdict depends on.

    Taken before the probe, so a change landing mid-sweep leaves a stale
    entry rather than filing the new file under the old verdict. The
    hard-link count is in there because a SKIP_HARDLINKS verdict changes when
    the download client lets go, which moves neither size nor mtime.
    """

    size: int
    mtime_ns: int
    nlink: int
    lang: str | None


@dataclass(frozen=True)
class Verdict:
    """What a sweep concluded about a file, minus anything transient.

    ``tracks`` is what the probe saw, as :func:`trackstarr.media.track_summary`
    dicts; empty when the file was never probed (a pre-probe skip).
    """

    status: Status
    reasons: str = ""
    tracks: list[dict] = field(default_factory=list)


def cache_key(path: str, lang: str | None) -> FileKey | None:
    """The file's current FileKey, or None if it is unreadable. None is
    never cached."""
    try:
        stat_result = os.stat(path)
    except OSError:
        return None
    return FileKey(stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_nlink, lang)


class SweepCache:
    """Verdicts from previous sweeps, keyed by path.

    ``lookup`` hits carried forward by ``carry`` and fresh verdicts booked by
    ``record`` build the next sweep's contents, so entries for files a sweep
    never visits fall away on ``save``. ``checkpoint`` persists mid-sweep
    without that pruning.

    ``fingerprint`` is the policy the verdicts were judged under; a mismatch on
    load drops the cache.
    """

    def __init__(self, path: str, fingerprint: dict):
        self.path = path
        self.fingerprint = fingerprint
        self._previous: dict[str, dict] = {}
        self._next: dict[str, dict] = {}
        #: Whether anything fresh landed since the last write; see checkpoint.
        self._dirty = False

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
        if data.get("config") != fingerprint:
            log.info("rule configuration changed, dropping the sweep cache")
            return cache
        entries = data.get("files")
        if isinstance(entries, dict):
            cache._previous = entries
        return cache

    def lookup(self, path: str, key: FileKey | None) -> Verdict | None:
        """The stored verdict for an unchanged file, or None.

        Stored track summaries are not decoded here; nothing reads a hit's
        tracks, so ``carry`` moves the whole entry forward instead.
        """
        entry = self._previous.get(path)
        if key is None or not isinstance(entry, dict):
            return None
        if not asdict(key).items() <= entry.items():
            return None
        try:
            # "" makes a missing status as invalid as a damaged one.
            return Verdict(Status(entry.get("status", "")), entry.get("reasons") or "")
        except ValueError:
            # A hand-edited or damaged entry; treat it as a miss.
            return None

    def record(self, path: str, key: FileKey | None, verdict: Verdict) -> None:
        if key is None:
            return
        entry = {
            **asdict(key),
            "status": str(verdict.status),
            "reasons": verdict.reasons,
        }
        if verdict.tracks:
            entry["tracks"] = verdict.tracks
        self._next[path] = entry
        self._dirty = True

    def carry(self, path: str) -> None:
        """Bring a hit's stored entry forward untouched, tracks and all.

        The entry is already on disk exactly as it stands, so this neither
        re-encodes it nor marks the cache dirty.
        """
        if entry := self._previous.get(path):
            self._next[path] = entry

    def checkpoint(self) -> None:
        """Persist mid-sweep, so an interrupted sweep keeps what it learned.

        Skipped while nothing fresh has been recorded: carried entries are
        already on disk, and now that entries hold track summaries a warm
        sweep would otherwise rewrite the whole cache unchanged, every
        interval.
        """
        if not self._dirty:
            return
        self._write({**self._previous, **self._next})
        self._dirty = False

    def save(self) -> None:
        self._write(self._next)

    def _write(self, entries: dict[str, dict]) -> None:
        try:
            write_json(self.path, {"config": self.fingerprint, "files": entries})
        except OSError as err:
            log.warning("could not write sweep cache %s: %s", self.path, err)
