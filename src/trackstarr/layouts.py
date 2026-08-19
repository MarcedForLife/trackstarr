"""The DOWNMIX_LAYOUTS and AUDIO_BITRATE vocabulary.

Parsing, validation and resolution into the concrete settings an encode
uses. Pure string-and-config work: nothing here knows about plans, streams
or probe output. Startup refuses invalid entries via
:func:`trackstarr.config.errors`.
"""

import re
from dataclasses import dataclass

from . import config


def bitrate_bps(rate: str) -> int | None:
    """``320k``, ``1M`` or ``320000`` as bits per second, None if unparseable.

    The one grammar for bitrate strings: layout entries, AUDIO_BITRATE and
    the weak-track comparison all parse through here, so they can never
    disagree about what a rate means.
    """
    matched = re.fullmatch(r"(\d+)([km]?)", rate.strip().lower())
    if not matched:
        return None
    return int(matched.group(1)) * {"": 1, "k": 1000, "m": 1000000}[matched.group(2)]


@dataclass(frozen=True)
class Layout:
    """One entry of DOWNMIX_LAYOUTS: a channel layout and the bitrate its
    downmixes are encoded at, either stated (``5.1:640k``) or scaled from
    AUDIO_BITRATE. Always present, so nothing downstream re-checks for it."""

    name: str  # "5.1"
    channels: int  # 6
    bitrate: str  # "640k"


def downmix_bitrate(channels: int | None) -> str:
    """AUDIO_BITRATE names the stereo rate; bigger layouts scale per channel.

    An AUDIO_BITRATE ffmpeg accepts but bitrate_bps can't parse (``0.2M``)
    is passed through unscaled rather than refused.
    """
    bps = bitrate_bps(config.AUDIO_BITRATE)
    if bps is None or not channels or channels <= 2:
        return config.AUDIO_BITRATE
    scaled = bps * channels // 2
    return f"{scaled // 1000}k" if scaled % 1000 == 0 else str(scaled)


def parse_layout(entry: str) -> Layout | None:
    """``5.1`` or ``5.1:640k`` as a Layout, None for anything else
    (``surround``, ``5:1``, ``0.0``, ``5.1:640x``)."""
    name, _, bitrate = entry.partition(":")
    matched = re.fullmatch(r"(\d)\.(\d)", name)
    if not matched or (bitrate and bitrate_bps(bitrate) is None):
        return None
    channels = int(matched.group(1)) + int(matched.group(2))
    if not channels:
        return None
    return Layout(name, channels, bitrate or downmix_bitrate(channels))


def resolved_layouts() -> list[Layout]:
    """DOWNMIX_LAYOUTS parsed, invalid entries dropped, smallest first."""
    parsed = [layout for entry in config.DOWNMIX_LAYOUTS if (layout := parse_layout(entry))]
    # Bitrate included so two entries differing only in rate ("5.1" and
    # "5.1:640k") order deterministically; a set has no order of its own.
    return sorted(parsed, key=lambda layout: (layout.channels, layout.name, layout.bitrate))


def encode_settings(codec: str, bitrate: str) -> str:
    """What media.GENERATED_TAG records about an encode: its codec and rate."""
    return f"{codec} {bitrate}"
