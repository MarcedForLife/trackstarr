"""Decide what a file needs, without touching it.

Everything here is pure given ffprobe output, which is what makes the rules
testable without media. :func:`build_plan` is the only entry point that reads
from disk: a stat for the hardlink check, then :func:`trackstarr.media.probe`.

The rules are listed in :data:`RULES`. Every rule is idempotent: applying the
result and re-planning yields an empty plan, so the sweep is safe to run as
often as you like.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import __version__, config
from .layouts import Layout, bitrate_bps, encode_settings, parse_layout, resolved_layouts
from .media import (
    GENERATED_TAG,
    container_title,
    duration,
    generated_settings,
    is_commentary,
    is_cover_art,
    is_forced,
    is_junk_title,
    is_sdh,
    probe,
    stream_bitrate,
    stream_lang,
    stream_title,
    title_is_load_bearing,
)

#: The rules, keyed by the name DISABLED_RULES uses to switch each off.
#: Single source for the module docstring above and the CLI help text.
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

#: Behaviours that are off by default, with the setting that turns each on.
#: Named beside RULES so the CLI help stays the one place the tool describes
#: itself.
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


#: Valid REGENERATE_DOWNMIXES values besides unset.
REGENERATE_MODES = ("generated", "all")


def config_errors() -> list[str]:
    """Startup-fatal configuration problems, as ready-to-log messages.

    Each would otherwise fail silently: a typo leaves a rule on, drops a
    layout, or regenerates nothing.
    """
    errors = []
    if unknown := set(config.DISABLED_RULES) - RULES.keys():
        errors.append(
            f"DISABLED_RULES contains unknown rules: {', '.join(sorted(unknown))} "
            f"(valid: {', '.join(RULES)})"
        )
    if invalid := {entry for entry in config.DOWNMIX_LAYOUTS if parse_layout(entry) is None}:
        errors.append(
            f"DOWNMIX_LAYOUTS contains unrecognised layouts: {', '.join(sorted(invalid))} "
            "(use forms like 2.0, 5.1:640k)"
        )
    by_channels: dict[int, list[str]] = {}
    for entry in config.DOWNMIX_LAYOUTS:
        if layout := parse_layout(entry):
            by_channels.setdefault(layout.channels, []).append(entry)
    for channels, entries in sorted(by_channels.items()):
        # Two entries for one channel count would generate identical tracks
        # and leave the rules judging against an arbitrary one of the rates.
        if len(entries) > 1:
            errors.append(
                f"DOWNMIX_LAYOUTS entries {', '.join(sorted(entries))} are all "
                f"{channels} channels; keep one"
            )
    if config.REGENERATE_DOWNMIXES not in ("", *REGENERATE_MODES):
        errors.append(
            f"REGENERATE_DOWNMIXES={config.REGENERATE_DOWNMIXES!r} is not one of: "
            + ", ".join(REGENERATE_MODES)
        )
    return errors


def rules_fingerprint() -> dict:
    """Everything a cached plan verdict depends on besides the file itself.

    The config values the rules read, plus the package version so rule
    changes shipped in code invalidate cached verdicts too.
    """
    return {
        "version": __version__,
        "always_keep": sorted(config.ALWAYS_KEEP),
        "commentary_pattern": config.COMMENTARY_RE.pattern,
        "image_codecs": sorted(config.IMAGE_CODECS),
        "skip_hardlinks": config.SKIP_HARDLINKS,
        "disabled_rules": sorted(config.DISABLED_RULES),
        "downmix_layouts": sorted(config.DOWNMIX_LAYOUTS),
        # These decide verdicts only under REGENERATE_DOWNMIXES, but always
        # fingerprinting them just means a codec tweak re-probes once.
        "audio_codec": config.AUDIO_CODEC,
        "audio_bitrate": config.AUDIO_BITRATE,
        "regenerate_downmixes": config.REGENERATE_DOWNMIXES,
        "remux_to_mkv": config.REMUX_TO_MKV,
        "drop_commentary": config.DROP_COMMENTARY,
        "junk_title_pattern": config.JUNK_TITLE_RE.pattern,
        "sdh_pattern": config.SDH_RE.pattern,
        "forced_pattern": config.FORCED_RE.pattern,
    }


def channel_rank(channels: int | None) -> tuple[bool, int]:
    """Ascending channel count, mono and unknown last.

    The first audio track is what disposition-blind players fall back to, so
    it must never be a mono track while anything better exists. A generated
    layout of any size (4.0, 6.1) slots in by its count like the rest.
    """
    return (not channels or channels < 2, channels or 0)


def keep_langs(original_lang: str | None) -> set[str]:
    """The languages rule 1 keeps for a title."""
    keep = set(config.ALWAYS_KEEP)
    if original_lang:
        keep.add(original_lang)
    return keep


def allowed_container(path: str) -> bool:
    """Whether the file is of a type the rules are willing to rewrite."""
    return os.path.splitext(path)[1].lower() in config.ALLOWED_EXTS


#: Containers that preserve custom stream tags. Regeneration depends on the
#: tag to recognise its own tracks, so it never drops anything elsewhere: on
#: MP4 it would re-encode its own unrecognisable tracks every sweep, and
#: judge commentary (whose title-based protection is also container-fragile)
#: as a weak track to delete.
TAG_PRESERVING_EXTS = {".mkv", ".webm"}


def hardlinked(path: str) -> bool:
    """More than one directory entry shares the file's inode.

    In an *arr setup that means the download client is still seeding it. An
    unreadable file counts as not hardlinked; the probe will report it.
    """
    try:
        return os.stat(path).st_nlink > 1
    except OSError:
        return False


@dataclass
class OutStream:
    """One stream in the output, and where it comes from in the input.

    ``title`` is the generated track's name on encode streams and the
    source's own title on copied audio and subtitles, re-asserted in the
    command because MP4 drops track names on a plain copy. ``lang`` and
    ``bitrate`` are set only on generated downmixes.
    """

    src: int  # stream index in the input file
    kind: str  # video | audio | subtitle | attachment
    encode: bool = False  # True only for generated downmixes
    channels: int | None = None
    lang: str | None = None
    title: str = ""
    bitrate: str | None = None  # resolved encode rate, generated downmixes only
    clear_title: bool = False  # strip a junk title while rewriting anyway
    sub_codec: str | None = None  # convert a subtitle while remuxing (mov_text -> srt)


@dataclass
class Plan:
    path: str
    streams: list[OutStream] = field(default_factory=list)
    #: Changes that justify a rewrite on their own.
    reasons: list[str] = field(default_factory=list)
    #: Changes that ride along with a rewrite but never trigger one. Rewriting
    #: a 60GB remux to drop a stray timecode track costs far more than the
    #: track does.
    incidental: list[str] = field(default_factory=list)
    original_lang: str | None = None
    keep_langs: set[str] = field(default_factory=set)
    #: Rule keys switched off for this plan, resolved from config at build
    #: time like keep_langs, so the plan carries the policy it was judged
    #: under and the CLI can show it.
    disabled_rules: set[str] = field(default_factory=set)
    #: The DROP_COMMENTARY opt-in, resolved the same way.
    drop_commentary: bool = False
    #: The REGENERATE_DOWNMIXES mode ("", "generated" or "all"), resolved
    #: the same way.
    regenerate_downmixes: str = ""
    #: The REMUX_TO_MKV opt-in, resolved the same way.
    remux_to_mkv: bool = False
    #: The layouts the downmix rule guarantees, smallest first, resolved
    #: from config the same way.
    downmix_layouts: list[Layout] = field(default_factory=list)
    #: Source duration at plan time, checked against the rewrite result
    #: before anything is overwritten. Zero when the probe did not carry one.
    src_duration: float = 0.0
    #: Strip a junk container title while rewriting anyway.
    clear_container_title: bool = False
    #: Set when the file cannot or need not be touched at all.
    skip: str | None = None

    @property
    def needed(self) -> bool:
        return not self.skip and bool(self.reasons)

    @property
    def out_path(self) -> str:
        """Where the rewrite lands: the source path, or its .mkv sibling
        when REMUX_TO_MKV converts the container."""
        base, ext = os.path.splitext(self.path)
        if self.remux_to_mkv and ext.lower() not in TAG_PRESERVING_EXTS:
            return base + ".mkv"
        return self.path

    def rule_enabled(self, rule: str) -> bool:
        return rule not in self.disabled_rules


def describe(plan: Plan) -> str:
    parts = list(plan.reasons)
    parts += [f"(also {item})" for item in plan.incidental]
    return "; ".join(parts)


def new_plan(path: str, original_lang: str | None) -> Plan:
    """A Plan carrying the policy resolved from config at build time."""
    return Plan(
        path=path,
        original_lang=original_lang,
        keep_langs=keep_langs(original_lang),
        disabled_rules=set(config.DISABLED_RULES),
        drop_commentary=config.DROP_COMMENTARY,
        regenerate_downmixes=config.REGENERATE_DOWNMIXES,
        remux_to_mkv=config.REMUX_TO_MKV,
        downmix_layouts=resolved_layouts(),
    )


def build_plan(path: str, original_lang: str | None) -> Plan:
    plan = new_plan(path, original_lang)
    if not allowed_container(path):
        ext = os.path.splitext(path)[1].lower()
        plan.skip = f"container {ext or '(none)'} not in ALLOWED_EXTS"
        return plan
    if config.SKIP_HARDLINKS and hardlinked(path):
        plan.skip = "hardlinked, left for the download client (SKIP_HARDLINKS)"
        return plan
    return plan_from_probe(plan, probe(path))


def plan_from_probe(plan: Plan, info: dict) -> Plan:
    """The rules themselves, split out so tests can feed synthetic probe data."""
    streams = info.get("streams") or []
    plan.src_duration = duration(info)

    video, audio, subs, attachments = _split_streams(plan, streams)
    if not video:
        plan.skip = "no video stream"
        return plan

    if plan.out_path != plan.path:
        plan.reasons.append("remux to mkv (REMUX_TO_MKV)")

    def keep_track(stream: dict, what: str) -> bool:
        if not plan.rule_enabled("languages"):
            return True
        lang = stream_lang(stream)
        if lang is None or lang in plan.keep_langs:
            return True
        plan.reasons.append(f"drop {what} {_stream_label(stream, lang)}")
        return False

    kept_audio = [stream for stream in audio if keep_track(stream, "audio")]
    kept_subs = [stream for stream in subs if keep_track(stream, "subtitle")]

    if not kept_audio:
        # Never leave a file silent: whether every audio track failed the
        # language test or there were none to begin with, keep the original.
        plan.skip = "would remove every audio track" if audio else "no audio streams"
        return plan

    kept_audio = _drop_commentary(plan, kept_audio)
    kept_audio = _drop_stale_downmixes(plan, kept_audio)
    kept_subs = _drop_redundant_sdh(plan, kept_subs)

    audio_out = [
        OutStream(
            src=stream["index"],
            kind="audio",
            channels=stream.get("channels"),
            title=stream_title(stream),
            clear_title=_flag_junk_title(plan, stream),
        )
        for stream in kept_audio
    ]
    for layout, src in _choose_downmixes(plan, kept_audio):
        plan.reasons.append(
            f"add {layout.name} downmix from stream {src['index']} "
            f"({src.get('channels')}ch {stream_lang(src) or 'und'})"
        )
        audio_out.append(
            OutStream(
                src=src["index"],
                kind="audio",
                encode=True,
                channels=layout.channels,
                lang=stream_lang(src),
                title=layout.name,
                bitrate=layout.bitrate,
            )
        )
    # A generated track sorts ahead of an existing track of equal rank.
    audio_out.sort(key=lambda out: (channel_rank(out.channels), not out.encode, out.src))

    ordered = [OutStream(src=stream["index"], kind="video") for stream in video]
    ordered += audio_out
    converting = plan.out_path != plan.path
    ordered += [
        OutStream(
            src=stream["index"],
            kind="subtitle",
            title=stream_title(stream),
            clear_title=_flag_junk_title(plan, stream),
            # Matroska has no mov_text; the text converts losslessly to SRT.
            sub_codec=(
                "srt" if converting and stream.get("codec_name") == "mov_text" else None
            ),
        )
        for stream in kept_subs
    ]
    ordered += [OutStream(src=stream["index"], kind="attachment") for stream in attachments]

    file_title = container_title(info)
    if is_junk_title(file_title):
        plan.incidental.append(f"clear junk container title ({file_title!r})")
        plan.clear_container_title = True

    if plan.rule_enabled("order"):
        # Rule 4 is worth a rewrite on its own, but only when the order of the
        # streams we are keeping actually differs — comparing against every
        # input stream would make any dropped stream look like a reordering.
        kept_src = {out.src for out in ordered}
        current = [stream["index"] for stream in streams if stream["index"] in kept_src]
        if not plan.reasons and [out.src for out in ordered] != current:
            plan.reasons.append("reorder streams")
    else:
        # A rewrite the other rules trigger must still preserve the input's
        # stream order; the generated downmix rides after its source track.
        ordered.sort(key=lambda out: (out.src, out.encode))
    plan.streams = ordered

    return plan


def _split_streams(
    plan: Plan, streams: list[dict]
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Bucket the input by kind, recording cover-art and stray-stream drops."""
    video: list[dict] = []
    audio: list[dict] = []
    subs: list[dict] = []
    attachments: list[dict] = []
    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video":
            if plan.rule_enabled("cover_art") and is_cover_art(stream):
                plan.reasons.append(
                    f"drop cover art (stream {stream['index']}, {stream.get('codec_name')})"
                )
            else:
                video.append(stream)
        elif kind == "audio":
            audio.append(stream)
        elif kind == "subtitle":
            subs.append(stream)
        elif kind == "attachment":
            # Fonts for ASS/SSA subtitles. Dropping these silently breaks
            # styled subtitle rendering, so they are always carried over.
            attachments.append(stream)
        else:
            plan.incidental.append(f"drop {kind} stream {stream['index']}")
    return video, audio, subs, attachments


