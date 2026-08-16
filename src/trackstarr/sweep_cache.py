"""Remember each file's verdict between sweeps.

On a settled library the nightly sweep re-probes thousands of unchanged files
to re-derive the same verdicts, at 50-200ms of ffprobe each. A size and mtime
signature is enough to skip that: a rewrite, an *arr upgrade or a manual
replacement all change both. Entries carry the original language and report
reasons they were judged with, and the whole cache is dropped when
:meth:`trackstarr.policy.Policy.fingerprint` changes. Deleting the cache file
forces a full re-probe.

Only verdicts that leave the file untouched are cached: a rewrite changes the
file (its next probe is a fresh judgement), and failures may be transient.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass

from .status import Status

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileKey:
    """Everything file-side a verdict depends on.

    Taken before the file is probed, so a change landing mid-sweep leaves the
    cached entry stale rather than caching the new file under the old
    verdict. The hard-link count is included because SKIP_HARDLINKS verdicts
    change when a seeding download client lets go of a file, which alters
    neither size nor mtime.
    """

    size: int
    mtime_ns: int
    nlink: int
    lang: str | None


@dataclass(frozen=True)
class Verdict:
    """What a sweep concluded about a file, minus anything transient."""

    status: Status
    reasons: str = ""


def cache_key(path: str, lang: str | None) -> FileKey | None:
    """The file's current FileKey, or None when it is unreadable; None is
    never cached."""
    try:
        stat_result = os.stat(path)
    except OSError:
        return None
    return FileKey(stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_nlink, lang)


class SweepCache:
    """Verdicts from previous sweeps, keyed by path.

    ``lookup`` hits carried forward by ``record`` build the next sweep's
    contents, so entries for files a sweep never visits (deleted or moved)
    fall away on ``save``; ``checkpoint`` persists mid-sweep without that
    pruning. Each persisted entry is a flat dict of the FileKey fields plus
    ``status`` and ``reasons``.

    ``fingerprint`` is the policy the verdicts were judged under
    (:meth:`trackstarr.policy.Policy.fingerprint`); a mismatch on load
    drops the cache.
    """

    def __init__(self, path: str, fingerprint: dict):
        self.path = path
        self.fingerprint = fingerprint
        self._previous: dict[str, dict] = {}
        self._next: dict[str, dict] = {}

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
        entry = self._previous.get(path)
        if key is None or not isinstance(entry, dict):
            return None
        if not asdict(key).items() <= entry.items():
            return None
        try:
            # "" for a missing status, which Status rejects exactly as a
            # damaged one, so both land in the ValueError below.
            return Verdict(Status(entry.get("status", "")), entry.get("reasons") or "")
        except ValueError:
            # A hand-edited or damaged entry; treat it as a miss.
            return None

    def record(self, path: str, key: FileKey | None, verdict: Verdict) -> None:
        if key is None:
            return
        self._next[path] = {
            **asdict(key),
            "status": str(verdict.status),
            "reasons": verdict.reasons,
        }

    def checkpoint(self) -> None:
        """Persist mid-sweep, so an interrupted sweep keeps what it learned."""
        self._write({**self._previous, **self._next})

    def save(self) -> None:
        self._write(self._next)

    def _write(self, entries: dict[str, dict]) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w") as cache_file:
                json.dump({"config": self.fingerprint, "files": entries}, cache_file)
            os.replace(tmp, self.path)
        except OSError as err:
            log.warning("could not write sweep cache %s: %s", self.path, err)
