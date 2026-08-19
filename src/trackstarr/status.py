"""The verdict vocabulary process() reports and the sweep persists.

A leaf module with no intra-package imports, so :mod:`trackstarr.processing`
and :mod:`trackstarr.sweep_cache` can share it without importing each other.
"""

import enum


class Status(enum.StrEnum):
    """Everything process() can report. The sweep's counts are keyed by
    these, and the values are what the cache and pending.tsv store."""

    SKIP = "skip"
    CONFORM = "conform"
    WOULD_FIX = "would-fix"
    FIXED = "fixed"
    DEFERRED = "deferred"
    FAILED = "failed"

    #: So logs and count dicts read "would-fix", not <Status.WOULD_FIX: ...>.
    __repr__ = str.__repr__