def _stream_label(stream: dict, detail: str = "") -> str:
    """``index (detail, title)``, with empty parts and empty parens omitted."""
    parts = [detail] if detail else []
    if title := stream_title(stream):
        parts.append(title)
    return f"{stream['index']} ({', '.join(parts)})" if parts else str(stream["index"])


def _flag_junk_title(plan: Plan, stream: dict) -> bool:
    """Record a junk title for clearing. Rides along, never triggers."""
    title = stream_title(stream)
    if not is_junk_title(title) or title_is_load_bearing(stream):
        return False
    plan.incidental.append(
        f"clear junk title on {stream.get('codec_type')} {stream['index']} ({title!r})"
    )
    return True


def _drop_redundant_sdh(plan: Plan, kept_subs: list[dict]) -> list[dict]:
    """Drop SDH subtitles whose language also keeps a full subtitle.

    Rides along with a rewrite, never triggers one: a text subtitle is the
    stray-timecode case, far cheaper than the rewrite that would remove it.
    Forced subtitles are never candidates in either direction: they are
    always kept, and never make an SDH track redundant.
    """
    if not plan.rule_enabled("sdh"):
        return kept_subs
    sdh: list[dict] = []
    full_langs: set[str | None] = set()
    for stream in kept_subs:
        if is_forced(stream):
            continue
        if is_sdh(stream):
            sdh.append(stream)
        else:
            full_langs.add(stream_lang(stream))
    redundant: set[int] = set()
    for stream in sdh:
        if stream_lang(stream) in full_langs:
            plan.incidental.append(f"drop SDH subtitle {_stream_label(stream)}")
            redundant.add(stream["index"])
    return [stream for stream in kept_subs if stream["index"] not in redundant]


