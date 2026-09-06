"""The DOWNMIX_LAYOUTS vocabulary and how each layout is encoded.

Pure string-and-config work; :func:`trackstarr.policy.errors` refuses bad
entries. Every layout states its own codec and rate in ``AUDIO_CODEC_`` and
``AUDIO_BITRATE_`` variables: a rate scaled from one setting was wrong for any
codec of different efficiency, and stereo (for phones, so AAC) and 5.1 (for
receivers, so AC-3) are for different players.

:data:`CODECS` is what is known about the encoders worth naming, not an
allow-list: an unlisted name is checked against ``ffmpeg -encoders``.
"""

import re
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Codec:
    """What is known about one audio encoder, so the settings page can warn
    before a save rather than a rewrite at a time afterwards.

    ffmpeg fails on ``-ac 8`` for AC-3, and writes Opus into MP4 that players
    decline. ``lossless`` means the rate is taken and ignored.
    """

    name: str  # the ffmpeg encoder name, as -c:a takes it
    max_channels: int
    containers: frozenset[str]
    lossless: bool = False


#: The encoders worth offering by name, in the settings page's order: most
#: widely decoded first.
#:
#: aac      every phone, browser and television decodes it
#: ac3      every receiver takes it over HDMI; 5.1 is its ceiling
#: eac3     better per bit, declined by some older receivers, same ceiling
#: libopus  best per bit, worst for direct play, Matroska only
#: flac     lossless, so large; the rate does nothing
#:
#: libfdk_aac is a better AAC ffmpeg cannot ship under its own licence. Alpine's
#: build lacks it, which ``ffmpeg -encoders`` catches.
CODECS = {
    codec.name: codec
    for codec in (
        Codec("aac", 8, frozenset({".mkv", ".mp4", ".m4v"})),
        Codec("ac3", 6, frozenset({".mkv", ".mp4", ".m4v"})),
        Codec("eac3", 6, frozenset({".mkv", ".mp4", ".m4v"})),
        Codec("libopus", 8, frozenset({".mkv"})),
        Codec("libfdk_aac", 8, frozenset({".mkv", ".mp4", ".m4v"})),
        Codec("flac", 8, frozenset({".mkv"}), lossless=True),
    )
}


def bitrate_bps(rate: str) -> int | None:
    """``320k``, ``1M`` or ``320000`` as bits per second, or None. The one
    grammar for rates, so layout rates and the low-bitrate comparison agree."""
    matched = re.fullmatch(r"(\d+)([km]?)", rate.strip().lower())
    if not matched:
        return None
    return int(matched.group(1)) * {"": 1, "k": 1000, "m": 1000000}[matched.group(2)]


def canonical_bitrate(rate: str) -> str:
    """A rate in one spelling, so ``320k`` and ``320000`` agree.

    :func:`encode_settings` writes this into a stream tag a later pass compares
    for equality, so two spellings would regenerate every track. An
    unparseable rate passes through.
    """
    bps = bitrate_bps(rate)
    if bps is None:
        return rate.strip()
    return f"{bps // 1000}k" if bps % 1000 == 0 else str(bps)


@dataclass(frozen=True)
class Layout:
    """One DOWNMIX_LAYOUTS entry with its encoder and rate. All three are
    always present, and the rate is canonical."""

    name: str  # "5.1"
    channels: int  # 6
    codec: str  # "ac3"
    bitrate: str  # "640k"


def parse_channels(name: str) -> int | None:
    """How many channels a layout name means (``5.1`` is 6), or None for
    anything that is not one."""
    matched = re.fullmatch(r"(\d)\.(\d)", name)
    if not matched:
        return None
    # 0.0 names no channels, so it is a typo, not a layout.
    return int(matched.group(1)) + int(matched.group(2)) or None


def layout_bitrate(name: str) -> str:
    """The rate configured for a layout, empty when nothing states one."""
    return config.AUDIO_BITRATES.get(name, "")


def layout_codec(name: str) -> str:
    """The encoder configured for a layout, empty when nothing states one."""
    return config.AUDIO_CODECS.get(name, "")


def parse_layout(entry: str) -> Layout | None:
    """A DOWNMIX_LAYOUTS entry as a Layout, or None when the name, codec or
    rate is unusable. :func:`trackstarr.policy.errors` checks them separately
    to say which."""
    channels = parse_channels(entry)
    codec = layout_codec(entry)
    rate = layout_bitrate(entry)
    if channels is None or not codec or bitrate_bps(rate) is None:
        return None
    return Layout(entry, channels, codec, canonical_bitrate(rate))


def resolved_layouts() -> list[Layout]:
    """DOWNMIX_LAYOUTS parsed, unusable entries dropped, in written order,
    which is the audio track order."""
    return [layout for entry in config.DOWNMIX_LAYOUTS if (layout := parse_layout(entry))]


def encode_settings(codec: str, bitrate: str) -> str:
    """What media.GENERATED_TAG records: codec and canonical rate."""
    return f"{codec} {canonical_bitrate(bitrate)}"
