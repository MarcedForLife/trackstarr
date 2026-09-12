"""Apply one track edit and judge the file again.

:mod:`trackstarr.mkvtag` writes the tag; this decides whether an edit aimed
from a page may be made at all, then books what it left: the file is judged
again at once so the page reloads to the new verdict, and the *arrs and the
media servers are told as a rewrite tells them. Nothing keeps a record of the
tags themselves beyond a history line saying who changed what.
"""

import logging
import re

from . import events, library, rewrites, sweep, sweep_cache
from .arr import Arr, innermost
from .langs import norm_lang
from .media_server import refresh_servers
from .mkvtag import FLAGS, UNDEFINED, Edit, Outcome, Result, edit_track, mistook, unwritable
from .policy import Policy
from .processing import Job, process
from .sweep_cache import cache_key, cache_path

log = logging.getLogger(__name__)

#: Most tracks one request may edit: a season's worth, with room. Matches the
#: files a title's sheet lists (library.MAX_FILES), which is where a batch is
#: picked from.
MAX_TARGETS = 200

#: What a language must look like once normalised, before mkvpropedit sees
#: it. mkvpropedit refuses a code that is not ISO 639, with a message worth
#: passing on, so this only turns away what could never be one.
_CODE = re.compile(r"[a-z]{2,3}")


def parse_edit(body: dict) -> Edit:
    """The edit a request body asks for. Raises ValueError with the refusal."""
    lang = body.get("lang")
    if lang is not None:
        if not isinstance(lang, str):
            raise ValueError("lang is a language code")
        # 639-2/B, as every track tag is compared: "ja" and "Japanese" land
        # as jpn, and nothing at all as und.
        code = norm_lang(lang) or UNDEFINED
        if not _CODE.fullmatch(code):
            raise ValueError(f"{lang!r} is not a language code")
        lang = code
    flags = body.get("flags") or {}
    if not isinstance(flags, dict):
        raise ValueError("flags is a map of flag name to on or off")
    known = {flag.name for flag in FLAGS}
    for name, value in flags.items():
        if name not in known:
            raise ValueError(
                f"{name!r} is not a flag; the flags are {', '.join(sorted(known))}"
            )
        if not isinstance(value, bool):
            raise ValueError(f"{name} is on or off")
    if lang is None and not flags:
        raise ValueError("nothing to change")
    return Edit(lang, dict(flags))


def refusal(path: str, stored: dict | None) -> str | None:
    """Why an edit aimed from a page cannot be made, or None.

    ``stored`` is the file's sweep cache entry, whose size and mtime say
    whether the tracks the page showed are still the file's; a file with no
    entry has shown nothing to aim an edit at. The rules need no such check,
    since they aim at a probe of their own.
    """
    if why := unwritable(path, stored):
        return why
    if stored is None:
        return "the file has no verdict yet; sweep or re-check the title first"
    return None


def apply(
    path: str,
    index: int,
    edit: Edit,
    by: str,
    stored: dict | None,
    title: library.Title | None = None,
) -> Result:
    """Make one edit to one track and judge the file again.

    ``stored`` is the file's sweep cache entry and ``title`` the library title
    it belongs to, whose original language the verdict turns on. The file is
    read back after the edit rather than trusted: the history records what
    changed, and the new verdict is booked so the sheet reloads to it rather
    than waiting on the next sweep.
    """
    if why := refusal(path, stored):
        return Result(path, Outcome.REFUSED, why)
    edited = edit_track(path, index, edit)
    if isinstance(edited, Result):
        return edited
    before, wanted, kind, after = edited
    changed = {
        name: {"from": before[name], "to": after.get(name)}
        for name in before
        if after.get(name) != before[name]
    }
    if changed:
        events.record("retagged", path=path, index=index, kind=kind, changed=changed, by=by)
        log.info("%s stream %d retagged by %s: %s", path, index, by, changed)
    # The verdict stands either way, so it is reached before the edit is held
    # against what was asked for.
    verdict = _rejudge(path, title)
    return mistook(path, wanted, after, verdict) or Result(path, Outcome.RETAGGED, "", verdict)


def _rejudge(path: str, title: library.Title | None) -> str:
    """Book the file's verdict as it now stands, and say what it is.

    Report only, through :func:`trackstarr.processing.process` so verdicts are
    reached in one place. The media servers are told too, as a rewrite tells
    them, so their own track lists follow the file's. A walk starting after
    the request's check can book over this entry at its next checkpoint; the
    edit moved the file's size and mtime, so that entry fails its own key and
    the file is probed again.
    """
    lang = title.lang if title else None
    # Keyed before the probe, as a sweep keys its verdicts, so a change landing
    # under the probe leaves a stale entry rather than a wrong one.
    key = cache_key(path, lang)
    result = process(Job(path, lang), dry_run=True)
    sweep.remember(path, key, result)
    # Our own edit moved the file, so the record follows it rather than losing
    # its claim to a change we made.
    rewrites.rekey(path, key)
    refresh_servers(path)
    return str(result.status)


def apply_all(targets: list[tuple[str, int]], edit: Edit, by: str) -> list[Result]:
    """One edit across every ``(path, stream index)`` named, in order: a
    season's untagged original track in one press. Each file is its own
    answer; one refusal stops nothing else. The *arr is asked to rescan once
    per title touched, since its media info is per title."""
    stored = sweep_cache.read(cache_path(), Policy.from_config().fingerprint())
    folders = {title.folder: title for title in library.known().titles}
    results: list[Result] = []
    # The *arr and item behind each title a file of which changed.
    rescans: dict[str, tuple[Arr, int]] = {}
    for path, index in targets:
        title = innermost(folders, path)
        result = apply(path, index, edit, by, stored.files.get(path), title)
        results.append(result)
        if result.verdict and title is not None and title.arr is not None and title.item_id:
            rescans.setdefault(title.id, (title.arr, title.item_id))
    for arr, item_id in rescans.values():
        arr.rescan(item_id)
    return results
