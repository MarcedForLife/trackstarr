"""ffprobe access and the stream predicates the planner reasons about."""

import json
import re
import subprocess
from dataclasses import dataclass

from . import config
from .langs import norm_lang
from .policy import IMAGE_CODECS, Policy
from .tracks import settings_bitrate

#: Tag recording a generated downmix's encode settings, so a later pass knows
#: our own tracks. Survives in Matroska; MP4 drops custom tags.
GENERATED_TAG = "TRACKSTARR"


class ProbeError(RuntimeError):
    """ffprobe could not produce usable output: a corrupt file, a timeout, or
    output that will not parse."""


def probe(path: str) -> dict:
    """ffprobe's JSON for a file. Raises ProbeError."""
    timeout = config.current().PROBE_TIMEOUT
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
                "-show_chapters",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as err:
        raise ProbeError(f"ffprobe timed out after {timeout}s") from err
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


def summary_bitrate(stream: dict) -> int | None:
    """The rate a library view shows: the reported one, or the rate a track we
    encoded records in its own tag.

    ffmpeg writes neither bit_rate nor BPS on an encode, so a generated downmix
    is the one track in the file with no rate on disk.
    """
    return stream_bitrate(stream) or settings_bitrate(generated_settings(stream) or "")


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


#: Audio codecs that carry a master rather than a mix. PCM has a variant per
#: sample format, so it is matched by prefix, and DTS-HD MA rides in a dts
#: stream, so it is read off the profile.
LOSSLESS_CODECS = frozenset({"flac", "alac", "truehd", "mlp", "tta", "wavpack"})


def is_lossless(stream: dict) -> bool:
    """Whether the track is a lossless master, which is the one loss a re-rip
    cannot undo. Nothing re-encodes one to save space."""
    codec = stream.get("codec_name") or ""
    if codec in LOSSLESS_CODECS or codec.startswith("pcm_"):
        return True
    return codec == "dts" and "MA" in (stream.get("profile") or "")


def is_cover_art(stream: dict) -> bool:
    """Embedded artwork, which players otherwise read as a second video track."""
    return has_disposition(stream, "attached_pic") or (stream.get("codec_name") in IMAGE_CODECS)


def title_is_load_bearing(stream: dict, policy: Policy) -> bool:
    """Titles the planner classifies on. Clearing one would break idempotence."""
    return is_commentary(stream, policy) or is_sdh(stream, policy) or is_forced(stream, policy)


def matches_release_tags(title: str, policy: Policy) -> bool:
    """Whether a title carries release tags. A pure pattern test; callers clearing
    titles must check title_is_load_bearing first."""
    return bool(title) and bool(policy.release_tag_re.search(title))


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
        "bitrate": summary_bitrate(stream),
        "flags": flags,
    }
    if (dv := dolby_vision(stream)) is not None:
        summary["dv"] = {
            name: value
            for name, value in {
                "profile": dv.profile,
                "compatibility": dv.compatibility,
                "unsupported": dv.unsupported,
            }.items()
            if value is not None and value != ""
        }
    return {name: value for name, value in summary.items() if value not in (None, "", [])}


@dataclass(frozen=True)
class DolbyVision:
    profile: int | None = None
    compatibility: int | None = None
    unsupported: str = ""

    @property
    def eligible(self) -> bool:
        return not self.unsupported


def dolby_vision(stream: dict) -> DolbyVision | None:
    """Read eligibility only from a complete, unambiguous DOVI record."""
    try:
        side_data = _side_data(stream)
    except ValueError:
        return DolbyVision(unsupported="Malformed Dolby Vision metadata")
    records = [
        item for item in side_data if item.get("side_data_type") == "DOVI configuration record"
    ]
    if not records:
        if any(
            "DOVI" in item.get("side_data_type", "")
            or "Dolby Vision" in item.get("side_data_type", "")
            for item in side_data
        ):
            return DolbyVision(unsupported="Missing Dolby Vision configuration record")
        return None
    if len(records) != 1:
        return DolbyVision(unsupported="Conflicting Dolby Vision configuration records")
    record = records[0]
    fields = (
        "dv_profile",
        "dv_bl_signal_compatibility_id",
        "bl_present_flag",
        "rpu_present_flag",
        "el_present_flag",
    )
    if any(type(record.get(key)) is not int for key in fields):
        return DolbyVision(unsupported="Incomplete or malformed Dolby Vision configuration")
    profile, compatibility, base, rpu, enhancement = (record[key] for key in fields)
    reason = ""
    if stream.get("codec_type") != "video" or is_cover_art(stream):
        reason = "Dolby Vision removal does not apply to artwork or non-video streams"
    elif stream.get("codec_name") != "hevc":
        reason = "Dolby Vision removal supports HEVC only"
    # 6 is the UHD Blu-ray ID a profile 7 conversion keeps, over the same HDR10 base.
    elif profile != 8 or compatibility not in (1, 6):
        reason = "Dolby Vision removal supports HDR10-compatible profile 8.1 and 8.6 only"
    elif (base, rpu, enhancement) != (1, 1, 0):
        reason = (
            "Dolby Vision removal requires a base layer and RPU without an enhancement layer"
        )
    elif rejected := stream.get(STRIP_REJECTED):
        reason = f"FFmpeg cannot remove Dolby Vision from this stream ({rejected})"
    return DolbyVision(profile, compatibility, reason)


