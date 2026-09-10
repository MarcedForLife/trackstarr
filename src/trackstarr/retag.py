"""Change one Matroska track's language or flags in place, then judge the file
again.

The rules read a track's tags off the file, so a track tagged ``und`` is never
the source for the original-language downmix, and a 2.0 with no commentary
mark counts as the stereo track. Both are a tag away from right, and the tag
lives in the file: mkvpropedit rewrites the header and leaves the streams, so
the change takes a second and every later probe reads it back. The file is
judged again at once, so the page reloads to the new verdict, and the *arr and
the media servers are told as a rewrite tells them. Nothing keeps a record of
the tags themselves beyond a history line saying who changed what.

Matroska only. ffmpeg cannot edit in place, MP4 keeps no commentary flag, and
the remux rule already converts the rest.
"""

import enum
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import NamedTuple

from . import config, events, library, sweep, sweep_cache
from .arr import Arr, innermost
from .executor import is_rewriting
from .langs import norm_lang
from .media import (
    ProbeError,
    has_disposition,
    is_commentary,
    is_forced,
    is_sdh,
    probe,
    stream_lang,
    stream_title,
)
from .media_server import refresh_servers
from .policy import Policy
from .processing import Job, process
from .sweep_cache import cache_key, cache_path

log = logging.getLogger(__name__)

#: Most tracks one request may edit: a season's worth, with room. Matches the
#: files a title's sheet lists (library.MAX_FILES), which is where a batch is
#: picked from.
MAX_TARGETS = 200

#: The tag written for "no language", which ffprobe reads back as none.
UNDEFINED = "und"


class Flag(NamedTuple):
    """One flag an edit may set: what the API calls it, which kind carries it,
    how mkvpropedit names the property, how ffprobe reports it, and how the
    rules read it, title included."""

    name: str
    kind: str
    property_name: str
    disposition: str
    classify: Callable[[dict, Policy], bool]


#: The flags on offer. Commentary is what keeps a 2.0 from counting as the
#: stereo track; forced and SDH (subtitles for the deaf and hard-of-hearing)
#: are what the subtitle rules read. Matroska allows every flag on every
#: track, but the planner reads each on one kind, so each is offered there.
FLAGS = (
    Flag("commentary", "audio", "flag-commentary", "comment", is_commentary),
    Flag("forced", "subtitle", "flag-forced", "forced", is_forced),
    Flag("sdh", "subtitle", "flag-hearing-impaired", "hearing_impaired", is_sdh),
)

#: The kinds an edit may touch, and mkvmerge's word for each in its listing.
MKVMERGE_KINDS = {"audio": "audio", "subtitle": "subtitles"}

#: What a language must look like once normalised, before mkvpropedit sees
#: it. mkvpropedit refuses a code that is not ISO 639, with a message worth
#: passing on, so this only turns away what could never be one.
_CODE = re.compile(r"[a-z]{2,3}")

#: How much of a tool's output a failure keeps. The tail, where the error is.
_OUTPUT_TAIL = 300

#: One edit at a time. Two mkvpropedit runs interleaved on one header can
#: leave the file unreadable, and two requests can name one file.
_edit_lock = threading.Lock()


class Outcome(enum.StrEnum):
    """What became of one target. FAILED is this module's own, not
    :class:`trackstarr.status.Status`'s: the file's verdict is separate."""

    RETAGGED = "retagged"
    UNCHANGED = "unchanged"
    REFUSED = "refused"
    FAILED = "failed"


class RetagError(RuntimeError):
    """The edit could not be made, or could not be trusted. The message is for
    the page."""


@dataclass(frozen=True)
class Edit:
    """What to set on every target. ``lang`` None leaves the language alone;
    the flags named are set and the rest left."""

    lang: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    """What one target came to. ``verdict`` is the file's status once judged
    again, for the file that was edited."""

    path: str
    status: Outcome
    detail: str = ""
    verdict: str = ""

    def as_json(self) -> dict:
        return {name: value for name, value in asdict(self).items() if value}


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


def available() -> bool:
    """Whether mkvtoolnix is on the path. Asked each time, since it is two
    lookups and an image that gains the tools should not need a restart."""
    return all(shutil.which(tool) for tool in ("mkvmerge", "mkvpropedit"))


