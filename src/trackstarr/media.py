"""ffprobe access and the stream predicates the planner reasons about."""

import json
import subprocess

from . import config
from .langs import norm_lang
from .policy import IMAGE_CODECS, Policy

#: Tag recording a generated downmix's encode settings, so a later pass knows
#: our own tracks. Survives in Matroska; MP4 drops custom tags.
GENERATED_TAG = "TRACKSTARR"


class ProbeError(RuntimeError):
    """ffprobe could not produce usable output: a corrupt file, a timeout, or
    output that will not parse."""


def probe(path: str) -> dict:
    """ffprobe's JSON for a file. Raises ProbeError."""
    try:
        out = subprocess.run(
            # -v error, not quiet: on a damaged file stderr is the only clue.
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=config.PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as err:
        raise ProbeError(f"ffprobe timed out after {config.PROBE_TIMEOUT}s") from err
    if out.returncode != 0:
        raise ProbeError(f"ffprobe failed: {out.stderr.strip()[:200]}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as err:
        raise ProbeError("ffprobe produced unparseable output") from err


def duration(info: dict) -> float:
    try:
        return float((info.get("format") or {}).get("duration") or 0.0)
    except TypeError, ValueError:
        return 0.0


def container_title(info: dict) -> str:
    return ((info.get("format") or {}).get("tags") or {}).get("title") or ""


def stream_title(stream: dict) -> str:
    """The track title: ``title`` in Matroska, ``name`` in MP4. ``handler_name``
    is muxer boilerplate and ignored."""
    tags = stream.get("tags") or {}
    return tags.get("title") or tags.get("name") or ""


def stream_lang(stream: dict) -> str | None:
    return norm_lang((stream.get("tags") or {}).get("language"))


def tag_value(stream: dict, name: str) -> str | None:
    """A stream tag by name, case-insensitively and ignoring a language suffix
    such as BPS-eng."""
    wanted = name.lower()
    for key, value in (stream.get("tags") or {}).items():
        if key.lower().partition("-")[0] == wanted:
            return value
    return None


def stream_bitrate(stream: dict) -> int | None:
    """Bits per second, or None. MP4 reports bit_rate; Matroska usually only
    has mkvmerge's BPS tag."""
    for reported in (stream.get("bit_rate"), tag_value(stream, "BPS")):
        try:
            if rate := int(reported or 0):
                return rate
        except TypeError, ValueError:
            continue
    return None


def unpreserved_bitrate(stream: dict) -> int | None:
    """This stream's rate, only when a copy would lose it.

    A BPS tag carries forward on its own, so None for a stream that has one.
    The reported rate otherwise, which is the mp4-to-mkv case where the header
    field has nowhere to go.
    """
    if tag_value(stream, "BPS") is not None:
        return None
    return stream_bitrate(stream)


def generated_settings(stream: dict) -> str | None:
    """The recorded encode settings of a generated track, or None."""
    return tag_value(stream, GENERATED_TAG)


def has_disposition(stream: dict, *flags: str) -> bool:
    disposition = stream.get("disposition") or {}
    return any(disposition.get(flag) for flag in flags)


def is_commentary(stream: dict, policy: Policy) -> bool:
    """Commentary, audio description and interview tracks, by disposition or
    by title. A 2.0 commentary must not count as the stereo track."""
    return has_disposition(stream, "comment", "visual_impaired", "descriptions") or bool(
        policy.commentary_re.search(stream_title(stream))
    )


def is_forced(stream: dict, policy: Policy) -> bool:
    """Forced subtitles show even with subtitles off, so they are always kept
    and never make another track redundant."""
    return has_disposition(stream, "forced") or bool(
        policy.forced_re.search(stream_title(stream))
    )


def is_sdh(stream: dict, policy: Policy) -> bool:
    """Subtitles for the deaf and hard-of-hearing: full dialogue plus
    speaker labels and sound cues."""
    return has_disposition(stream, "hearing_impaired") or bool(
        policy.sdh_re.search(stream_title(stream))
    )


def is_cover_art(stream: dict) -> bool:
    """Embedded artwork, which players otherwise read as a second video track."""
    return has_disposition(stream, "attached_pic") or (stream.get("codec_name") in IMAGE_CODECS)


def title_is_load_bearing(stream: dict, policy: Policy) -> bool:
    """Titles the planner classifies on. Clearing one would break idempotence."""
    return is_commentary(stream, policy) or is_sdh(stream, policy) or is_forced(stream, policy)


def is_junk_title(title: str, policy: Policy) -> bool:
    """Whether a title is release junk. A pure pattern test; callers clearing
    titles must check title_is_load_bearing first."""
    return bool(title) and bool(policy.junk_title_re.search(title))


def track_summary(stream: dict, policy: Policy) -> dict:
    """One stream distilled for a library view.

    The flags are this module's classifications, so a view reads "commentary"
    as the rules do. Empty fields are dropped.
    """
    kind = stream.get("codec_type")
    flags = [
        name
        for name, present in (
            ("default", has_disposition(stream, "default")),
            ("commentary", kind == "audio" and is_commentary(stream, policy)),
            ("forced", kind == "subtitle" and is_forced(stream, policy)),
            ("sdh", kind == "subtitle" and is_sdh(stream, policy)),
            ("cover_art", kind == "video" and is_cover_art(stream)),
            # Only audio is ever generated; the tag scan walks every key.
            ("generated", kind == "audio" and generated_settings(stream) is not None),
        )
        if present
    ]
    summary = {
        "index": stream.get("index"),
        "kind": kind,
        "codec": stream.get("codec_name"),
        "channels": stream.get("channels"),
        "lang": stream_lang(stream),
        "title": stream_title(stream),
        "bitrate": stream_bitrate(stream),
        "flags": flags,
    }
    return {name: value for name, value in summary.items() if value not in (None, "", [])}
