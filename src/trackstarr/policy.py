"""The rule policy: every setting the rules read, as one object.

Built from :mod:`trackstarr.config` once per plan, so a plan carries the
settings it was judged under. :meth:`Policy.fingerprint` derives from the
fields, so a new setting is fingerprinted the day it lands; a hand-kept list
would let cached verdicts outlive whichever setting someone forgot.

The vocabulary those settings are written in lives here too, with
:func:`errors` to check the configured values against it.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, fields

from . import __version__, config
from .layouts import Layout, bitrate_bps, layout_bitrate, parse_channels, resolved_layouts

#: Keyed by the name DISABLED_RULES uses to switch each off, and the one
#: source for the CLI help text.
RULES = {
    "languages": "Keep audio and subtitle tracks whose language is English, the "
    "title's original language, or untagged. Drop the rest.",
    "downmix": "Guarantee a non-commentary track for each channel layout in "
    "DOWNMIX_LAYOUTS (2.0 and 5.1 by default), downmixed from the best "
    "surviving bigger track. Nothing is upmixed.",
    "cover_art": "Drop embedded cover art.",
    "order": "Order streams video, audio by ascending channel count, subtitles.",
    "sdh": "Drop SDH subtitles whose language also keeps a full subtitle, when "
    "rewriting anyway. Forced subtitles are always kept.",
}

#: Off by default, with the setting that turns each on. Beside RULES so the
#: CLI help is the one place the tool describes itself.
OPT_IN_RULES = {
    "commentary": "Drop commentary, described-audio and isolated-score tracks "
    "outright (set DROP_COMMENTARY).",
    "regenerate": "Rebuild this tool's own downmixes when their recorded "
    "codec or bitrate no longer matches config (REGENERATE_DOWNMIXES="
    "generated), or additionally replace any layout-sized track reported "
    "well below the layout's rate (REGENERATE_DOWNMIXES=all). Always fresh "
    "from the best surviving bigger track, never a re-encode in place.",
    "remux": "Rewrite MP4/M4V into Matroska, the container every feature "
    "works in (set REMUX_TO_MKV). Text subtitles convert to SRT; nothing "
    "else is re-encoded beyond the usual downmixes.",
}

#: Ride-alongs never worth a rewrite alone, so unlike RULES they have no
#: switch. Named anyway, because the history records which of them fired.
INCIDENTAL_RULES = {
    "junk_titles": "Clear release junk from track and container titles.",
    "stray_streams": "Drop data and timecode streams nothing plays.",
}

#: Every name a plan may put in Plan.rules or Plan.incidental_rules, and so
#: every name the history can carry.
RULE_NAMES = frozenset(RULES) | frozenset(OPT_IN_RULES) | frozenset(INCIDENTAL_RULES)

#: Valid REGENERATE_DOWNMIXES values besides unset. errors() refuses others.
REGENERATE_MODES = ("generated", "all")

#: The ffmpeg muxer per container, since the staging name carries no real
#: extension. errors() checks every ALLOWED_EXTS entry appears here.
MUXERS = {".mkv": "matroska", ".mp4": "mp4", ".m4v": "mp4"}

#: Containers that keep custom stream tags. Regeneration finds its own tracks
#: by that tag, so it drops nothing elsewhere: on MP4 it would re-encode its
#: own tracks every sweep and read commentary as weak. A subset of MUXERS by
#: construction, since nothing reaches the rules we cannot also write.
TAG_PRESERVING_EXTS = frozenset({".mkv"})

#: Video codecs that mean embedded artwork rather than a real video stream.
IMAGE_CODECS = frozenset({"mjpeg", "png", "gif", "bmp", "webp", "tiff"})


@dataclass(frozen=True)
class Policy:
    """Everything the rules read, resolved from config at build time.

    A plan snapshots one, so the command built from it matches the settings
    it was judged with, and the sweep cache can fingerprint just what its
    verdicts depended on.
    """

    always_keep: frozenset[str]
    allowed_exts: frozenset[str]
    disabled_rules: frozenset[str]
    drop_commentary: bool
    regenerate_downmixes: str
    remux_to_mkv: bool
    #: The layouts the downmix rule guarantees, smallest first.
    downmix_layouts: tuple[Layout, ...]
    audio_codec: str
    skip_hardlinks: bool
    commentary_re: re.Pattern[str]
    sdh_re: re.Pattern[str]
    forced_re: re.Pattern[str]
    junk_title_re: re.Pattern[str]

    @classmethod
    def from_config(cls) -> Policy:
        return cls(
            always_keep=frozenset(config.ALWAYS_KEEP_LANGS),
            allowed_exts=frozenset(config.ALLOWED_EXTS),
            disabled_rules=frozenset(config.DISABLED_RULES),
            drop_commentary=config.DROP_COMMENTARY,
            regenerate_downmixes=config.REGENERATE_DOWNMIXES,
            remux_to_mkv=config.REMUX_TO_MKV,
            downmix_layouts=tuple(resolved_layouts()),
            audio_codec=config.AUDIO_CODEC,
            skip_hardlinks=config.SKIP_HARDLINKS,
            commentary_re=config.COMMENTARY_RE,
            sdh_re=config.SDH_RE,
            forced_re=config.FORCED_RE,
            junk_title_re=config.JUNK_TITLE_RE,
        )

    def rule_enabled(self, rule: str) -> bool:
        return rule not in self.disabled_rules

    def keep_langs(self, original_lang: str | None) -> set[str]:
        """The languages rule 1 keeps for a title."""
        keep = set(self.always_keep)
        if original_lang:
            keep.add(original_lang)
        return keep

    def allowed_container(self, path: str) -> bool:
        """Whether the file is of a type the rules are willing to rewrite."""
        return os.path.splitext(path)[1].lower() in self.allowed_exts

    def fingerprint(self) -> dict:
        """Everything a cached verdict depends on besides the file itself,
        as a JSON-serialisable dict.

        Every field, plus the package version, so rule changes shipped in
        code invalidate cached verdicts too.
        """
        return {"version": __version__} | {
            field.name: _fingerprint_value(getattr(self, field.name)) for field in fields(self)
        }

    def digest(self) -> str:
        """A short, stable id for this exact policy.

        Every event carries one, so a months-old rewrite still traces to the
        settings that ordered it. ``serve`` and every sweep record the full
        fingerprint beside theirs, which is what a digest resolves against.
        Twelve hex characters: this separates the handful of settings
        generations an install goes through, not adversarial collisions.
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
        return f"{value.name}:{value.bitrate}"
    return value