def _drop_commentary(plan: Plan, kept_audio: list[dict]) -> list[dict]:
    """Drop commentary tracks when DROP_COMMENTARY asks for it.

    Declines entirely when every surviving track is commentary: the other
    rules still apply, but the file is never left silent.
    """
    if not plan.drop_commentary:
        return kept_audio
    keep: list[dict] = []
    dropped: list[dict] = []
    for stream in kept_audio:
        if is_commentary(stream):
            dropped.append(stream)
        else:
            keep.append(stream)
    if not keep:
        return kept_audio
    for stream in dropped:
        plan.reasons.append(f"drop commentary audio {_stream_label(stream)}")
    return keep


def _downmix_sources(
    streams: list[dict], channels: int, exclude: dict | None = None
) -> list[dict]:
    """Non-commentary tracks bigger than ``channels``: the pool a downmix
    for that layout is made from.

    The one definition both the drop side and the rebuild side use, so a
    track is never dropped unless the rebuild would find a source.
    """
    return [
        stream
        for stream in streams
        if stream is not exclude
        and (stream.get("channels") or 0) > channels
        and not is_commentary(stream)
    ]


def _drop_stale_downmixes(plan: Plan, kept_audio: list[dict]) -> list[dict]:
    """Drop tracks the downmix rule should rebuild fresh from their source.

    The REGENERATE_DOWNMIXES opt-in; see :func:`_stale_reason` for what each
    mode drops. Nothing is dropped unless a bigger track to rebuild the
    layout from survives, so a file never loses a layout it had, and only
    tag-preserving containers are touched at all.
    """
    if not plan.regenerate_downmixes or not plan.rule_enabled("downmix"):
        return kept_audio
    if os.path.splitext(plan.path)[1].lower() not in TAG_PRESERVING_EXTS:
        return kept_audio
    # The same first-wins choice _choose_downmixes makes, so a track is
    # always judged against the layout it would be rebuilt with.
    layouts: dict[int | None, Layout] = {}
    for layout in plan.downmix_layouts:
        layouts.setdefault(layout.channels, layout)
    keep: list[dict] = []
    for stream in kept_audio:
        layout = layouts.get(stream.get("channels"))
        why = _stale_reason(plan, stream, layout) if layout else None
        if why and _downmix_sources(kept_audio, layout.channels, exclude=stream):
            plan.reasons.append(why)
        else:
            keep.append(stream)
    return keep


