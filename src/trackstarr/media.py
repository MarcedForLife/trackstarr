"""ffprobe access and the stream predicates the planner reasons about."""

import json
import subprocess

from . import config
from .langs import norm_lang
from .policy import Policy

#: Stream tag recording the settings a generated downmix was encoded with,
#: written at encode time so a later pass can recognise our own tracks. MP4
#: does not preserve custom stream tags, so it only survives in Matroska.
GENERATED_TAG = "TRACKSTARR"


class ProbeError(RuntimeError):
    """ffprobe could not produce usable output for a file.

    The one exception callers need to handle: it covers a corrupt or
    truncated file, a probe timeout, and unparseable probe output.
    """


def probe(path: str) -> dict:
    """Return ffprobe's JSON for a file, raising ProbeError when it can't."""
    try:
        out = subprocess.run(
            # -v error rather than quiet: on a damaged file the stderr text is
            # the only clue about what is actually wrong with it.
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
    """The track title: Matroska reports it as ``title``, MP4 as ``name``.

    ``handler_name`` is deliberately not consulted; it is muxer boilerplate
    ("SoundHandler"), not a title anyone set.
    """
    tags = stream.get("tags") or {}
    return tags.get("title") or tags.get("name") or ""


def stream_lang(stream: dict) -> str | None:
    return norm_lang((stream.get("tags") or {}).get("language"))


def tag_value(stream: dict, name: str) -> str | None:
    """A stream tag by name, case-insensitively, ignoring an ``-eng`` style
    language suffix: mkvmerge writes BPS-eng, and Matroska may change case."""
    wanted = name.lower()
    for key, value in (stream.get("tags") or {}).items():
        if key.lower().partition("-")[0] == wanted:
            return value
    return None


def stream_bitrate(stream: dict) -> int | None:
    """Bits per second, or None when the container doesn't say.

    MP4 reports per-stream bit_rate; Matroska usually doesn't, but mkvmerge
    writes the BPS statistics tags most release files carry.
    """
    for reported in (stream.get("bit_rate"), tag_value(stream, "BPS")):
        try:
            if rate := int(reported or 0):
                return rate
        except TypeError, ValueError:
            continue
    return None


def generated_settings(stream: dict) -> str | None:
    """The recorded encode settings of a trackstarr-generated track, or None
    for tracks we didn't make."""
    return tag_value(stream, GENERATED_TAG)


def has_disposition(stream: dict, *flags: str) -> bool:
    disposition = stream.get("disposition") or {}
    return any(disposition.get(flag) for flag in flags)


def is_commentary(stream: dict, policy: Policy) -> bool:
    """Commentary, audio description and interview tracks.

    The disposition flags are authoritative when a muxer bothered to set
    them; most rips don't, so the track title is the fallback. Getting this
    right is the whole point of the downmix rule: a 2.0 commentary track must
    not count as the stereo track a player can fall back to.
    """
    return has_disposition(stream, "comment", "visual_impaired", "descriptions") or bool(
        policy.commentary_re.search(stream_title(stream))
    )


def is_forced(stream: dict, policy: Policy) -> bool:
    """Forced subtitles display even with subtitles off (foreign dialogue,
    signs), so they are always kept and never make another track redundant."""
    return has_disposition(stream, "forced") or bool(
        policy.forced_re.search(stream_title(stream))
    )


def is_sdh(stream: dict, policy: Policy) -> bool:
    """Subtitles for the deaf and hard-of-hearing: full dialogue plus
    speaker labels and sound cues."""
    return has_disposition(stream, "hearing_impaired") or bool(
        policy.sdh_re.search(stream_title(stream))
    )


def is_cover_art(stream: dict, policy: Policy) -> bool:
    """Embedded artwork, which players otherwise read as a second video track."""
    return has_disposition(stream, "attached_pic") or (
        stream.get("codec_name") in policy.image_codecs
    )


def title_is_load_bearing(stream: dict, policy: Policy) -> bool:
    """Titles the planner's own decisions read, so a rewrite must not clear
    them: the next plan would classify the track differently, breaking
    idempotence."""
    return is_commentary(stream, policy) or is_sdh(stream, policy) or is_forced(stream, policy)


def is_junk_title(title: str, policy: Policy) -> bool:
    """Release junk (bitrates, resolutions, source tags) rather than meaning.

    A pure pattern test; callers clearing stream titles must guard them with
    title_is_load_bearing first.
    """
    return bool(title) and bool(policy.junk_title_re.search(title))
