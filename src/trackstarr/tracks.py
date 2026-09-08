"""The two ordered lists that say which tracks a rewrite makes, keeps and drops:
AUDIO_LAYOUTS by size, LANGUAGES by language.

Both share the ``name:action`` grammar and its actions, languages taking a
subset, and a track is made where a row of each says downmix. One list per axis
rather than one per action, so a size or language cannot be guaranteed and
deleted at once. Written order matters in both: the audio track order for
layouts, the downmix source preference for languages.

Entry shapes, told apart by field count::

    2.0             downmix, at the stock encoder and rate for that size
    7.1:remove      an action, for the two that encode nothing
    5.1:eac3:448k   downmix, at that encoder and rate
    eng             downmix, for a language
    fre:keep        an action, for a language

Rates are per layout rather than scaled from one setting: stereo (phones, so
AAC) and 5.1 (receivers, so AC-3) are for different players.

Pure string work. :func:`trackstarr.policy.errors` refuses bad entries.
"""

import re
from dataclasses import dataclass
from typing import NamedTuple

from . import config
from .langs import norm_lang


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


#: The encoders worth offering by name, most widely decoded first. Not an
#: allow-list: an unlisted name is checked against ``ffmpeg -encoders``.
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
    """One AUDIO_LAYOUTS entry: a size and what happens to it.

    ``codec`` and ``bitrate`` are set only on a ``downmix``, so a stale encoder
    beside a removed layout cannot change a fingerprint. On one both are present
    and the rate is canonical.
    """

    name: str  # "5.1"
    channels: int  # 6
    action: str  # one of ACTIONS
    codec: str = ""  # "ac3", downmixes only
    bitrate: str = ""  # "640k", downmixes only

    @property
    def downmixes(self) -> bool:
        return self.action == DOWNMIX

    @property
    def removes(self) -> bool:
        return self.action == REMOVE


#: What an AUDIO_LAYOUTS entry may say. "downmix" is the default and the only
#: one that generates anything; every track made is one, taken from the best
#: bigger track, so a size with nothing above it is simply not made.
#:
#: downmix  guarantee a track, from the best bigger one
#: keep     leave it alone, but keep its place in the order
#: remove   delete every track matching it
ACTIONS = ("downmix", "keep", "remove")
DOWNMIX, KEEP, REMOVE = ACTIONS

#: What a LANGUAGES entry may say, a subset: RULE_LANGUAGES drops everything
#: the list does not name, so no row has to.
LANG_ACTIONS = (DOWNMIX, KEEP)


def parse_channels(name: str) -> int | None:
    """How many channels a layout name means (``5.1`` is 6), or None for
    anything that is not one."""
    matched = re.fullmatch(r"(\d)\.(\d)", name)
    if not matched:
        return None
    # 0.0 names no channels, so it is a typo, not a layout.
    return int(matched.group(1)) + int(matched.group(2)) or None


#: What a bare entry is made at. Any other bare name states its own, which
#: errors() asks for. 6.1 and 7.1 are past ac3's ceiling, so aac.
STOCK = {
    "1.0": ("aac", "160k"),
    "2.0": ("aac", "320k"),
    "5.1": ("ac3", "640k"),
    "6.1": ("aac", "704k"),
    "7.1": ("aac", "768k"),
}


#: The rates the settings page offers per size, low to high. Not a limit: the
#: page shows whatever an entry already holds beside them, and any rate saves.
RATES = {
    "1.0": ("96k", "128k", "160k", "192k"),
    "2.0": ("128k", "192k", "256k", "320k", "384k"),
    "5.1": ("384k", "448k", "512k", "640k"),
    "6.1": ("512k", "640k", "704k", "768k"),
    "7.1": ("512k", "640k", "768k", "896k"),
}


class Spec(NamedTuple):
    """One entry's fields, split out but not checked. ``codec`` and ``bitrate``
    are empty for an action, and for a bare name :data:`STOCK` has no entry
    for."""

    name: str
    action: str
    codec: str
    bitrate: str


