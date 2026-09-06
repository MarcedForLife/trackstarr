"""The rule policy: every setting the rules read, as one object.

Built from :mod:`trackstarr.config` once per plan, so a plan carries the
settings it was judged under. :meth:`Policy.fingerprint` derives from the
fields, so a new setting invalidates cached verdicts the day it lands.

The rule vocabulary lives here too, with :func:`errors` to check configured
values against it.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, fields

from . import __version__, config
from .layouts import (
    CODECS,
    Layout,
    bitrate_bps,
    layout_bitrate,
    layout_codec,
    parse_channels,
    resolved_layouts,
)

#: What a rule may be set to, weakest first. "alongside" means: make this
#: change when something else is already rewriting the file, never on its own,
#: since a 60GB remux costs more than a junk title is worth.
MODES = ("never", "alongside", "always")
NEVER, ALONGSIDE, ALWAYS = MODES


@dataclass(frozen=True)
class Rule:
    """One rule's default mode and the description the CLI and settings page
    show. Rules are named by their key everywhere: RULE_<NAME> sets the mode
    and the history records which fired."""

    default: str
    summary: str


#: Every rule, in reading order: what is kept, what is generated, what the
#: output looks like. Ride-alongs are changes worth less than a rewrite; the
#: rules that are off drop or convert something an owner has to ask for.
RULES = {
    "languages": Rule(
        ALWAYS,
        "Keep audio and subtitle tracks that are untagged, in ALWAYS_KEEP_LANGS "
        "(English by default) or in the title's original language while "
        "KEEP_ORIGINAL_LANG is on. Drop the rest.",
    ),
    "commentary": Rule(
        NEVER,
        "Drop commentary, described-audio and isolated-score tracks. They are "
        "never downmix sources either way.",
    ),
    "sdh": Rule(
        ALONGSIDE,
        "Drop an SDH (deaf and hard-of-hearing) subtitle when the same language "
        "keeps a full one. Forced subtitles always stay.",
    ),
    "downmix": Rule(
        ALWAYS,
        "Guarantee a non-commentary track for each layout in DOWNMIX_LAYOUTS "
        "(2.0 and 5.1 by default), in the title's original language "
        "(DOWNMIX_ORIGINAL_LANG) and in each language in DOWNMIX_LANGS, downmixed "
        "from the best surviving bigger track of that language. A layout no named "
        "language can fill gets one track from the best source in any language. "
        "Nothing is upmixed.",
    ),
    "regenerate": Rule(
        NEVER,
        "Rebuild trackstarr's own downmixes when their codec or bitrate no longer "
        "matches the settings (REGENERATE_SCOPE=generated). With "
        "REGENERATE_SCOPE=all, also replace any layout-sized track reported under "
        "REGENERATE_BELOW_PERCENT of its layout's rate where a surviving bigger "
        "track has more to give. Every replacement is a fresh downmix from the "
        "best surviving bigger track, never a re-encode in place.",
    ),
    "cover_art": Rule(ALWAYS, "Drop embedded cover art."),
    "junk_titles": Rule(ALONGSIDE, "Clear release junk from track and container titles."),
    "stray_streams": Rule(ALONGSIDE, "Drop data and timecode streams nothing plays."),
    "order": Rule(
        ALWAYS,
        "Order streams: video, audio in DOWNMIX_LAYOUTS order then other sizes by "
        "channel count, subtitles, attachments.",
    ),
    "remux": Rule(
        NEVER,
        "Rewrite MP4 and M4V into Matroska, the container every rule works in. "
        "Text subtitles convert to SRT; only the usual downmixes are encoded.",
    ),
}

#: Every name a plan may put in Plan.rules or Plan.incidental_rules.
RULE_NAMES = frozenset(RULES)

#: How far the regenerate rule reaches. errors() refuses other values.
REGENERATE_SCOPES = ("generated", "all")


def resolved_modes() -> dict[str, str]:
    """Every rule and its mode, defaults filled in. An unknown rule or mode is
    left out and reaches :func:`errors`."""
    stated = config.RULE_MODES
    return {
        name: mode if (mode := stated.get(name)) in MODES else rule.default
        for name, rule in RULES.items()
    }


#: The ffmpeg muxer per container, since the staging name has no real
#: extension. errors() checks every ALLOWED_EXTS entry appears here.
MUXERS = {".mkv": "matroska", ".mp4": "mp4", ".m4v": "mp4"}

#: Containers that keep custom stream tags. Regeneration finds its own tracks
#: by tag, so on MP4 it would re-encode them every sweep; it drops nothing
#: there.
TAG_PRESERVING_EXTS = frozenset({".mkv"})

#: Every container recognised as a film or episode, whether or not the rules
#: rewrite it. The difference from ALLOWED_EXTS is the files the sweep walks
#: past, named: an AVI-only title reads "Unsupported" rather than unchecked.
VIDEO_EXTS = frozenset(
    {
        ".3gp",
        ".asf",
        ".avi",
        ".divx",
        ".dv",
        ".f4v",
        ".flv",
        ".iso",
        ".m2ts",
        ".m2v",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".mxf",
        ".ogm",
        ".ogv",
        ".rm",
        ".rmvb",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
        ".wtv",
    }
)

#: Video codecs that mean embedded artwork rather than a real video stream.
IMAGE_CODECS = frozenset({"mjpeg", "png", "gif", "bmp", "webp", "tiff"})


@dataclass(frozen=True)
class Policy:
    """Everything the rules read, resolved from config at plan time.

    A plan snapshots one, so its command matches the settings it was judged
    with and the sweep cache can fingerprint what its verdicts depended on.
    """

    always_keep: frozenset[str]
    keep_original_lang: bool
    allowed_exts: frozenset[str]
    #: Every rule and its mode, sorted by name. A tuple of pairs rather than a
    #: dict so the field stays hashable and fingerprints deterministically.
    rule_modes: tuple[tuple[str, str], ...]
    #: How far the regenerate rule reaches; see REGENERATE_SCOPES.
    regenerate_scope: str
    #: Percent of its layout's rate under which "all" calls a track low-bitrate.
    regenerate_below: int
    #: The layouts the downmix rule guarantees, in DOWNMIX_LAYOUTS order, which
    #: is also the audio order. Each carries its own encoder and rate.
    downmix_layouts: tuple[Layout, ...]
    #: Which languages each layout is guaranteed in. Both empty means one track
    #: per layout in whatever language the best source speaks.
    downmix_original_lang: bool
    downmix_langs: frozenset[str]
    skip_hardlinks: bool
    commentary_re: re.Pattern[str]
    sdh_re: re.Pattern[str]
    forced_re: re.Pattern[str]
    junk_title_re: re.Pattern[str]

    @classmethod
    def from_config(cls) -> Policy:
        return cls(
            always_keep=frozenset(config.ALWAYS_KEEP_LANGS),
            keep_original_lang=config.KEEP_ORIGINAL_LANG,
            allowed_exts=frozenset(config.ALLOWED_EXTS),
            rule_modes=tuple(sorted(resolved_modes().items())),
            regenerate_scope=config.REGENERATE_SCOPE,
            regenerate_below=config.REGENERATE_BELOW_PERCENT,
            downmix_layouts=tuple(resolved_layouts()),
            downmix_original_lang=config.DOWNMIX_ORIGINAL_LANG,
            downmix_langs=frozenset(config.DOWNMIX_LANGS),
            skip_hardlinks=config.SKIP_HARDLINKS,
            commentary_re=config.COMMENTARY_RE,
            sdh_re=config.SDH_RE,
            forced_re=config.FORCED_RE,
            junk_title_re=config.JUNK_TITLE_RE,
        )

    def rule_mode(self, rule: str) -> str:
        """What this rule was set to when the plan was made."""
        return dict(self.rule_modes)[rule]

    def rules_in(self, *modes: str) -> frozenset[str]:
        """The rules set to any of these modes."""
        return frozenset(name for name, mode in self.rule_modes if mode in modes)

    def needs_original_lang(self) -> bool:
        """Whether a verdict can turn on the title's original language.

        Only the languages rule drops a track over it, so with that rule off
        or KEEP_ORIGINAL_LANG unset an *arr outage costs nothing irreversible.
        The downmix rule only prefers the original among tracks kept anyway.
        """
        return self.rule_mode("languages") != NEVER and self.keep_original_lang

    def keep_langs(self, original_lang: str | None) -> set[str]:
        """The languages the languages rule keeps for a title."""
        keep = set(self.always_keep)
        if original_lang and self.keep_original_lang:
            keep.add(original_lang)
        return keep

    def allowed_container(self, path: str) -> bool:
        """Whether the file is of a type the rules are willing to rewrite."""
        return os.path.splitext(path)[1].lower() in self.allowed_exts

    def is_video(self, path: str) -> bool:
        """Whether the file is a film or episode in any container. What the
        sweep walks; :meth:`allowed_container` is what the rules act on."""
        return os.path.splitext(path)[1].lower() in VIDEO_EXTS

    def fingerprint(self) -> dict:
        """Everything a cached verdict depends on besides the file, as JSON:
        every field plus the package version, so code changes invalidate too."""
        return {"version": __version__} | {
            field.name: _fingerprint_value(getattr(self, field.name)) for field in fields(self)
        }

    def digest(self) -> str:
        """A short, stable id for this exact policy.

        Every event carries one, so a months-old rewrite traces to its
        settings; ``serve`` and every sweep record the full fingerprint it
        resolves against. Twelve hex characters separate settings generations,
        not adversaries.
        """
        canonical = json.dumps(self.fingerprint(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _fingerprint_value(value):
    """A field value as something json.dump accepts, deterministically."""
    if isinstance(value, re.Pattern):
        return value.pattern
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, tuple):
        return [_fingerprint_value(item) for item in value]
    if isinstance(value, Layout):
        # Codec included, or a codec change would keep serving old verdicts.
        return f"{value.name}:{value.codec}:{value.bitrate}"
    return value


def output_exts() -> frozenset[str]:
    """The containers a rewrite may end up writing into.

    With the remux rule on at all, everything is converted on the way out, so
    an encoder only Matroska holds is fine. A container with no muxer is left
    out: it is already its own error.
    """
    if resolved_modes()["remux"] != NEVER:
        return frozenset(TAG_PRESERVING_EXTS)
    return frozenset(config.ALLOWED_EXTS & MUXERS.keys())


def _codec_problems(entry: str, channels: int, codec: str, variable: str) -> list[str]:
    """What a layout's encoder cannot do, as ready-to-log messages.

    Only for encoders :data:`trackstarr.layouts.CODECS` knows; an unlisted one
    is checked against ``ffmpeg -encoders`` instead. Otherwise these surface
    one rewrite at a time: ffmpeg fails on ``-ac 8`` for AC-3, and writes an
    Opus track into MP4 that players decline.
    """
    known = CODECS.get(codec)
    if known is None:
        return []
    problems = []
    if channels > known.max_channels:
        problems.append(
            f"{variable}={codec} encodes at most {known.max_channels} channels, but "
            f"DOWNMIX_LAYOUTS asks it for {entry}; every rewrite of one would fail"
        )
    if unwritable := sorted(output_exts() - known.containers):
        problems.append(
            f"{variable}={codec} cannot be written into {', '.join(unwritable)}, which "
            "ALLOWED_EXTS names; set RULE_REMUX, drop the container, or pick another encoder"
        )
    return problems


def errors() -> list[str]:
    """Startup-fatal problems with the rule vocabulary, which would otherwise
    fail silently. :mod:`trackstarr.cli` reports these beside config.errors()."""
    problems: list[str] = []
    if unmuxable := config.ALLOWED_EXTS - MUXERS.keys():
        problems.append(
            f"ALLOWED_EXTS contains containers with no known muxer: "
            f"{', '.join(sorted(unmuxable))} (valid: {', '.join(sorted(MUXERS))})"
        )
    for rule, mode in sorted(config.RULE_MODES.items()):
        variable = config.rule_variable(rule)
        if rule not in RULES:
            problems.append(f"{variable} names no rule (valid: {', '.join(RULES)})")
        elif mode not in MODES:
            problems.append(f"{variable}={mode!r} is not one of: {', '.join(MODES)}")
    layouts = {entry: parse_channels(entry) for entry in config.DOWNMIX_LAYOUTS}
    if invalid := {entry for entry, channels in layouts.items() if channels is None}:
        problems.append(
            f"DOWNMIX_LAYOUTS contains unrecognised layouts: {', '.join(sorted(invalid))} "
            "(use forms like 2.0, 5.1)"
        )
    # Name, encoder and rate are checked separately so the report says which.
    for entry in sorted(entry for entry, channels in layouts.items() if channels):
        variable = config.bitrate_variable(entry)
        rate = layout_bitrate(entry)
        if not rate:
            problems.append(f"DOWNMIX_LAYOUTS contains {entry} with no rate; set {variable}")
        elif bitrate_bps(rate) is None:
            problems.append(f"{variable}={rate!r} is not a bitrate (use forms like 640k)")
        codec_var = config.codec_variable(entry)
        if codec := layout_codec(entry):
            problems += _codec_problems(entry, layouts[entry] or 0, codec, codec_var)
        else:
            problems.append(
                f"DOWNMIX_LAYOUTS contains {entry} with no encoder; set {codec_var} "
                f"(one of {', '.join(CODECS)}, or any encoder your ffmpeg carries)"
            )
    by_channels: dict[int, list[str]] = {}
    for entry, channels in layouts.items():
        if channels:
            by_channels.setdefault(channels, []).append(entry)
    for channels, entries in sorted(by_channels.items()):
        # Two entries of one channel count generate identical tracks.
        if len(entries) > 1:
            problems.append(
                f"DOWNMIX_LAYOUTS entries {', '.join(sorted(entries))} are all "
                f"{channels} channels; keep one"
            )
    if config.REGENERATE_SCOPE not in REGENERATE_SCOPES:
        problems.append(
            f"REGENERATE_SCOPE={config.REGENERATE_SCOPE!r} is not one of: "
            + ", ".join(REGENERATE_SCOPES)
        )
    return problems


def warnings() -> list[str]:
    """Settings switched on that cannot do anything as configured. Each may be
    half an edit, so none refuses startup. The settings pages carry the same
    notes inline."""
    problems: list[str] = []
    modes = resolved_modes()
    # A lossless encoder ignores the rate. A warning, since a lossless downmix
    # is legitimate and the rate is required of every layout.
    problems += [
        f"{config.codec_variable(layout.name)}={layout.codec} is lossless, so the "
        f"{layout.bitrate} at {config.bitrate_variable(layout.name)} does nothing"
        for layout in resolved_layouts()
        if (codec := CODECS.get(layout.codec)) and codec.lossless
    ]
    # The languages rule drops the source before the downmix can use it.
    if (
        config.DOWNMIX_ORIGINAL_LANG
        and modes["downmix"] != NEVER
        and modes["languages"] != NEVER
        and not config.KEEP_ORIGINAL_LANG
    ):
        problems.append(
            "DOWNMIX_ORIGINAL_LANG is set but KEEP_ORIGINAL_LANG is not, so the original "
            "language is dropped before anything can be downmixed from it"
        )
    # Remux converts every allowed container that is not already Matroska.
    if modes["remux"] != NEVER and not config.ALLOWED_EXTS - TAG_PRESERVING_EXTS:
        problems.append(
            f"{config.rule_variable('remux')}={modes['remux']} but ALLOWED_EXTS names no "
            "container to convert from, so nothing is remuxed (it converts anything but "
            f"{', '.join(TAG_PRESERVING_EXTS)})"
        )
    # Regeneration finds its own tracks by a tag only these containers keep.
    if modes["regenerate"] != NEVER and not config.ALLOWED_EXTS & TAG_PRESERVING_EXTS:
        problems.append(
            f"{config.rule_variable('regenerate')}={modes['regenerate']} but ALLOWED_EXTS "
            f"names none of {', '.join(TAG_PRESERVING_EXTS)}, the only containers that keep "
            "the tag it finds its own tracks by, so nothing is regenerated"
        )
    # Legitimate as a report-only setup, but also what a page of switches
    # flipped one at a time ends up as.
    if not any(mode == ALWAYS for mode in modes.values()):
        problems.append(
            "no rule is set to always, so nothing can order a rewrite and every "
            "ride-along waits for one that never comes"
        )
    return problems
