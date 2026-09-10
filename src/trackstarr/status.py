"""The verdict vocabulary process() reports and the sweep persists. A leaf
module."""

import enum


class Status(enum.StrEnum):
    """Everything process() can report. The values are what the cache and
    pending.tsv store."""

    SKIP = "skip"
    #: A video file in a container the rules will never rewrite. Its own
    #: verdict because the reader can act on it, by changing ALLOWED_EXTS.
    UNSUPPORTED = "unsupported"
    CONFORM = "conform"
    #: A file the rules would rewrite, with nothing written yet.
    PENDING = "pending"
    MODIFIED = "modified"
    DEFERRED = "deferred"
    FAILED = "failed"

    #: So logs and count dicts read "pending", not <Status.PENDING: ...>.
    __repr__ = str.__repr__
