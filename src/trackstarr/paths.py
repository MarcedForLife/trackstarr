"""Library path identity, shared by the modules that match files to a folder."""

import posixpath
from collections.abc import Callable


def canonical(path: str) -> str:
    """One lexical spelling of a media path, so two names for a file are one key.

    Never resolves symlinks, since identity must not depend on the disk
    answering. POSIX's special leading `//` collapses along with the rest.
    """
    settled = posixpath.normpath(path)
    return "/" + settled.lstrip("/") if settled.startswith("//") else settled


def within(folder: str) -> Callable[[str], bool]:
    """A test for the folder itself and the files under it, by whole component.

    Built once per read: a queue page runs it over every captured row.
    """
    prefix = folder.rstrip("/") + "/"
    return lambda path: path == folder or path.startswith(prefix)
