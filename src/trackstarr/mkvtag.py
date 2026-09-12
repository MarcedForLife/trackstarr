"""One Matroska track's language and flags, edited in place.

mkvpropedit rewrites the header and leaves the streams, so a tag costs a second
where ffmpeg would copy the whole file to change it. Every later probe reads the
new tag back.

Matroska only. ffmpeg cannot edit in place, MP4 keeps no commentary flag, and
the remux rule already converts the rest. What to do with an edited file, judge
it again, tell the *arrs, is :mod:`trackstarr.retag`'s.
"""

import enum
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import NamedTuple

from . import config
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
from .policy import Policy

log = logging.getLogger(__name__)

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


def available() -> bool:
    """Whether mkvtoolnix is on the path. Asked each time, since it is two
    lookups and an image that gains the tools should not need a restart."""
    return all(shutil.which(tool) for tool in ("mkvmerge", "mkvpropedit"))


def unwritable(path: str, seen: dict | None = None) -> str | None:
    """Why this file's header cannot be edited in place, or None.

    A hardlink is refused whatever SKIP_HARDLINKS says. A rewrite publishes a
    new inode and leaves the download client's copy whole, where an edit in
    place changes both. ``seen`` is the size and mtime_ns a caller last read,
    which an edit aimed at what it showed must still match.
    """
    if not available():
        return "mkvtoolnix is not installed, so track tags cannot be edited in place"
    if os.path.splitext(path)[1].lower() != ".mkv":
        return "only Matroska (.mkv) files are edited in place; the remux rule converts others"
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
    if seen and (seen.get("size"), seen.get("mtime_ns")) != (found.st_size, found.st_mtime_ns):
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


def edit_track(path: str, index: int, edit: Edit) -> tuple[dict, dict, str, dict] | Result:
    """The edit itself, one at a time: the state before, the state asked for,
    the kind and the state read back, or the Result that stopped it."""
    with _edit_lock:
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


def mistook(path: str, wanted: dict, after: dict, verdict: str = "") -> Result | None:
    """The failure for an edit mkvpropedit accepted and the file does not show,
    or None. Worth a failure rather than a quiet success."""
    if after == wanted:
        return None
    return Result(
        path,
        Outcome.FAILED,
        f"the edit did not take: the track reads {_describe(after)}",
        verdict,
    )


def write_lang(path: str, index: int, lang: str) -> Result:
    """Set one track's language in place, for the rules rather than a page.

    No history and no re-judging of its own, the caller holds the plan the tag
    came from and books the verdict it reaches. A refusal is its cue to write
    the tag the slow way, in the rewrite.
    """
    if why := unwritable(path):
        return Result(path, Outcome.REFUSED, why)
    edited = edit_track(path, index, Edit(lang=lang))
    if isinstance(edited, Result):
        return edited
    _, wanted, _, after = edited
    return mistook(path, wanted, after) or Result(path, Outcome.RETAGGED)