#: How far below its layout's rate a track must report before "all" replaces
#: it: not even half. Deliberately far from 1.0: encoders emit what the
#: content needs rather than the nominal request, and codecs differ in
#: efficiency, so a decent 640k AC3 5.1 must not read as weak against a
#: 960k AAC target.
_WEAK_BITRATE_RATIO = 0.5


def _stale_reason(plan: Plan, stream: dict, layout: Layout) -> str | None:
    """Why a layout-sized track should be rebuilt, or None to keep it.

    A track carrying GENERATED_TAG is ours: rebuilt when its recorded
    settings no longer match config. Under "all", any other real track
    reported below _WEAK_BITRATE_RATIO of the layout's rate is replaced
    too. Unknown bitrates and commentary are left alone, and an original
    track is never re-encoded in place either way, only replaced by a
    fresh downmix from a bigger track.
    """
    recorded = generated_settings(stream)
    desired = encode_settings(layout.bitrate)
    if recorded is not None:
        if recorded == desired:
            return None
        return (
            f"regenerate {layout.name} downmix {_stream_label(stream, recorded)} as {desired}"
        )
    if plan.regenerate_downmixes != "all" or is_commentary(stream):
        return None
    reported = stream_bitrate(stream)
    target = bitrate_bps(layout.bitrate) or 0
    if reported is None or reported >= target * _WEAK_BITRATE_RATIO:
        return None
    return (
        f"replace weak {layout.name} track {_stream_label(stream, f'{reported // 1000}k')} "
        f"with a fresh downmix"
    )


