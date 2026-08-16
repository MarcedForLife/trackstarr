"""Path containment, shared by the *arr title index and the Plex section match."""

from __future__ import annotations


def path_within(path: str, base: str) -> bool:
    """True when path is base itself or inside it, on directory boundaries.

    The trailing-slash join is what keeps /data/media from matching
    /data/media2; callers must hand in bases with no trailing slash.
    """
    return path == base or path.startswith(base + "/")
