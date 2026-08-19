"""The DOWNMIX_LAYOUTS vocabulary and the rate each layout is encoded at.

Pure string-and-config work; nothing here knows about plans, streams or
probe output. Startup refuses bad entries through
:func:`trackstarr.policy.errors`.

Every layout states its own rate in its own ``AUDIO_BITRATE_`` variable,
derived from nothing. A rate scaled per channel from one setting had to be
worked out before it could be judged, and was quietly wrong for any codec
whose efficiency didn't match.
"""

import re
from dataclasses import dataclass

from . import config


def bitrate_bps(rate: str) -> int | None:
    """``320k``, ``1M`` or ``320000`` as bits per second, None if it won't
    parse.

    The one grammar for bitrate strings. Layout rates and the weak-track
    comparison both go through here, so they cannot disagree about what a
    rate means.
    """
    matched = re.fullmatch(r"(\d+)([km]?)", rate.strip().lower())
    if not matched:
        return None
    return int(matched.group(1)) * {"": 1, "k": 1000, "m": 1000000}[matched.group(2)]


def canonical_bitrate(rate: str) -> str:
    """A rate reduced to one spelling, so ``320k`` and ``320000`` agree.

    :func:`encode_settings` writes this into the file as a stream tag a later
    pass compares for equality, so two spellings of one rate would read as a
    settings change and regenerate every track we have made. A rate ffmpeg
    takes but bitrate_bps cannot parse passes through unchanged.
    """
    bps = bitrate_bps(rate)
    if bps is None:
        return rate.strip()
    return f"{bps // 1000}k" if bps % 1000 == 0 else str(bps)


@dataclass(frozen=True)
class Layout:
    """One DOWNMIX_LAYOUTS entry: a channel layout and the bitrate its
    downmixes are encoded at. Always present, so nothing downstream rechecks
    it, and always canonical, so one rate has one spelling wherever it lands,
    the ffmpeg argument, the plan summary, the fingerprint, the tag."""

    name: str  # "5.1"
    channels: int  # 6
    bitrate: str  # "640k"


def parse_channels(name: str) -> int | None:
    """How many channels a layout name means, so ``5.1`` is 6. None for
    anything that isn't one: ``surround``, ``5:1``, ``0.0``."""
    matched = re.fullmatch(r"(\d)\.(\d)", name)
    if not matched:
        return None
    # 0.0 names no channels, so it is a typo, not a layout.
    return int(matched.group(1)) + int(matched.group(2)) or None


def layout_bitrate(name: str) -> str:
    """The rate configured for a layout, empty when nothing states one."""
    return config.AUDIO_BITRATES.get(name, "")


def parse_layout(entry: str) -> Layout | None:
    """A DOWNMIX_LAYOUTS entry as a Layout, or None if it isn't a layout
    name or nothing gives it a usable rate.

    Both halves collapse to None because callers want a Layout or nothing.
    :func:`trackstarr.policy.errors` asks the two separately, so the startup
    report can say which failed.
    """
    channels = parse_channels(entry)
    rate = layout_bitrate(entry)
    if channels is None or bitrate_bps(rate) is None:
        return None
    return Layout(entry, channels, canonical_bitrate(rate))


def resolved_layouts() -> list[Layout]:
    """DOWNMIX_LAYOUTS parsed, unusable entries dropped, smallest first.

    By name within a channel count, so two layouts of the same size order
    the same every time. A set has no order of its own.
    """
    parsed = [layout for entry in config.DOWNMIX_LAYOUTS if (layout := parse_layout(entry))]
    return sorted(parsed, key=lambda layout: (layout.channels, layout.name))


def encode_settings(codec: str, bitrate: str) -> str:
    """What media.GENERATED_TAG records about an encode: codec and rate.

    This goes into the file, so :func:`canonical_bitrate` settles the
    spelling rather than leaving whatever the setting said.
    """
    return f"{codec} {canonical_bitrate(bitrate)}"