def _choose_downmixes(plan: Plan, kept_audio: list[dict]) -> list[tuple[Layout, dict]]:
    """One ``(layout, source)`` per configured layout the file misses.

    A layout is missed when no real (non-commentary) track has its channel
    count. Each is downmixed from the best surviving bigger track; a layout
    with nothing bigger to make it from is skipped, nothing is upmixed.
    """
    if not plan.rule_enabled("downmix"):
        return []
    real = [stream for stream in kept_audio if not is_commentary(stream)]
    # Two layout names with the same channel count ("4.2" and "5.1") would
    # generate identical tracks, so a satisfied count also satisfies the rest.
    satisfied = {stream.get("channels") for stream in real}
    chosen: list[tuple[Layout, dict]] = []
    for layout in plan.downmix_layouts:
        if layout.channels in satisfied:
            continue
        candidates = _downmix_sources(kept_audio, layout.channels)
        if not candidates:
            continue
        src = min(candidates, key=lambda stream: _downmix_rank(stream, plan.original_lang))
        chosen.append((layout, src))
        satisfied.add(layout.channels)
    return chosen


def _downmix_rank(stream: dict, original_lang: str | None) -> tuple[int, int]:
    """Prefer the original language, then English, then whatever is left.

    Within a language, prefer the most channels to downmix from.
    """
    lang = stream_lang(stream)
    if original_lang and lang == original_lang:
        pref = 0
    elif lang == "eng":
        pref = 1
    else:
        pref = 2
    return (pref, -(stream.get("channels") or 0))