def refusal(path: str, stored: dict | None) -> str | None:
    """Why the file cannot be edited in place, or None.

    ``stored`` is its sweep cache entry, whose size and mtime say whether the
    tracks the page showed are still the file's; a file with no entry has
    shown nothing to aim an edit at. A hardlink is refused whatever
    SKIP_HARDLINKS says: a rewrite publishes a new inode and leaves the
    download client's copy whole, where an edit in place changes both.
    """
    if not available():
        return "mkvtoolnix is not installed, so track tags cannot be edited in place"
    if os.path.splitext(path)[1].lower() != ".mkv":
        return "only Matroska (.mkv) files are edited in place; the remux rule converts others"
    if stored is None:
        return "the file has no verdict yet; sweep or re-check the title first"
    try:
        found = os.stat(path)
    except OSError as err:
        return f"could not read the file: {err.strerror or err}"
    if found.st_nlink > 1:
        return (
            "hardlinked: an edit in place would change the download client's copy too; "
            "a rewrite makes a new file instead"
        )
    if is_rewriting(path):
        return "a rewrite of this file is under way; wait for it to finish"
    if (stored.get("size"), stored.get("mtime_ns")) != (found.st_size, found.st_mtime_ns):
        return "the file has changed since it was last checked; re-check the title first"
    return None


def _state(stream: dict) -> dict:
    """The editable tags as the file carries them: the language and each of
    the kind's flags, off the disposition rather than the title."""
    told: dict = {"lang": stream_lang(stream) or UNDEFINED}
    for flag in FLAGS:
        if flag.kind == stream.get("codec_type"):
            told[flag.name] = has_disposition(stream, flag.disposition)
    return told


def _describe(state: dict) -> str:
    """One track's tags as a reader sees them, for a message on the page."""
    return ", ".join(
        f"language {value}" if name == "lang" else f"{name} {'on' if value else 'off'}"
        for name, value in state.items()
    )


