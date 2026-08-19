"""Decide what a file needs, without touching it.

Everything here is pure given ffprobe output, which is what makes the rules
testable without media. :func:`build_plan` is the only entry point that reads
from disk: a stat for the staleness signature and hardlink check, then
:func:`trackstarr.media.probe`.

The rules are named in :data:`trackstarr.policy.RULES`. Every rule is
idempotent: applying the result and re-planning yields an empty plan, so the
sweep is safe to run as often as you like.
"""

import os
from dataclasses import dataclass, field
from typing import NamedTuple

from .layouts import Layout, bitrate_bps, encode_settings
from .media import (
    GENERATED_TAG,
    ProbeError,
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
from .policy import MUXERS, TAG_PRESERVING_EXTS, Policy


def channel_rank(channels: int | None) -> tuple[bool, int]:
    """Ascending channel count, mono and unknown last.

    The first audio track is what disposition-blind players fall back to, so
    it must never be a mono track while anything better exists. A generated
    layout of any size (4.0, 6.1) slots in by its count like the rest.
    """
    return (not channels or channels < 2, channels or 0)


class SourceSignature(NamedTuple):
    """Size and mtime of a source file, compared by the staleness checks."""

    size: int
    mtime_ns: int

    @classmethod
    def of(cls, st: os.stat_result) -> SourceSignature:
        return cls(st.st_size, st.st_mtime_ns)


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
    bitrate: str = ""  # resolved encode rate, generated downmixes only
    clear_title: bool = False  # strip a junk title while rewriting anyway
    sub_codec: str | None = None  # convert a subtitle while remuxing (mov_text -> srt)


@dataclass
class Plan:
    path: str
    #: The policy the file was judged under, resolved from config at build
    #: time, so the plan carries what it was judged with and the CLI can
    #: show it.
    policy: Policy = field(default_factory=Policy.from_config)
    streams: list[OutStream] = field(default_factory=list)
    #: Changes that justify a rewrite on their own.
    reasons: list[str] = field(default_factory=list)
    #: Changes that ride along with a rewrite but never trigger one. Rewriting
    #: a 60GB remux to drop a stray timecode track costs far more than the
    #: track does.
    incidental: list[str] = field(default_factory=list)
    original_lang: str | None = None
    keep_langs: set[str] = field(default_factory=set)
    #: Source duration at plan time, checked against the rewrite result
    #: before anything is overwritten. Zero when the probe did not carry one.
    src_duration: float = 0.0
    #: Source size and mtime at plan time. A plan can go stale waiting on the
    #: rewrite lock (a sweep and a webhook can plan the same file, and the
    #: loser waits out a whole rewrite), and applying a stale plan maps
    #: streams by indices the file no longer has. apply_plan refuses to start
    #: unless the file still matches. None when the plan was built straight
    #: from probe data, as tests do.
    src_signature: SourceSignature | None = None
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
        if self.policy.remux_to_mkv and ext.lower() not in TAG_PRESERVING_EXTS:
            return base + ".mkv"
        return self.path


def describe(plan: Plan) -> str:
    parts = list(plan.reasons)
    parts += [f"(also {item})" for item in plan.incidental]
    return "; ".join(parts)


def new_plan(path: str, original_lang: str | None) -> Plan:
    """A Plan carrying the policy resolved from config at build time."""
    policy = Policy.from_config()
    return Plan(
        path=path,
        policy=policy,
        original_lang=original_lang,
        keep_langs=policy.keep_langs(original_lang),
    )


def build_plan(path: str, original_lang: str | None) -> Plan:
    plan = new_plan(path, original_lang)
    if not plan.policy.allowed_container(path):
        ext = os.path.splitext(path)[1].lower()
        plan.skip = f"container {ext or '(none)'} not in ALLOWED_EXTS"
        return plan
    try:
        src = os.stat(path)
    except OSError as err:
        # The probe would fail on the same unreadable file; same outcome.
        raise ProbeError(f"cannot stat: {err}") from err
    plan.src_signature = SourceSignature.of(src)
    if plan.policy.skip_hardlinks and src.st_nlink > 1:
        plan.skip = "hardlinked, left for the download client (SKIP_HARDLINKS)"
        return plan
    return plan_from_probe(plan, probe(path))


def plan_from_probe(plan: Plan, info: dict) -> Plan:
    """The rules themselves, split out so tests can feed synthetic probe data."""
    policy = plan.policy
    streams = info.get("streams") or []
    plan.src_duration = duration(info)

    video, audio, subs, attachments = _split_streams(plan, streams)
    if not video:
        plan.skip = "no video stream"
        return plan

    # out_path is derived from the path and the policy, neither of which
    # changes from here, so the subtitle codec below reads the same answer.
    converting = plan.out_path != plan.path
    if converting:
        plan.reasons.append("remux to mkv (REMUX_TO_MKV)")

    def keep_track(stream: dict, what: str) -> bool:
        if not policy.rule_enabled("languages"):
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
    if is_junk_title(file_title, policy):
        plan.incidental.append(f"clear junk container title ({file_title!r})")
        plan.clear_container_title = True

    if policy.rule_enabled("order"):
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
            if plan.policy.rule_enabled("cover_art") and is_cover_art(stream, plan.policy):
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
    if not is_junk_title(title, plan.policy) or title_is_load_bearing(stream, plan.policy):
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
    if not plan.policy.rule_enabled("sdh"):
        return kept_subs
    sdh: list[dict] = []
    full_langs: set[str | None] = set()
    for stream in kept_subs:
        if is_forced(stream, plan.policy):
            continue
        if is_sdh(stream, plan.policy):
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
    if not plan.policy.drop_commentary:
        return kept_audio
    keep: list[dict] = []
    dropped: list[dict] = []
    for stream in kept_audio:
        if is_commentary(stream, plan.policy):
            dropped.append(stream)
        else:
            keep.append(stream)
    if not keep:
        return kept_audio
    for stream in dropped:
        plan.reasons.append(f"drop commentary audio {_stream_label(stream)}")
    return keep


def _downmix_sources(
    streams: list[dict], channels: int, policy: Policy, exclude: dict | None = None
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
        and not is_commentary(stream, policy)
    ]


def _drop_stale_downmixes(plan: Plan, kept_audio: list[dict]) -> list[dict]:
    """Drop tracks the downmix rule should rebuild fresh from their source.

    The REGENERATE_DOWNMIXES opt-in; see :func:`_stale_reason` for what each
    mode drops. Nothing is dropped unless a bigger track to rebuild the
    layout from survives, so a file never loses a layout it had.
    """
    if not plan.policy.regenerate_downmixes or not plan.policy.rule_enabled("downmix"):
        return kept_audio
    if os.path.splitext(plan.path)[1].lower() not in TAG_PRESERVING_EXTS:
        return kept_audio
    # The same first-wins choice _choose_downmixes makes, so a track is
    # always judged against the layout it would be rebuilt with.
    layouts: dict[int | None, Layout] = {}
    for layout in plan.policy.downmix_layouts:
        layouts.setdefault(layout.channels, layout)
    keep: list[dict] = []
    for stream in kept_audio:
        rebuilt_as = layouts.get(stream.get("channels"))
        why = _stale_reason(plan, stream, rebuilt_as) if rebuilt_as else None
        if (
            rebuilt_as
            and why
            and _downmix_sources(kept_audio, rebuilt_as.channels, plan.policy, exclude=stream)
        ):
            plan.reasons.append(why)
        else:
            keep.append(stream)
    return keep


#: How far below its layout's rate a track must report before "all" replaces
#: it: not even half. Deliberately far from 1.0: encoders emit what the
#: content needs rather than the nominal request, and codecs differ in
#: efficiency, so a decent 448k AC3 5.1 must not read as weak against a
#: 640k AAC target.
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
    desired = encode_settings(plan.policy.audio_codec, layout.bitrate)
    if recorded is not None:
        if recorded == desired:
            return None
        return (
            f"regenerate {layout.name} downmix {_stream_label(stream, recorded)} as {desired}"
        )
    if plan.policy.regenerate_downmixes != "all" or is_commentary(stream, plan.policy):
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
    if not plan.policy.rule_enabled("downmix"):
        return []
    real = [stream for stream in kept_audio if not is_commentary(stream, plan.policy)]
    # Two layout names with the same channel count ("4.2" and "5.1") would
    # generate identical tracks, so a satisfied count also satisfies the rest.
    satisfied = {stream.get("channels") for stream in real}
    chosen: list[tuple[Layout, dict]] = []
    for layout in plan.policy.downmix_layouts:
        if layout.channels in satisfied:
            continue
        candidates = _downmix_sources(kept_audio, layout.channels, plan.policy)
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
                plan.policy.audio_codec,
                f"-ac:a:{idx}",
                str(out.channels),
                f"-b:a:{idx}",
                out.bitrate,
                f"-metadata:s:a:{idx}",
                f"title={out.title}",
                # Recorded so REGENERATE_DOWNMIXES can recognise this track
                # and its settings on a later pass.
                f"-metadata:s:a:{idx}",
                f"{GENERATED_TAG}={encode_settings(plan.policy.audio_codec, out.bitrate)}",
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

    # Rewrites stage under a .partial name so library scanners ignore the
    # half-written file, which leaves ffmpeg unable to infer the muxer from
    # the extension. Name it from the container the plan actually writes.
    args += ["-f", MUXERS[os.path.splitext(plan.out_path)[1].lower()]]
    args += ["-max_muxing_queue_size", "9999", dest]
    return args
