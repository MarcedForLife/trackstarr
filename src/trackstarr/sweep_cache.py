"""Remember each file's verdict between sweeps.

On a settled library the nightly sweep re-probes thousands of unchanged files
to re-derive the same verdicts, at 50-200ms of ffprobe each. A size and mtime
signature is enough to skip that: a rewrite, an *arr upgrade or a manual
replacement all change both. Entries carry the original language and report
reasons they were judged with, and the whole cache is dropped when
:func:`trackstarr.planner.rules_fingerprint` changes. Deleting the cache file
forces a full re-probe.

Only verdicts that leave the file untouched are cached: a rewrite changes the
file (its next probe is a fresh judgement), and failures may be transient.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from .planner import rules_fingerprint

log = logging.getLogger(__name__)

#: Fields record() appends to a cache entry after the key: status, reasons.
_VERDICT_FIELDS = 2


@dataclass(frozen=True)
class Verdict:
    """What a sweep concluded about a file, minus anything transient."""

    status: str
    reasons: str = ""


def cache_key(path: str, lang: str | None) -> list | None:
    """``[size, mtime_ns, nlink, lang]``: everything file-side a verdict
    depends on.

    Taken before the file is probed, so a change landing mid-sweep leaves the
    cached entry stale rather than caching the new file under the old
    verdict. The hard-link count is included because SKIP_HARDLINKS verdicts
    change when a seeding download client lets go of a file, which alters
    neither size nor mtime. None when the file is unreadable; None is never
    cached.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns, st.st_nlink, lang]


class SweepCache:
    """Verdicts from previous sweeps, keyed by path.

    ``lookup`` hits carried forward by ``record`` build the next sweep's
    contents, so entries for files a sweep never visits (deleted or moved)
    fall away on ``save``; ``checkpoint`` persists mid-sweep without that
    pruning.
    """

    def __init__(self, path: str):
        self.path = path
        self._previous: dict[str, list] = {}
        self._next: dict[str, list] = {}

    @classmethod
    def load(cls, path: str) -> SweepCache:
        cache = cls(path)
        try:
            with open(path) as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            return cache
        except (OSError, json.JSONDecodeError) as err:
            log.warning("ignoring unreadable sweep cache %s: %s", path, err)
            return cache
        if data.get("config") != rules_fingerprint():
            log.info("rule configuration changed, dropping the sweep cache")
            return cache
        entries = data.get("files")
        if isinstance(entries, dict):
            cache._previous = entries
        return cache

    def lookup(self, path: str, key: list | None) -> Verdict | None:
        entry = self._previous.get(path)
        if key is None or not isinstance(entry, list):
            return None
        if len(entry) != len(key) + _VERDICT_FIELDS or entry[: len(key)] != key:
            return None
        return Verdict(entry[-2], entry[-1])

    def record(self, path: str, key: list | None, verdict: Verdict) -> None:
        if key is None:
            return
        self._next[path] = [*key, verdict.status, verdict.reasons]

    def checkpoint(self) -> None:
        """Persist mid-sweep, so an interrupted sweep keeps what it learned."""
        self._write({**self._previous, **self._next})

    def save(self) -> None:
        self._write(self._next)

    def _write(self, entries: dict[str, list]) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w") as cache_file:
                json.dump({"config": rules_fingerprint(), "files": entries}, cache_file)
            os.replace(tmp, self.path)
        except OSError as err:
            log.warning("could not write sweep cache %s: %s", self.path, err)
