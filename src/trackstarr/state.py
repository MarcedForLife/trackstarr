"""The atomic JSON write the sweep cache and the parked set share.

A leaf module like :mod:`trackstarr.status`, so either side can use it
without importing the other.
"""

import json
import os


def write_json(path: str, payload) -> None:
    """Replace ``path`` with ``payload`` as JSON, atomically.

    A reader mid-swap sees the old file or the new, never a torn write.
    Raises OSError; what a lost write costs is the caller's story to tell.
    """
    partial = f"{path}.tmp"
    with open(partial, "w") as out_file:
        json.dump(payload, out_file)
    os.replace(partial, path)