def _run_tool(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """One mkvtoolnix call. Raises RetagError on a timeout or an exit code of
    2, which is how both tools report an error; 1 is a warning with the work
    done."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as err:
        raise RetagError(f"{args[0]} timed out after {timeout}s") from err
    if out.returncode >= 2:
        # mkvtoolnix writes its errors to stdout.
        said = (out.stdout.strip() or out.stderr.strip())[-_OUTPUT_TAIL:]
        raise RetagError(f"{args[0]} failed ({out.returncode}): {said}")
    return out


def _mkvmerge_tracks(path: str) -> list[dict]:
    """Every track as mkvmerge lists it, in file order. Raises RetagError."""
    out = _run_tool(["mkvmerge", "-J", path], config.current().PROBE_TIMEOUT)
    try:
        listed = json.loads(out.stdout)
    except json.JSONDecodeError as err:
        raise RetagError("mkvmerge produced unparseable output") from err
    tracks = listed.get("tracks") if isinstance(listed, dict) else None
    return [track for track in tracks if isinstance(track, dict)] if tracks else []


def _track_uid(path: str, streams: list[dict], stream: dict) -> int:
    """The Matroska UID of the track ffprobe calls ``stream``.

    ffprobe numbers every stream and mkvpropedit every track, and the two
    disagree once a file carries attachments, so the track is found by its
    place among its own kind and then held against what mkvmerge says of it.
    The two must list the same number of that kind: ffprobe drops a track it
    cannot read, which would shift the place onto the wrong one. An edit is
    only ever aimed by UID.
    """
    kind = stream["codec_type"]
    alike = [found.get("index") for found in streams if found.get("codec_type") == kind]
    listed = [
        track for track in _mkvmerge_tracks(path) if track.get("type") == MKVMERGE_KINDS[kind]
    ]
    if len(alike) != len(listed):
        raise RetagError(
            "the track could not be found: ffprobe and mkvmerge do not list the same "
            f"{kind} tracks"
        )
    props = listed[alike.index(stream["index"])].get("properties") or {}
    if kind == "audio" and props.get("audio_channels") not in (None, stream.get("channels")):
        raise RetagError(
            "the track could not be found: its channels differ between ffprobe and mkvmerge"
        )
    # Either element, since ffprobe reads whichever of the two the file has;
    # neither is a track with no language.
    spoken = {
        norm_lang(props[key]) for key in ("language", "language_ietf") if props.get(key)
    } or {None}
    if stream_lang(stream) not in spoken:
        raise RetagError(
            "the track could not be found: its language differs between ffprobe and mkvmerge"
        )
    uid = props.get("uid")
    if not isinstance(uid, int):
        raise RetagError("the track could not be found: mkvmerge reports no UID for it")
    return uid


def _propedit(path: str, uid: int, before: dict, wanted: dict) -> None:
    """Set what differs between the two states on the one track. Raises
    RetagError.

    The rewrite timeout, not the probe's: a header with no void space to grow
    into is rewritten from the front of the file, and a timeout kills, which
    mid-write is the one way this module could damage a file.
    """
    args = ["mkvpropedit", path, "--edit", f"track:={uid}"]
    if wanted["lang"] != before["lang"]:
        args += ["--set", f"language={wanted['lang']}"]
    for flag in FLAGS:
        if flag.name in wanted and wanted[flag.name] != before[flag.name]:
            args += ["--set", f"{flag.property_name}={int(wanted[flag.name])}"]
    log.info("editing tags: %s", shlex.join(args))
    _run_tool(args, config.current().FFMPEG_TIMEOUT)


def _stream(path: str, index: int) -> tuple[list[dict], dict | None]:
    """The file's streams and the one at ``index``. Raises ProbeError."""
    streams = probe(path).get("streams") or []
    return streams, next((found for found in streams if found.get("index") == index), None)


def _misread(stream: dict, edit: Edit, before: dict) -> str | None:
    """Why turning a flag off would change nothing, or None.

    The page seeds its switches from the rules' reading, which takes a title
    such as "Director's Commentary" as the flag. Clearing that flag clears a
    bit the file never had; the title is what has to go, and that is the
    release tags rule's business.
    """
    policy = Policy.from_config()
    for flag in FLAGS:
        clearing = edit.flags.get(flag.name) is False and not before.get(flag.name)
        if clearing and flag.classify(stream, policy):
            return (
                f"{flag.name} is already off; the rules read it off the title "
                f"{stream_title(stream)!r} instead"
            )
    return None


def _edit_track(path: str, index: int, edit: Edit) -> tuple[dict, dict, str, dict] | Result:
    """The edit itself, under the lock: the state before, the state asked for,
    the kind and the state read back, or the Result that stopped it."""
    try:
        streams, stream = _stream(path, index)
    except ProbeError as err:
        return Result(path, Outcome.FAILED, str(err))
    if stream is None or stream.get("codec_type") not in MKVMERGE_KINDS:
        return Result(
            path, Outcome.REFUSED, f"stream {index} is not an audio or subtitle track"
        )
    kind = stream["codec_type"]
    for flag in FLAGS:
        if flag.name in edit.flags and flag.kind != kind:
            return Result(
                path,
                Outcome.REFUSED,
                f"{flag.name} is a flag on {flag.kind} tracks, not {kind}",
            )
    before = _state(stream)
    if why := _misread(stream, edit, before):
        return Result(path, Outcome.REFUSED, why)
    wanted = before | ({"lang": edit.lang} if edit.lang is not None else {}) | edit.flags
    if wanted == before:
        return Result(path, Outcome.UNCHANGED, "already tagged that way")
    try:
        _propedit(path, _track_uid(path, streams, stream), before, wanted)
        _, read_back = _stream(path, index)
        if read_back is None:
            # mkvpropedit does not renumber streams, so the file moved under
            # the edit rather than the edit losing the track.
            raise RetagError("the track is gone from the file after the edit")
    except (RetagError, ProbeError) as err:
        return Result(path, Outcome.FAILED, str(err))
    return before, wanted, kind, _state(read_back)


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
    with _edit_lock:
        edited = _edit_track(path, index, edit)
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
    verdict = _rejudge(path, title)
    if after != wanted:
        # mkvpropedit said yes and the file says otherwise, which is worth a
        # failure rather than a quiet success. The verdict stands either way.
        return Result(
            path,
            Outcome.FAILED,
            f"the edit did not take: the track reads {_describe(after)}",
            verdict,
        )
    return Result(path, Outcome.RETAGGED, "", verdict)


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
