"""Decide what a file needs, without touching it.

Pure given ffprobe output, which is what lets the rules be tested without
media. :func:`build_plan` is the only thing here that reads from disk.

The rules are named in :data:`trackstarr.policy.RULES` and every one is
idempotent: apply the result, re-plan, get an empty plan. That is what makes
the sweep safe to run as often as you like.

Deciding only. :mod:`trackstarr.command` renders a plan into an ffmpeg
command and :mod:`trackstarr.executor` runs it.
"""

import os
from dataclasses import dataclass, field
from typing import NamedTuple

from .layouts import Layout, bitrate_bps, encode_settings
from .media import (
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
    track_summary,
)
from .policy import TAG_PRESERVING_EXTS, Policy


def channel_rank(channels: int | None) -> tuple[bool, int]:
    """Ascending channel count, mono and unknown last.

    Disposition-blind players fall back to the first audio track, so it must
    never be mono while anything better exists.
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
    """One output stream, and where it comes from in the input.

    ``title`` is the layout name on encode streams and the source's own title
    on copied ones, re-asserted because MP4 drops track names on a plain
    copy. ``lang`` and ``bitrate`` are set only on generated downmixes.
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
    #: The policy the file was judged under, so the plan carries its own
    #: settings and the CLI can show them.
    policy: Policy = field(default_factory=Policy.from_config)
    streams: list[OutStream] = field(default_factory=list)
    #: Changes that justify a rewrite on their own.
    reasons: list[str] = field(default_factory=list)
    #: Changes that ride along with a rewrite but never cause one: a 60GB
    #: remux costs far more than the stray track it would drop.
    incidental: list[str] = field(default_factory=list)
    #: The lists above in fixed :data:`trackstarr.policy.RULE_NAMES`, since
    #: prose gets reworded and a stats view needs stable names. Written through
    #: :func:`_because` and :func:`_alongside`, so a reason never arrives unnamed.
    rules: set[str] = field(default_factory=set)
    incidental_rules: set[str] = field(default_factory=set)
    original_lang: str | None = None
    keep_langs: set[str] = field(default_factory=set)
    #: Every input stream as a :func:`trackstarr.media.track_summary`, drops
    #: included, so the sweep cache can double as a library index. Empty when
    #: the file was never probed.
    tracks: list[dict] = field(default_factory=list)
    #: Source duration at plan time, checked against the rewrite result
    #: before anything is overwritten. Zero when the probe did not carry one.
    src_duration: float = 0.0
    #: Source size and mtime at plan time. A plan goes stale waiting on the
    #: rewrite lock, and a stale one maps streams by indices the file no longer
    #: has, so apply_plan refuses unless this matches. None for a hand-built plan.
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


def _because(plan: Plan, rule: str, reason: str) -> None:
    """Record a change worth a rewrite on its own, and the rule behind it.
    ``rule`` comes from :data:`trackstarr.policy.RULE_NAMES`."""
    plan.reasons.append(reason)
    plan.rules.add(rule)


