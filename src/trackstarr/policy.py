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
from .tracks import (
    ACTIONS,
    CODECS,
    DOWNMIX,
    LANG_ACTIONS,
    ORIGINAL,
    STOCK,
    Lang,
    Layout,
    Spec,
    bitrate_bps,
    downmixed_layouts,
    lang_name,
    parse_channels,
    parse_lang,
    removed_layouts,
    resolved_langs,
    resolved_layouts,
    split_entry,
)

#: What a rule may be set to, weakest first. "alongside" means: make this
#: change when something else is already rewriting the file, never on its own,
#: since a 60GB remux costs more than clearing release tags is worth.
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
        "Drop audio and subtitle tracks in a language LANGUAGES does not name. "
        "Untagged tracks always stay.",
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
    "regenerate": Rule(
        NEVER,
        "Rebuild the downmixes trackstarr made when their codec or bitrate no "
        "longer matches the settings (REGENERATE_SCOPE=generated). With "
        "REGENERATE_SCOPE=all, also replace any layout-sized track reported "
        "under REGENERATE_BELOW_PERCENT of its layout's rate where a surviving "
        "bigger track has more to give. A replacement is a fresh downmix from the "
        "best surviving bigger track. REGENERATE_ABOVE_PERCENT re-encodes a track "
        "that far over its layout's rate from itself, lossless tracks aside.",
    ),
    "cover_art": Rule(ALWAYS, "Drop embedded cover art."),
    "release_tags": Rule(ALONGSIDE, "Clear release tags from track and container titles."),
    "stray_streams": Rule(ALONGSIDE, "Drop data and timecode streams nothing plays."),
    "order": Rule(
        ALWAYS,
        "Order streams: video, audio in AUDIO_LAYOUTS order then other sizes by "
        "channel count, subtitles, attachments.",
    ),
    "remux": Rule(
        NEVER,
        "Rewrite MP4 and M4V into Matroska, the container every rule works in. "
        "Text subtitles convert to SRT. Only the usual downmixes are encoded.",
    ),
}

#: The changes with no mode of their own: a row set to downmix or remove is the
#: whole switch. Both always act, and both name themselves in the history.
LIST_RULES = ("downmix", "drop_layouts")
DOWNMIX_RULE, DROP_LAYOUTS_RULE = LIST_RULES

#: Every name a plan may put in Plan.rules or Plan.incidental_rules. Wider than
#: RULES, which is only what RULE_<NAME> sets a mode for.
RULE_NAMES = frozenset(RULES) | set(LIST_RULES)

#: How far the regenerate rule reaches. errors() refuses other values.
REGENERATE_SCOPES = ("generated", "all")