#: Set on a probed stream by :func:`trackstarr.planner.build_plan` when
#: :func:`strip_trial` fails, holding FFmpeg's error.
STRIP_REJECTED = "trackstarr_dv_strip_rejected"


def strip_trial(path: str, stream: int) -> str:
    """FFmpeg's error from stripping the stream's first frames, or "".

    dovi_rpu parses every unit, not just the RPU, so one malformed SEI costs a
    whole keyframe. Trying FFmpeg itself means an FFmpeg that reads the stream lifts this.
    """
    timeout = config.current().PROBE_TIMEOUT
    try:
        out = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                # Otherwise ffmpeg drops the packet and carries on, exiting 0.
                "-xerror",
                "-i",
                path,
                "-map",
                f"0:{stream}",
                "-c",
                "copy",
                "-bsf:v",
                "dovi_rpu=strip=1",
                "-frames:v",
                "24",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ProbeError("Dolby Vision removal trial could not run") from err
    lines = out.stderr.strip().splitlines()
    if out.returncode == 0 and not lines:
        return ""
    if not lines:
        return f"ffmpeg exited {out.returncode}"
    # The "[dovi_rpu @ 0x...] " prefix names an address that changes every run.
    return re.sub(r"^\[[^]]*\] ", "", lines[0]).rstrip(".")


# Only these source/output container paths have acceptance coverage.
DV_CONTAINERS = frozenset({".mkv", ".mp4", ".m4v"})
FRAME_VIDEO_PROPERTIES = (
    "width",
    "height",
    "pix_fmt",
    "color_range",
    "color_space",
    "color_transfer",
    "color_primaries",
)
VIDEO_PROPERTIES = (
    "codec_type",
    "codec_name",
    "profile",
    *FRAME_VIDEO_PROPERTIES,
    "chroma_location",
    "sample_aspect_ratio",
    "field_order",
)
HDR_TYPES = frozenset(
    {
        "Mastering display metadata",
        "Content light level metadata",
        "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)",
    }
)


def _side_data(item: dict) -> list[dict]:
    sides = item.get("side_data_list", [])
    if not isinstance(sides, list) or any(
        not isinstance(side, dict) or not isinstance(side.get("side_data_type", ""), str)
        for side in sides
    ):
        raise ValueError("malformed side data")
    return sides


def hdr_metadata(item: dict) -> list[dict]:
    return [side for side in _side_data(item) if side.get("side_data_type") in HDR_TYPES]


def frame_sample(path: str, stream: int) -> list[dict]:
    """Decode a bounded prefix, paired by frame order across container timestamp shifts."""
    timeout = config.current().PROBE_TIMEOUT
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                str(stream),
                "-read_intervals",
                "%+#24",
                "-show_frames",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ProbeError("Dolby Vision frame verification probe failed") from err
    # Decoding errors can leave a partial sample even when ffprobe exits zero.
    if out.returncode != 0 or out.stderr.strip():
        raise ProbeError(f"Dolby Vision frame verification failed: {out.stderr.strip()[:200]}")
    try:
        frames = json.loads(out.stdout)["frames"]
        if (
            not isinstance(frames, list)
            or not frames
            or any(
                not isinstance(frame, dict) or frame.get("media_type") != "video"
                for frame in frames
            )
        ):
            raise ValueError("no video frames")
        for frame in frames:
            _side_data(frame)
        return frames
    except (ValueError, KeyError, TypeError) as err:
        raise ProbeError("Dolby Vision frame verification returned no usable frames") from err


def is_chapter_stream(stream: dict) -> bool:
    """The QuickTime text track FFmpeg regenerates from mapped chapters."""
    return (
        stream.get("codec_type") == "data"
        and stream.get("codec_name") == "bin_data"
        and stream.get("codec_tag_string") == "text"
    )