def split_entry(entry: str) -> Spec | None:
    """An AUDIO_LAYOUTS entry's fields, or None for a shape with no meaning.

    Told apart by field count, so no name has to be reserved. Nothing is
    validated here, :func:`trackstarr.policy.errors` does that.
    """
    parts = [part.strip() for part in entry.split(":")]
    if len(parts) == 1:
        codec, bitrate = STOCK.get(parts[0], ("", ""))
        return Spec(parts[0], DOWNMIX, codec, bitrate)
    if len(parts) == 2:
        return Spec(parts[0], parts[1], "", "")
    if len(parts) == 3:
        return Spec(parts[0], DOWNMIX, parts[1], parts[2])
    return None


def parse_layout(entry: str) -> Layout | None:
    """An AUDIO_LAYOUTS entry as a Layout, or None where any field is unusable.
    :func:`trackstarr.policy.errors` checks them separately to say which."""
    spec = split_entry(entry)
    if spec is None:
        return None
    channels = parse_channels(spec.name)
    if channels is None or spec.action not in ACTIONS:
        return None
    if spec.action != DOWNMIX:
        return Layout(spec.name, channels, spec.action)
    if not spec.codec or bitrate_bps(spec.bitrate) is None:
        return None
    return Layout(spec.name, channels, spec.action, spec.codec, canonical_bitrate(spec.bitrate))


def resolved_layouts() -> list[Layout]:
    """AUDIO_LAYOUTS parsed, unusable entries dropped, in written order, which
    is the audio track order. Every action, since the order is the whole
    list's."""
    return [layout for entry in config.AUDIO_LAYOUTS if (layout := parse_layout(entry))]


def downmixed_layouts() -> list[Layout]:
    """The layouts guaranteed to exist, in order: what a downmix is made for."""
    return [layout for layout in resolved_layouts() if layout.downmixes]


def removed_layouts() -> list[Layout]:
    """The layouts deleted wherever a file has them."""
    return [layout for layout in resolved_layouts() if layout.removes]


def encode_settings(codec: str, bitrate: str) -> str:
    """What media.GENERATED_TAG records: codec and canonical rate."""
    return f"{codec} {canonical_bitrate(bitrate)}"


#: The LANGUAGES name for whatever the *arrs report a title was made in, which
#: is not an ISO code and is already a non-language to arr.original_of. Resolved
#: per title by Policy.resolve.
ORIGINAL = "original"


@dataclass(frozen=True)
class Lang:
    """One LANGUAGES entry: a language kept, and whether layouts are made in it.
    A subtitle ignores the action, having no layout."""

    name: str  # ISO 639-2/B, or ORIGINAL
    action: str  # one of LANG_ACTIONS

    @property
    def downmixes(self) -> bool:
        return self.action == DOWNMIX


def parse_lang(entry: str) -> Lang | None:
    """A LANGUAGES entry as a Lang, or None where either field is unusable.
    :func:`trackstarr.policy.errors` checks them separately to say which."""
    parts = [part.strip() for part in entry.split(":")]
    if len(parts) > 2:
        return None
    name = lang_name(parts[0])
    action = parts[1] if len(parts) == 2 else DOWNMIX
    if not name or action not in LANG_ACTIONS:
        return None
    return Lang(name, action)


def lang_name(entry: str) -> str:
    """An entry's language as a code, or "" for one that is not a language.
    Normalised as track tags are, so "en", "eng" and "English" all land as
    eng."""
    name = entry.strip().lower()
    return name if name == ORIGINAL else (norm_lang(name) or "")


def resolved_langs() -> list[Lang]:
    """LANGUAGES parsed, unusable entries dropped, in written order, which is
    the downmix source preference."""
    return [lang for entry in config.LANGUAGES if (lang := parse_lang(entry))]