def resolved_modes(stated: dict[str, str]) -> dict[str, str]:
    """Every rule and its mode, RULE_MODES filled in with the defaults. An
    unknown rule or mode is left out and reaches :func:`errors`."""
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

    #: Every language kept, in LANGUAGES order, which is the downmix source
    #: preference. Resolve against a title before reading: a row may name
    #: ORIGINAL.
    languages: tuple[Lang, ...]
    allowed_exts: frozenset[str]
    #: Every rule and its mode, sorted by name. A tuple of pairs rather than a
    #: dict so the field stays hashable and fingerprints deterministically.
    rule_modes: tuple[tuple[str, str], ...]
    #: How far the regenerate rule reaches; see REGENERATE_SCOPES.
    regenerate_scope: str
    #: Percent of its layout's rate under which "all" calls a track low-bitrate.
    regenerate_below: int
    #: Percent of its layout's rate over which a track is re-encoded from
    #: itself. 0 leaves every fat track alone.
    regenerate_above: int
    #: Every layout named, in AUDIO_LAYOUTS order, which is the audio order.
    #: Each says whether it is downmixed, kept or removed; see :data:`ACTIONS`.
    audio_layouts: tuple[Layout, ...]
    skip_hardlinks: bool
    commentary_re: re.Pattern[str]
    sdh_re: re.Pattern[str]
    forced_re: re.Pattern[str]
    release_tag_re: re.Pattern[str]

    @classmethod
    def from_config(cls) -> Policy:
        """The rules in force. One snapshot for all twelve, so a save landing
        mid-read cannot fingerprint a mix of both."""
        settings = config.current()
        return cls(
            languages=tuple(resolved_langs(settings.LANGUAGES)),
            allowed_exts=frozenset(settings.ALLOWED_EXTS),
            rule_modes=tuple(sorted(resolved_modes(settings.RULE_MODES).items())),
            regenerate_scope=settings.REGENERATE_SCOPE,
            regenerate_below=settings.REGENERATE_BELOW_PERCENT,
            regenerate_above=settings.REGENERATE_ABOVE_PERCENT,
            audio_layouts=tuple(resolved_layouts(settings.AUDIO_LAYOUTS)),
            skip_hardlinks=settings.SKIP_HARDLINKS,
            commentary_re=settings.COMMENTARY_RE,
            sdh_re=settings.SDH_RE,
            forced_re=settings.FORCED_RE,
            release_tag_re=settings.RELEASE_TAG_RE,
        )

    def downmixed(self) -> list[Layout]:
        """The layouts guaranteed to exist, in order."""
        return [layout for layout in self.audio_layouts if layout.downmixes]

    def removed(self) -> list[Layout]:
        """The layouts deleted wherever a file has one."""
        return [layout for layout in self.audio_layouts if layout.removes]

    def rules_in(self, *modes: str) -> frozenset[str]:
        """The rules set to any of these modes."""
        return frozenset(name for name, mode in self.rule_modes if mode in modes)

    def needs_original_lang(self) -> bool:
        """Whether a verdict can turn on the title's original language, which
        is what makes an *arr outage worth waiting out."""
        return any(lang.name == ORIGINAL for lang in self.languages)

    def resolve(self, original_lang: str | None) -> tuple[Lang, ...]:
        """The language list for one title, with ORIGINAL substituted. An
        explicit row beats it, and an *arr that cannot answer drops it."""
        named = {lang.name for lang in self.languages}
        resolved = []
        for lang in self.languages:
            if lang.name != ORIGINAL:
                resolved.append(lang)
            elif original_lang and original_lang not in named:
                resolved.append(Lang(original_lang, lang.action))
        return tuple(resolved)

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
        return f"{value.name}:{value.action}:{value.codec}:{value.bitrate}"
    if isinstance(value, Lang):
        return f"{value.name}:{value.action}"
    return value


def output_exts() -> frozenset[str]:
    """The containers a rewrite may end up writing into.

    With the remux rule on at all, everything is converted on the way out, so
    an encoder only Matroska holds is fine. A container with no muxer is left
    out: it is already its own error.
    """
    settings = config.current()
    if resolved_modes(settings.RULE_MODES)["remux"] != NEVER:
        return frozenset(TAG_PRESERVING_EXTS)
    return frozenset(settings.ALLOWED_EXTS & MUXERS.keys())