def errors() -> list[str]:
    """Startup-fatal problems with the configured rule vocabulary.

    Each would otherwise fail silently: a typo leaves a rule on, drops a
    layout, regenerates nothing, or plans a container ffmpeg then chokes on.
    :mod:`trackstarr.cli` reports these beside config.errors().
    """
    problems: list[str] = []
    if unmuxable := config.ALLOWED_EXTS - MUXERS.keys():
        problems.append(
            f"ALLOWED_EXTS contains containers with no known muxer: "
            f"{', '.join(sorted(unmuxable))} (valid: {', '.join(sorted(MUXERS))})"
        )
    if unknown := config.DISABLED_RULES - RULES.keys():
        problems.append(
            f"DISABLED_RULES contains unknown rules: {', '.join(sorted(unknown))} "
            f"(valid: {', '.join(RULES)})"
        )
    layouts = {entry: parse_channels(entry) for entry in config.DOWNMIX_LAYOUTS}
    if invalid := {entry for entry, channels in layouts.items() if channels is None}:
        problems.append(
            f"DOWNMIX_LAYOUTS contains unrecognised layouts: {', '.join(sorted(invalid))} "
            "(use forms like 2.0, 5.1)"
        )
    # Name and rate are checked separately, so the report says which is wrong.
    for entry in sorted(entry for entry, channels in layouts.items() if channels):
        variable = config.bitrate_variable(entry)
        rate = layout_bitrate(entry)
        if not rate:
            problems.append(f"DOWNMIX_LAYOUTS contains {entry} with no rate; set {variable}")
        elif bitrate_bps(rate) is None:
            problems.append(f"{variable}={rate!r} is not a bitrate (use forms like 640k)")
    by_channels: dict[int, list[str]] = {}
    for entry, channels in layouts.items():
        if channels:
            by_channels.setdefault(channels, []).append(entry)
    for channels, entries in sorted(by_channels.items()):
        # Two entries of one channel count generate identical tracks and
        # leave the rules judging against whichever rate came first.
        if len(entries) > 1:
            problems.append(
                f"DOWNMIX_LAYOUTS entries {', '.join(sorted(entries))} are all "
                f"{channels} channels; keep one"
            )
    if config.REGENERATE_DOWNMIXES not in ("", *REGENERATE_MODES):
        problems.append(
            f"REGENERATE_DOWNMIXES={config.REGENERATE_DOWNMIXES!r} is not one of: "
            + ", ".join(REGENERATE_MODES)
        )
    return problems
