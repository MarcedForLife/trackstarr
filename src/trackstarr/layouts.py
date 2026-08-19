"""The DOWNMIX_LAYOUTS vocabulary and the rate each layout is encoded at.

Parsing, validation and resolution into the concrete settings an encode
uses. Pure string-and-config work: nothing here knows about plans, streams
or probe output. Startup refuses invalid entries via
:func:`trackstarr.config.errors`.

Every layout states its own rate, in its own ``AUDIO_BITRATE_`` variable.
Nothing is derived from anything else: a rate that was scaled per channel
from a single setting had to be worked out before it could be judged, and
was silently wrong for a codec whose efficiency did not happen to match.
"""

import re
from dataclasses import dataclass

from . import config


def bitrate_bps(rate: str) -> int | None:
    """``320k``, ``1M`` or ``320000`` as bits per second, None if unparseable.

    The one grammar for bitrate strings: the layout rates and the weak-track
    comparison all parse through here, so they can never disagree about what
    a rate means.
    """
    matched = re.fullmatch(r"(\d+)([km]?)", rate.strip().lower())
    if not matched:
        return None
    return int(matched.group(1)) * {"": 1, "k": 1000, "m": 1000000}[matched.group(2)]


def canonical_bitrate(rate: str) -> str:
    """A rate reduced to its one spelling, so ``320k`` and ``320000`` agree.

    :func:`encode_settings` writes the result into the file as a stream tag
    that a later pass compares for equality, so two spellings of one rate
    would read as a settings change and regenerate every track this tool has
    made. A rate ffmpeg accepts but bitrate_bps cannot parse (``0.2M``) is
    passed through unchanged, as everywhere else.
    """
    bps = bitrate_bps(rate)
    if bps is None:
        return rate.strip()
    return f"{bps // 1000}k" if bps % 1000 == 0 else str(bps)


@dataclass(frozen=True)
class Layout:
    """One entry of DOWNMIX_LAYOUTS: a channel layout and the bitrate its
    downmixes are encoded at, as its ``AUDIO_BITRATE_`` variable states it.
    Always present, so nothing downstream re-checks for it, and always
    canonical, so one rate has one spelling wherever it lands: the ffmpeg
    argument, the plan summary, the fingerprint and the tag."""

    name: str  # "5.1"
    channels: int  # 6
    bitrate: str  # "640k"


def parse_channels(name: str) -> int | None:
    """How many channels a layout name means (``5.1`` is 6), None for
    anything that isn't one (``surround``, ``5:1``, ``0.0``)."""
    matched = re.fullmatch(r"(\d)\.(\d)", name)
    if not matched:
        return None
    # 0.0 names no channels, so it is a typo rather than a layout.
    return int(matched.group(1)) + int(matched.group(2)) or None


def layout_bitrate(name: str) -> str:
    """The rate configured for a layout, empty when nothing states one."""
    return config.AUDIO_BITRATES.get(name, "")


def parse_layout(entry: str) -> Layout | None:
    """A DOWNMIX_LAYOUTS entry as a Layout, None when it is not a layout name
    or nothing gives it a usable rate.

    Both halves collapse to None here because callers want a Layout or
    nothing; :func:`trackstarr.policy.errors` asks the two questions
    separately, so the startup report can say which one failed.
    """
    channels = parse_channels(entry)
    rate = layout_bitrate(entry)
    if channels is None or bitrate_bps(rate) is None:
        return None
    return Layout(entry, channels, canonical_bitrate(rate))


def resolved_layouts() -> list[Layout]:
    """DOWNMIX_LAYOUTS parsed, unusable entries dropped, smallest first.

    Sorted by name within a channel count, so two layouts of the same size
    ("4.2" and "5.1") order deterministically; a set has no order of its own.
    """
    parsed = [layout for entry in config.DOWNMIX_LAYOUTS if (layout := parse_layout(entry))]
    return sorted(parsed, key=lambda layout: (layout.channels, layout.name))


def encode_settings(codec: str, bitrate: str) -> str:
    """What media.GENERATED_TAG records about an encode: its codec and rate.

    Written into the file, so the spelling is settled by
    :func:`canonical_bitrate` rather than left as whatever the setting said.
    """
    return f"{codec} {canonical_bitrate(bitrate)}"