def _codec_problems(entry: str, channels: int, codec: str) -> list[str]:
    """What a layout's encoder cannot do, as ready-to-log messages.

    Only for encoders :data:`trackstarr.tracks.CODECS` knows; an unlisted one
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
            f"AUDIO_LAYOUTS entry {entry!r} asks {codec} for {channels} channels, but it "
            f"encodes at most {known.max_channels}, so every rewrite of one would fail"
        )
    if unwritable := sorted(output_exts() - known.containers):
        problems.append(
            f"AUDIO_LAYOUTS entry {entry!r} cannot be written into "
            f"{', '.join(unwritable)}, which ALLOWED_EXTS names. Set RULE_REMUX, drop the "
            "container, or pick another encoder"
        )
    return problems


def _spec_problems(entry: str, spec: Spec, channels: int) -> list[str]:
    """What one entry of a usable shape and size still gets wrong.

    One message per field, since one entry holds all three. Only a downmix is
    encoded, so only it needs an encoder and rate.
    """
    if spec.action not in ACTIONS:
        return [
            f"AUDIO_LAYOUTS entry {entry!r} says {spec.action!r}, which is not one of: "
            + ", ".join(ACTIONS)
        ]
    if spec.action != DOWNMIX:
        return []
    if not spec.codec and not spec.bitrate:
        return [
            (
                f"AUDIO_LAYOUTS entry {entry!r} states no encoder and rate, and {spec.name} "
                f"is not a size they are shipped for ({', '.join(sorted(STOCK))}). Write it "
                f"as {spec.name}:ENCODER:RATE (one of {', '.join(CODECS)}, or any encoder "
                "your ffmpeg carries)"
            )
        ]
    if not spec.codec:
        return [
            (
                f"AUDIO_LAYOUTS entry {entry!r} states no encoder. Write it as "
                f"{spec.name}:ENCODER:{spec.bitrate} (one of {', '.join(CODECS)}, or any "
                "encoder your ffmpeg carries)"
            )
        ]
    problems = []
    if not spec.bitrate:
        problems.append(
            f"AUDIO_LAYOUTS entry {entry!r} states no rate. Write it as "
            f"{spec.name}:{spec.codec}:RATE (in forms like 640k)"
        )
    elif bitrate_bps(spec.bitrate) is None:
        problems.append(
            f"AUDIO_LAYOUTS entry {entry!r} states {spec.bitrate!r}, which is not a "
            "bitrate (use forms like 640k)"
        )
    return problems + _codec_problems(entry, channels, spec.codec)


def _layout_problems(entries: tuple[str, ...]) -> list[str]:
    """What is wrong with AUDIO_LAYOUTS, worst shape first: unreadable entries,
    then ones naming no layout, then the fields of the rest."""
    malformed: list[str] = []
    unrecognised: list[str] = []
    sized: dict[str, tuple[Spec, int]] = {}
    for entry in entries:
        if (spec := split_entry(entry)) is None:
            malformed.append(entry)
        elif (channels := parse_channels(spec.name)) is None:
            unrecognised.append(entry)
        else:
            sized[entry] = (spec, channels)

    problems = []
    if malformed:
        problems.append(
            "AUDIO_LAYOUTS entries are not a layout, an action or an encoder and rate: "
            f"{', '.join(sorted(malformed))} (use forms like 5.1, 7.1:remove, 5.1:eac3:448k)"
        )
    if unrecognised:
        problems.append(
            f"AUDIO_LAYOUTS contains unrecognised layouts: {', '.join(sorted(unrecognised))} "
            "(use forms like 2.0, 5.1)"
        )
    for entry in sorted(sized):
        problems += _spec_problems(entry, *sized[entry])

    by_channels: dict[int, list[str]] = {}
    for entry, (_, channels) in sized.items():
        by_channels.setdefault(channels, []).append(entry)
    # Two opinions about one set of tracks: added and removed at once never settles.
    problems += [
        f"AUDIO_LAYOUTS entries {', '.join(sorted(entries))} are all {count} channels, "
        "so keep one"
        for count, entries in sorted(by_channels.items())
        if len(entries) > 1
    ]
    return problems


def _lang_problems(entries: tuple[str, ...]) -> list[str]:
    """What is wrong with LANGUAGES: an entry of no usable shape, then one
    naming no language, then a bad action."""
    problems = []
    for entry in entries:
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) > 2:
            problems.append(
                f"LANGUAGES entry {entry!r} is not a language or a language and an action "
                "(use forms like eng, fre:keep)"
            )
        elif not lang_name(parts[0]):
            problems.append(
                f"LANGUAGES entry {entry!r} names no language (use a code like eng, a name "
                f"like English, or {ORIGINAL})"
            )
        elif len(parts) == 2 and parts[1] not in LANG_ACTIONS:
            problems.append(
                f"LANGUAGES entry {entry!r} says {parts[1]!r}, which is not one of: "
                f"{', '.join(LANG_ACTIONS)} ({config.rule_variable('languages')} drops "
                "everything the list does not name)"
            )
    by_code: dict[str, list[str]] = {}
    for entry in entries:
        if (lang := parse_lang(entry)) is not None:
            by_code.setdefault(lang.name, []).append(entry)
    # Two opinions about one set of tracks, as with a repeated channel count.
    problems += [
        f"LANGUAGES entries {', '.join(sorted(entries))} are all {code}, so keep one"
        for code, entries in sorted(by_code.items())
        if len(entries) > 1
    ]
    return problems


def errors() -> list[str]:
    """Startup-fatal problems with the rule vocabulary, which would otherwise
    fail silently. :mod:`trackstarr.cli` reports these beside config.errors()."""
    settings = config.current()
    problems: list[str] = []
    if unmuxable := settings.ALLOWED_EXTS - MUXERS.keys():
        problems.append(
            f"ALLOWED_EXTS contains containers with no known muxer: "
            f"{', '.join(sorted(unmuxable))} (valid: {', '.join(sorted(MUXERS))})"
        )
    for rule, mode in sorted(settings.RULE_MODES.items()):
        variable = config.rule_variable(rule)
        if rule not in RULES:
            problems.append(f"{variable} names no rule (valid: {', '.join(RULES)})")
        elif mode not in MODES:
            problems.append(f"{variable}={mode!r} is not one of: {', '.join(MODES)}")
    problems += _layout_problems(settings.AUDIO_LAYOUTS)
    problems += _lang_problems(settings.LANGUAGES)
    if settings.REGENERATE_SCOPE not in REGENERATE_SCOPES:
        problems.append(
            f"REGENERATE_SCOPE={settings.REGENERATE_SCOPE!r} is not one of: "
            + ", ".join(REGENERATE_SCOPES)
        )
    return problems


def warnings() -> list[str]:
    """Settings switched on that cannot do anything as configured, or that undo
    each other. Each may be half an edit, so none refuses startup. The settings
    pages carry the same notes inline."""
    settings = config.current()
    problems: list[str] = []
    modes = resolved_modes(settings.RULE_MODES)
    # A lossless encoder ignores the rate. A warning, since a lossless downmix
    # is legitimate and the rate is required of every added layout.
    problems += [
        f"AUDIO_LAYOUTS makes {layout.name} with {layout.codec}, which is lossless, so "
        f"the {layout.bitrate} beside it does nothing"
        for layout in downmixed_layouts(settings.AUDIO_LAYOUTS)
        if (codec := CODECS.get(layout.codec)) and codec.lossless
    ]
    # Only untagged tracks would survive.
    if modes["languages"] != NEVER and not resolved_langs(settings.LANGUAGES):
        problems.append(
            f"LANGUAGES is empty and {config.rule_variable('languages')}="
            f"{modes['languages']} drops every language, so only untagged tracks survive"
        )
    # Only where the removed size is bigger than an added one, since a rebuild
    # downmixes from above. Not an error: trimming once and regenerating from
    # then on is a real order of work.
    added = downmixed_layouts(settings.AUDIO_LAYOUTS)
    removed = removed_layouts(settings.AUDIO_LAYOUTS)
    losing_sources = any(gone.channels > made.channels for gone in removed for made in added)
    # REGENERATE_ABOVE_PERCENT answers it: a trimmed file's own downmixes are
    # made again from themselves, so a later rate change does reach them.
    above = settings.REGENERATE_ABOVE_PERCENT
    if modes["regenerate"] != NEVER and losing_sources and not above:
        problems.append(
            f"AUDIO_LAYOUTS removes tracks {config.rule_variable('regenerate')} rebuilds "
            "downmixes from, so a later codec or rate change reaches nothing on the "
            "files it has already trimmed"
        )
    # Legitimate for a library that only reorders, and what a list edited a row
    # at a time passes through.
    if not added and not removed:
        problems.append(
            "AUDIO_LAYOUTS adds and removes no layout, so no audio track is made or "
            "dropped and the list only sets the order"
        )
    # Remux converts every allowed container that is not already Matroska.
    if modes["remux"] != NEVER and not settings.ALLOWED_EXTS - TAG_PRESERVING_EXTS:
        problems.append(
            f"{config.rule_variable('remux')}={modes['remux']} but ALLOWED_EXTS names no "
            "container to convert from, so nothing is remuxed (it converts anything but "
            f"{', '.join(TAG_PRESERVING_EXTS)})"
        )
    # Regeneration finds the tracks it made by a tag only these containers keep.
    if modes["regenerate"] != NEVER and not settings.ALLOWED_EXTS & TAG_PRESERVING_EXTS:
        problems.append(
            f"{config.rule_variable('regenerate')}={modes['regenerate']} but ALLOWED_EXTS "
            f"names none of {', '.join(TAG_PRESERVING_EXTS)}, the only containers that keep "
            "the tag marking which tracks trackstarr made, so nothing is regenerated"
        )
    # Legitimate as a report-only setup, but also what a page of switches
    # flipped one at a time ends up as.
    if not any(mode == ALWAYS for mode in modes.values()):
        problems.append(
            "no rule is set to always, so nothing can order a rewrite and every "
            "ride-along waits for one that never comes"
        )
    return problems