def _alongside(plan: Plan, rule: str, reason: str) -> None:
    """Record a change that rides along with a rewrite but never causes one,
    and the rule behind it."""
    plan.incidental.append(reason)
    plan.incidental_rules.add(rule)


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
    plan.tracks = [track_summary(stream, policy) for stream in streams]

    video, audio, subs, attachments = _split_streams(plan, streams)
    if not video:
        plan.skip = "no video stream"
        return plan

    # Neither the path nor the policy changes from here, so the subtitle
    # codec below reads the same answer.
    converting = plan.out_path != plan.path
    if converting:
        _because(plan, "remux", "remux to mkv (REMUX_TO_MKV)")

    def keep_track(stream: dict, what: str) -> bool:
        if not policy.rule_enabled("languages"):
            return True
        lang = stream_lang(stream)
        if lang is None or lang in plan.keep_langs:
            return True
        _because(plan, "languages", f"drop {what} {_stream_label(stream, lang)}")
        return False

    kept_audio = [stream for stream in audio if keep_track(stream, "audio")]
    kept_subs = [stream for stream in subs if keep_track(stream, "subtitle")]

    if not kept_audio:
        # Never leave a file silent, whether every track failed the language
        # test or there were none to begin with.
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
        _because(
            plan,
            "downmix",
            f"add {layout.name} downmix from stream {src['index']} "
            f"({src.get('channels')}ch {stream_lang(src) or 'und'})",
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
        _alongside(plan, "junk_titles", f"clear junk container title ({file_title!r})")
        plan.clear_container_title = True

    if policy.rule_enabled("order"):
        # Judged against the copied streams only: every input stream would
        # make any drop look like a reorder, and a generated downmix is an
        # insertion, not a move.
        copied = [out.src for out in ordered if not out.encode]
        kept_src = set(copied)
        current = [stream["index"] for stream in streams if stream["index"] in kept_src]
        if copied != current:
            if plan.reasons:
                # The streams come out reordered either way; recorded so the
                # history doesn't undercount what the rewrite did.
                _alongside(plan, "order", "reorder streams")
            else:
                # Worth a rewrite alone.
                _because(plan, "order", "reorder streams")
    else:
        # A rewrite the other rules trigger still has to preserve the input
        # order; a generated downmix rides after its source track.
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
            if plan.policy.rule_enabled("cover_art") and is_cover_art(stream):
                _because(
                    plan,
                    "cover_art",
                    f"drop cover art (stream {stream['index']}, {stream.get('codec_name')})",
                )
            else:
                video.append(stream)
        elif kind == "audio":
            audio.append(stream)
        elif kind == "subtitle":
            subs.append(stream)
        elif kind == "attachment":
            # Fonts for ASS/SSA subtitles. Dropping one silently breaks
            # styled rendering, so they always come along.
            attachments.append(stream)
        else:
            _alongside(plan, "stray_streams", f"drop {kind} stream {stream['index']}")
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
    _alongside(
        plan,
        "junk_titles",
        f"clear junk title on {stream.get('codec_type')} {stream['index']} ({title!r})",
    )
    return True


def _drop_redundant_sdh(plan: Plan, kept_subs: list[dict]) -> list[dict]:
    """Drop SDH subtitles whose language also keeps a full subtitle.

    Rides along, never triggers: a text subtitle costs far less than the
    rewrite removing it would. Forced subtitles count neither way, always
    kept and never enough to make an SDH track redundant.
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
            _alongside(plan, "sdh", f"drop SDH subtitle {_stream_label(stream)}")
            redundant.add(stream["index"])
    return [stream for stream in kept_subs if stream["index"] not in redundant]


def _drop_commentary(plan: Plan, kept_audio: list[dict]) -> list[dict]:
    """Drop commentary tracks when DROP_COMMENTARY asks for it.

    Declines entirely when every surviving track is commentary. The other
    rules still apply; the file is never left silent.
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
        _because(plan, "commentary", f"drop commentary audio {_stream_label(stream)}")
    return keep


def _downmix_sources(
    streams: list[dict], channels: int, policy: Policy, exclude: dict | None = None
) -> list[dict]:
    """Non-commentary tracks bigger than ``channels``, the pool a downmix
    for that layout comes from.

    One definition for both the drop side and the rebuild side, so a track is
    never dropped unless the rebuild would find a source.
    """
    return [
        stream
        for stream in streams
        if stream is not exclude
        and (stream.get("channels") or 0) > channels
        and not is_commentary(stream, policy)
    ]


def _drop_stale_downmixes(plan: Plan, kept_audio: list[dict]) -> list[dict]:
    """Drop tracks the downmix rule should rebuild from their source.

    The REGENERATE_DOWNMIXES opt-in; :func:`_stale_reason` says what each
    mode drops. Nothing goes unless a bigger track survives to rebuild from,
    so a file never loses a layout it had.
    """
    if not plan.policy.regenerate_downmixes or not plan.policy.rule_enabled("downmix"):
        return kept_audio
    if os.path.splitext(plan.path)[1].lower() not in TAG_PRESERVING_EXTS:
        return kept_audio
    # The same first-wins choice _choose_downmixes makes, so a track is
    # judged against the layout it would be rebuilt as.
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
            _because(plan, "regenerate", why)
        else:
            keep.append(stream)
    return keep


#: How far below its layout's rate a track must report before "all" replaces
#: it. Far from 1.0 on purpose: codecs differ and encoders emit what the
#: content needs, so a decent 448k AC3 5.1 must not read as weak at 640k.
_WEAK_BITRATE_RATIO = 0.5


def _stale_reason(plan: Plan, stream: dict, layout: Layout) -> str | None:
    """Why a layout-sized track should be rebuilt, or None to keep it.

    A track carrying GENERATED_TAG is ours, and gets rebuilt when its
    recorded settings no longer match config. Under "all", so does any real
    track reported below _WEAK_BITRATE_RATIO of the layout's rate. Unknown
    bitrates and commentary are left alone, and nothing is ever re-encoded in
    place, only replaced by a fresh downmix from a bigger track.
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

    Missing means no real, non-commentary track has that channel count. Each
    comes from the best surviving bigger track; with nothing bigger the
    layout is skipped, never upmixed.
    """
    if not plan.policy.rule_enabled("downmix"):
        return []
    real = [stream for stream in kept_audio if not is_commentary(stream, plan.policy)]
    # Two layout names of the same channel count would generate identical
    # tracks, so a satisfied count satisfies the rest.
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