def ffmpeg_args(plan: Plan, dest: str) -> list[str]:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", plan.path]
    for out in plan.streams:
        args += ["-map", f"0:{out.src}"]
    args += ["-map_chapters", "0", "-c", "copy"]

    for out_index, out in enumerate(plan.streams):
        if out.encode:
            # A fresh encode must not inherit its source's tags: mkvmerge
            # statistics (BPS, NUMBER_OF_BYTES) would advertise the old
            # track's numbers on the new one.
            args += [f"-map_metadata:s:{out_index}", "-1"]
        else:
            # Any explicit per-stream metadata mapping disables the default
            # copying for every stream, so each copied stream re-maps its own.
            args += [f"-map_metadata:s:{out_index}", f"0:s:{out.src}"]

    audio_streams = (out for out in plan.streams if out.kind == "audio")
    for idx, out in enumerate(audio_streams):
        if out.encode:
            args += [
                f"-c:a:{idx}",
                config.AUDIO_CODEC,
                f"-ac:a:{idx}",
                str(out.channels),
                f"-b:a:{idx}",
                out.bitrate,
                f"-metadata:s:a:{idx}",
                f"title={out.title}",
                # Recorded so REGENERATE_DOWNMIXES can recognise this track
                # and its settings on a later pass.
                f"-metadata:s:a:{idx}",
                f"{GENERATED_TAG}={encode_settings(out.bitrate)}",
                # Dispositions are copied from the source stream, so without
                # this the downmix inherits `default` from the track it came
                # from and the file ends up with two default audio tracks.
                # Clearing it leaves the original default exactly where it was.
                f"-disposition:a:{idx}",
                "0",
            ]
            if out.lang:
                args += [f"-metadata:s:a:{idx}", f"language={out.lang}"]
        elif out.clear_title:
            args += [f"-metadata:s:a:{idx}", "title="]
        elif out.title:
            # MP4 drops track names on a plain copy, blinding the commentary
            # and SDH predicates on the next pass; re-assert them. Redundant
            # but harmless for Matroska.
            args += [f"-metadata:s:a:{idx}", f"title={out.title}"]

    sub_streams = (out for out in plan.streams if out.kind == "subtitle")
    for idx, out in enumerate(sub_streams):
        if out.sub_codec:
            args += [f"-c:s:{idx}", out.sub_codec]
        if out.clear_title:
            args += [f"-metadata:s:s:{idx}", "title="]
        elif out.title:
            args += [f"-metadata:s:s:{idx}", f"title={out.title}"]

    if plan.clear_container_title:
        args += ["-metadata", "title="]

    args += ["-max_muxing_queue_size", "9999", dest]
    return args
