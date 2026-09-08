"""Decide what a file needs, without touching it.

Pure given ffprobe output, so the rules are testable without media;
:func:`build_plan` is the only thing that reads from disk. The rules are named
in :data:`trackstarr.policy.RULES`, each set to never, alongside another rule's
rewrite, or always. What an AUDIO_LAYOUTS or LANGUAGES row says has no mode and
always acts. Every rule is idempotent: apply, re-plan, get an empty plan.

:mod:`trackstarr.command` renders a plan into an ffmpeg command and
:mod:`trackstarr.executor` runs it.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from . import config
from .media import (
    ProbeError,
    container_title,
    duration,
    generated_settings,
    is_commentary,
    is_cover_art,
    is_forced,
    is_lossless,
    is_sdh,
    matches_release_tags,
    probe,
    stream_bitrate,
    stream_lang,
    stream_title,
    title_is_load_bearing,
    track_summary,
    unpreserved_bitrate,
)
from .policy import (
    ALONGSIDE,
    ALWAYS,
    DOWNMIX_RULE,
    DROP_LAYOUTS_RULE,
    TAG_PRESERVING_EXTS,
    VIDEO_EXTS,
    Policy,
)
from .status import Status
from .tracks import CODECS, Lang, Layout, bitrate_bps, encode_settings, settings_bitrate


def channel_rank(channels: int | None, order: Sequence[int] = ()) -> tuple[int, bool, int]:
    """Sort key for an audio track of this size.

    ``order`` is AUDIO_LAYOUTS' channel counts in written order; those come
    first, then the rest by ascending count, mono and unknown last.
    """
    if channels in order:
        return (order.index(channels), False, 0)
    return (len(order), not channels or channels < 2, channels or 0)


class SourceSignature(NamedTuple):
    """Size and mtime of a source file, compared by the staleness checks."""

    size: int
    mtime_ns: int

    @classmethod
    def of(cls, st: os.stat_result) -> SourceSignature:
        return cls(st.st_size, st.st_mtime_ns)


@dataclass
class OutStream:
    """One output stream and its input source.

    ``title`` is the layout name on encodes and the source's title on copies,
    re-asserted because MP4 drops track names on a plain copy. ``lang``,
    ``codec`` and ``bitrate`` are set only on generated tracks.
    """

    src: int  # stream index in the input file
    kind: str  # video | audio | subtitle | attachment
    encode: bool = False  # a downmix, or a track re-encoded from itself
    channels: int | None = None
    lang: str | None = None
    title: str = ""
    codec: str = ""  # resolved encoder, generated downmixes only
    bitrate: str = ""  # resolved encode rate, generated downmixes only
    clear_title: bool = False  # strip release tags from a title while rewriting anyway
    sub_codec: str | None = None  # convert a subtitle while remuxing (mov_text -> srt)
    #: A copied stream's rate when the output container would otherwise lose
    #: it; see :func:`trackstarr.media.unpreserved_bitrate`. None otherwise.
    src_bitrate: int | None = None


@dataclass
class Plan:
    path: str
    #: The policy the file was judged under, so the plan carries its own
    #: settings and the CLI can show them.
    policy: Policy = field(default_factory=Policy.from_config)
    streams: list[OutStream] = field(default_factory=list)
    #: The rules acting on this plan, and which of those only ride along.
    #: Filled by :func:`plan_from_probe`.
    acting: frozenset[str] = frozenset()
    alongside: frozenset[str] = frozenset()
    #: Changes that justify a rewrite on their own.
    reasons: list[str] = field(default_factory=list)
    #: Changes that ride along with a rewrite but never cause one: a 60GB
    #: remux costs far more than the stray track it would drop.
    incidental: list[str] = field(default_factory=list)
    #: The lists above as :data:`trackstarr.policy.RULE_NAMES`, since prose
    #: gets reworded. Written through :func:`_record`.
    rules: set[str] = field(default_factory=set)
    incidental_rules: set[str] = field(default_factory=set)
    original_lang: str | None = None
    #: policy.languages with ORIGINAL substituted for this title.
    langs: tuple[Lang, ...] = ()
    #: Every input stream as a :func:`trackstarr.media.track_summary`, so the
    #: sweep cache can double as a library index. Empty when never probed.
    tracks: list[dict] = field(default_factory=list)
    #: Source duration at plan time, checked against the rewrite result
    #: before anything is overwritten. Zero when the probe did not carry one.
    src_duration: float = 0.0
    #: Source size and mtime at plan time. A plan waiting on the rewrite lock
    #: goes stale, so apply_plan refuses unless this matches. None when
    #: hand-built.
    src_signature: SourceSignature | None = None
    #: Strip release tags from the container title while rewriting anyway.
    clear_container_title: bool = False
    #: Whether this rewrite converts the container. Decided per pass, since a
    #: ride-along remux rule does not act on the deciding pass.
    remuxing: bool = False
    #: Set when the file cannot or need not be touched at all.
    skip: str | None = None
    #: The skip's status: SKIP, or UNSUPPORTED for a container the rules never
    #: write. Means nothing without ``skip``.
    skip_status: Status = Status.SKIP

    @property
    def needed(self) -> bool:
        return not self.skip and bool(self.reasons)

    def acts(self, rule: str) -> bool:
        """Whether a rule is one of those running on this plan."""
        return rule in self.acting

    @property
    def out_path(self) -> str:
        """Where the rewrite lands: the source path, or its .mkv sibling when
        the remux rule converts the container."""
        base, _ = os.path.splitext(self.path)
        return base + ".mkv" if self.remuxing else self.path


def describe(plan: Plan) -> str:
    """What the plan does, or why it does nothing.

    A skipped plan leads with the skip, since its reasons are what the rules
    wanted rather than what happens; they still follow, because "would remove
    every audio track" needs the track named.
    """
    parts = list(plan.reasons)
    parts += [f"(also {item})" for item in plan.incidental]
    if plan.skip:
        return f"{plan.skip}: {'; '.join(parts)}" if parts else plan.skip
    return "; ".join(parts)


def planned_tracks(plan: Plan) -> list[dict]:
    """The file as the rewrite would leave it, in output order.

    Read beside :attr:`Plan.tracks`, this is the before-and-after a library
    view draws; every entry names its input stream. Copied streams inherit
    their source's codec, language and flags, minus a cleared title and with
    a remux's subtitle codec. Generated downmixes carry their layout's settings.
    """
    sources = {track.get("index"): track for track in plan.tracks}
    out: list[dict] = []
    for position, stream in enumerate(plan.streams):
        source = sources.get(stream.src) or {}
        if stream.encode:
            entry = {
                "codec": stream.codec,
                "channels": stream.channels,
                "lang": stream.lang,
                "title": stream.title,
                "bitrate": bitrate_bps(stream.bitrate),
                "flags": ["generated"],
            }
        else:
            entry = {
                "codec": stream.sub_codec or source.get("codec"),
                "channels": source.get("channels"),
                "lang": source.get("lang"),
                "title": "" if stream.clear_title else source.get("title", ""),
                "bitrate": source.get("bitrate"),
                "flags": source.get("flags") or [],
            }
        entry |= {"index": position, "src": stream.src, "kind": stream.kind}
        out.append(
            {name: value for name, value in entry.items() if value not in (None, "", [])}
        )
    return out


def track_changes(plan: Plan) -> dict:
    """Which of the file's streams the rewrite drops and which of the output's
    it generates, by index. Empty lists are dropped.

    What a before-and-after view needs beyond the two track lists, at a fraction
    of the size of :func:`planned_tracks`: a rewrite's output is probed again
    anyway, so only the source list and this are worth keeping. ``dropped``
    holds input indices and ``added`` output positions, as ``src`` and ``index``
    do there.
    """
    carried = {stream.src for stream in plan.streams if not stream.encode}
    told = {
        "dropped": [
            track["index"] for track in plan.tracks if track.get("index") not in carried
        ],
        "added": [position for position, stream in enumerate(plan.streams) if stream.encode],
    }
    return {name: value for name, value in told.items() if value}


def why(plan: Plan) -> dict:
    """The plan's conclusion in the history's vocabulary: the four lists a
    "modified" event records, plus the skip. :func:`describe` is the prose
    form."""
    told = {
        "skip": plan.skip or "",
        "reasons": plan.reasons,
        "incidental": plan.incidental,
        "rules": sorted(plan.rules),
        "incidental_rules": sorted(plan.incidental_rules),
    }
    return {name: value for name, value in told.items() if value}


def _record(plan: Plan, rule: str, reason: str) -> None:
    """Record a change under its rule: ``incidental`` for a ride-along,
    ``reasons`` otherwise. ``rule`` is from :data:`trackstarr.policy.RULE_NAMES`."""
    if rule in plan.alongside:
        plan.incidental.append(reason)
        plan.incidental_rules.add(rule)
    else:
        plan.reasons.append(reason)
        plan.rules.add(rule)


def new_plan(path: str, original_lang: str | None) -> Plan:
    """A Plan carrying the policy resolved from config at build time."""
    policy = Policy.from_config()
    return Plan(
        path=path,
        policy=policy,
        original_lang=original_lang,
        langs=policy.resolve(original_lang),
    )


def build_plan(path: str, original_lang: str | None) -> Plan:
    plan = new_plan(path, original_lang)
    if not plan.policy.allowed_container(path):
        ext = os.path.splitext(path)[1].lower()
        plan.skip = f"container {ext or '(none)'} not in ALLOWED_EXTS"
        # Only a video file is "unsupported"; a .txt named by hand is skipped.
        if ext in VIDEO_EXTS:
            plan.skip_status = Status.UNSUPPORTED
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
    """The verdict for one probed file. Split out so tests can feed probe data.

    The rules run twice. First with only the rules that may order a rewrite,
    which decides whether the file is rewritten; then with the ride-alongs too,
    which is what the rewrite does. One pass could not do both: a ride-along
    changes what the other rules see, so dropping a track under
    ``languages=alongside`` would have the downmix rule order a rewrite to
    rebuild that layout.
    """
    always = plan.policy.rules_in(ALWAYS)
    alongside = plan.policy.rules_in(ALONGSIDE)
    deciding = _apply_rules(_for_pass(plan, always, frozenset()), info)
    if not alongside:
        return deciding
    whole = _apply_rules(_for_pass(plan, always | alongside, alongside), info)
    if deciding.reasons:
        return whole
    # No rewrite, so no ride-alongs happen; what they would have done is still
    # reported, so a title carrying release tags reads as that rather than as conforming.
    deciding.incidental = whole.incidental
    deciding.incidental_rules = whole.incidental_rules
    return deciding


def _for_pass(plan: Plan, acting: frozenset[str], alongside: frozenset[str]) -> Plan:
    """A fresh Plan for one pass of the rules, carrying nothing the rules
    write, so the passes cannot see each other's answers."""
    return Plan(
        path=plan.path,
        policy=plan.policy,
        original_lang=plan.original_lang,
        langs=plan.langs,
        src_signature=plan.src_signature,
        acting=acting,
        alongside=alongside,
    )


def _apply_rules(plan: Plan, info: dict) -> Plan:
    """One pass of the rules over a probe, judging only what ``plan.acting``
    names."""
    policy = plan.policy
    streams = info.get("streams") or []
    plan.src_duration = duration(info)
    plan.tracks = [track_summary(stream, policy) for stream in streams]

    video, audio, subs, tail = _split_streams(plan, streams)
    if not video:
        plan.skip = "no video stream"
        return plan

    ext = os.path.splitext(plan.path)[1].lower()
    converting = plan.acts("remux") and ext not in TAG_PRESERVING_EXTS
    if converting:
        plan.remuxing = True
        _record(plan, "remux", f"remux to mkv ({config.rule_variable('remux')})")

    listed = {lang.name for lang in plan.langs}

    def keep_track(stream: dict, what: str) -> bool:
        # Untagged always stays: a file of them is a file with nothing to judge.
        if (lang := stream_lang(stream)) is None:
            return True
        if lang in listed or not plan.acts("languages"):
            return True
        _record(plan, "languages", f"drop {what} {_stream_label(stream, lang)}")
        return False

    kept_audio = [stream for stream in audio if keep_track(stream, "audio")]
    kept_subs = [stream for stream in subs if keep_track(stream, "subtitle")]

    if not kept_audio:
        # Never leave a file silent.
        plan.skip = "would remove every audio track" if audio else "no audio streams"
        return plan

    kept_audio = _drop_commentary(plan, kept_audio)
    # A dropped track is owed its language back, whatever the downmix settings
    # say; see _choose_downmixes.
    kept_audio, rebuild_langs, replacing = _drop_stale_downmixes(plan, kept_audio)
    # Kept above, since a track re-encoded from itself is its own source: only
    # its copy in the output goes.
    reencodes = _reencode_oversized(plan, kept_audio)
    kept_subs = _drop_redundant_sdh(plan, kept_subs)

    downmixes = _choose_downmixes(plan, kept_audio, rebuild_langs)
    for layout, src in downmixes:
        made = _downmix_source(src)
        # A track dropped for this one to replace is one change, not two.
        if (replaced := _claim_replacement(replacing, layout, src)) is not None:
            _record(plan, "regenerate", f"{replaced} {made}")
        else:
            _record(plan, DOWNMIX_RULE, f"add {layout.name} downmix {made}")
    # Drops nothing came back for: the rebuild found no source in that
    # language and no fallback.
    for orphaned in replacing:
        _record(plan, "regenerate", orphaned.reason)
    # Every track the rewrite encodes: a fresh downmix, or one made from itself.
    generated = [*downmixes, *reencodes]
    # Last of the audio rules: the others' output replaces what it drops, and no
    # title is cleared on a track about to go.
    kept_audio = _drop_layouts(plan, kept_audio, generated)

    reencoded = {src["index"] for _, src in reencodes}
    audio_out = [
        OutStream(
            src=stream["index"],
            kind="audio",
            channels=stream.get("channels"),
            title=stream_title(stream),
            clear_title=_flag_release_tags(plan, stream),
            src_bitrate=unpreserved_bitrate(stream),
        )
        for stream in kept_audio
        if stream["index"] not in reencoded
    ]
    audio_out += [
        OutStream(
            src=src["index"],
            kind="audio",
            encode=True,
            channels=layout.channels,
            lang=stream_lang(src),
            title=layout.name,
            codec=layout.codec,
            bitrate=layout.bitrate,
        )
        for layout, src in generated
    ]
    # A generated track sorts ahead of an existing track of equal rank. Every
    # named layout, whatever happens to it: that is what Keep is for.
    layout_order = [layout.channels for layout in plan.policy.audio_layouts]
    audio_out.sort(
        key=lambda out: (channel_rank(out.channels, layout_order), not out.encode, out.src)
    )

    ordered = [
        OutStream(src=stream["index"], kind="video", src_bitrate=unpreserved_bitrate(stream))
        for stream in video
    ]
    ordered += audio_out
    for stream in kept_subs:
        # Matroska has no mov_text; the text converts losslessly to SRT.
        sub_codec = "srt" if converting and stream.get("codec_name") == "mov_text" else None
        ordered.append(
            OutStream(
                src=stream["index"],
                kind="subtitle",
                title=stream_title(stream),
                clear_title=_flag_release_tags(plan, stream),
                # Not carried across a conversion: the rate describes the
                # mov_text bytes, and the SRT will be a different size.
                src_bitrate=None if sub_codec else unpreserved_bitrate(stream),
                sub_codec=sub_codec,
            )
        )
    ordered += [
        OutStream(src=stream["index"], kind=stream.get("codec_type") or "data")
        for stream in tail
    ]

    file_title = container_title(info)
    if plan.acts("release_tags") and matches_release_tags(file_title, policy):
        _record(plan, "release_tags", f"clear release tags on container title ({file_title!r})")
        plan.clear_container_title = True

    if plan.acts("order"):
        # Copied streams only: a drop is not a reorder, and a generated downmix
        # is an insertion.
        copied = [out.src for out in ordered if not out.encode]
        kept_src = set(copied)
        current = [stream["index"] for stream in streams if stream["index"] in kept_src]
        if copied != current:
            _record(plan, "order", "reorder streams")
    else:
        # Preserve input order; a generated downmix follows its source.
        ordered.sort(key=lambda out: (out.src, out.encode))
    plan.streams = ordered

    return plan


def _split_streams(
    plan: Plan, streams: list[dict]
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Bucket the input by kind, recording cover-art and stray-stream drops.

    The fourth bucket follows the subtitles: attachments, plus strays when the
    rule dropping them is not acting.
    """
    video: list[dict] = []
    audio: list[dict] = []
    subs: list[dict] = []
    tail: list[dict] = []
    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video":
            if plan.acts("cover_art") and is_cover_art(stream):
                _record(
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
            # Fonts for ASS/SSA subtitles; dropping one breaks styled rendering.
            tail.append(stream)
        elif plan.acts("stray_streams"):
            _record(plan, "stray_streams", f"drop {kind} stream {stream['index']}")
        else:
            tail.append(stream)
    return video, audio, subs, tail


def _stream_label(stream: dict, detail: str = "") -> str:
    """``index (detail, title)``, with empty parts and empty parens omitted."""
    parts = [detail] if detail else []
    if title := stream_title(stream):
        parts.append(title)
    return f"{stream['index']} ({', '.join(parts)})" if parts else str(stream["index"])


def _flag_release_tags(plan: Plan, stream: dict) -> bool:
    """Record a title carrying release tags for clearing, and say whether to clear it."""
    if not plan.acts("release_tags"):
        return False
    title = stream_title(stream)
    if not matches_release_tags(title, plan.policy) or title_is_load_bearing(
        stream, plan.policy
    ):
        return False
    _record(
        plan,
        "release_tags",
        f"clear release tags on {stream.get('codec_type')} {stream['index']} ({title!r})",
    )
    return True


def _drop_redundant_sdh(plan: Plan, kept_subs: list[dict]) -> list[dict]:
    """Drop SDH subtitles whose language also keeps a full subtitle.

    Rides along by default, since a text subtitle costs less than the rewrite.
    Forced subtitles are always kept and never make an SDH track redundant.
    """
    if not plan.acts("sdh"):
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
            _record(plan, "sdh", f"drop SDH subtitle {_stream_label(stream)}")
            redundant.add(stream["index"])
    return [stream for stream in kept_subs if stream["index"] not in redundant]


def _drop_commentary(plan: Plan, kept_audio: list[dict]) -> list[dict]:
    """Drop commentary tracks, unless every surviving track is one."""
    if not plan.acts("commentary"):
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
        _record(plan, "commentary", f"drop commentary audio {_stream_label(stream)}")
    return keep


def _downmix_sources(
    streams: list[dict], channels: int, policy: Policy, exclude: dict | None = None
) -> list[dict]:
    """Non-commentary tracks bigger than ``channels``: the pool a downmix comes
    from. Shared by the drop and rebuild sides, so a track is never dropped
    unless the rebuild would find a source."""
    return [
        stream
        for stream in streams
        if stream is not exclude
        and (stream.get("channels") or 0) > channels
        and not is_commentary(stream, policy)
    ]


class Replacement(NamedTuple):
    """A dropped track waiting on its replacement.

    ``reason`` is the first half of a sentence finished once the rebuild names
    its source. ``lang`` is None for an untagged track.
    """

    channels: int | None
    lang: str | None
    reason: str


def _downmix_source(src: dict) -> str:
    """How a generated downmix names the track it was made from."""
    return f"from stream {src['index']} ({src.get('channels')}ch {stream_lang(src) or 'und'})"


def _claim_replacement(replacing: list[Replacement], layout: Layout, src: dict) -> str | None:
    """Pop and return the drop this generated track answers, or None.

    By language first: a German 2.0 dropped for a French one is two changes.
    An untagged drop is claimed by any match in its layout.
    """
    for lang in (stream_lang(src), None):
        for at, entry in enumerate(replacing):
            if entry.channels == layout.channels and entry.lang == lang:
                return replacing.pop(at).reason
    return None


def _rebuild_targets(plan: Plan) -> dict[int | None, Layout]:
    """The layout each channel count is judged against, empty where the
    regenerate rule cannot act at all.

    Added layouts only: a rebuild is a fresh downmix, so a size nothing makes
    has nothing to rebuild. First wins, as in :func:`_choose_downmixes`. The
    rule knows its own tracks by a tag only some containers keep, so elsewhere
    it would re-encode them every sweep.
    """
    if not plan.acts("regenerate") or not plan.policy.downmixed():
        return {}
    if os.path.splitext(plan.path)[1].lower() not in TAG_PRESERVING_EXTS:
        return {}
    targets: dict[int | None, Layout] = {}
    for layout in plan.policy.downmixed():
        targets.setdefault(layout.channels, layout)
    return targets


def _drop_stale_downmixes(
    plan: Plan, kept_audio: list[dict]
) -> tuple[list[dict], set[str], list[Replacement]]:
    """The regenerate rule: drop tracks the downmix rule should rebuild.

    :func:`_stale_reason` says what each REGENERATE_SCOPE drops. Nothing goes
    unless a bigger track survives to rebuild from; :func:`_reencode_oversized`
    is what reaches a fat track with nothing above it. Returns the kept tracks,
    the dropped languages (each owed a rebuild) and the pending drop reasons.
    """
    layouts = _rebuild_targets(plan)
    if not layouts:
        return kept_audio, set(), []
    keep: list[dict] = []
    rebuild_langs: set[str] = set()
    replacing: list[Replacement] = []
    for stream in kept_audio:
        rebuilt_as = layouts.get(stream.get("channels"))
        # Nothing goes unless a bigger track survives to rebuild from.
        sources = (
            _downmix_sources(kept_audio, rebuilt_as.channels, plan.policy, exclude=stream)
            if rebuilt_as
            else []
        )
        why = None
        if rebuilt_as and sources:
            why = _stale_reason(plan, stream, rebuilt_as, sources)
        if why:
            # Held, not recorded: the reason is half a sentence until the
            # replacement names its source.
            lang = stream_lang(stream)
            replacing.append(Replacement(stream.get("channels"), lang, why))
            if lang:
                rebuild_langs.add(lang)
        else:
            keep.append(stream)
    return keep, rebuild_langs, replacing


def _has_more_to_give(sources: list[dict], target: int) -> bool:
    """Whether a downmix from one of these could beat a track at ``target``.

    A downmix holds no more than its source. An unreported rate counts as able,
    since Matroska routinely reports none.
    """
    return any((rate := stream_bitrate(source)) is None or rate >= target for source in sources)


def _stale_reason(plan: Plan, stream: dict, layout: Layout, sources: list[dict]) -> str | None:
    """Why a layout-sized track should be rebuilt, or None to keep it.

    A track carrying GENERATED_TAG is rebuilt when its recorded settings no
    longer match. Under REGENERATE_SCOPE=all, so is a real track under
    REGENERATE_BELOW_PERCENT of the layout's rate when a source has more to
    give. Unknown bitrates and commentary are left alone.
    """
    recorded = generated_settings(stream)
    desired = encode_settings(layout.codec, layout.bitrate)
    if recorded is not None:
        if recorded == desired:
            return None
        return (
            f"regenerate {layout.name} downmix {_stream_label(stream, recorded)} as {desired}"
        )
    if plan.policy.regenerate_scope != "all" or is_commentary(stream, plan.policy):
        return None
    reported = stream_bitrate(stream)
    target = bitrate_bps(layout.bitrate) or 0
    if reported is None or reported * 100 >= target * plan.policy.regenerate_below:
        return None
    if not _has_more_to_give(sources, target):
        return None
    return (
        f"replace low-bitrate {layout.name} track "
        f"{_stream_label(stream, f'{reported // 1000}k')} with a fresh downmix"
    )


def _reencode_oversized(plan: Plan, kept_audio: list[dict]) -> list[tuple[Layout, dict]]:
    """The regenerate rule's other half: tracks made from themselves at their
    layout's settings, paired with the layout, or empty with
    REGENERATE_ABOVE_PERCENT unset.

    Reads what :func:`_drop_stale_downmixes` kept, so a track it drops is
    rebuilt from the bigger source rather than made from itself: a fold-down of
    the original beats a second pass over a track that has already been through
    an encoder. A track it does not drop is re-encoded wherever it is over the
    line, bigger tracks beside it or not.
    """
    if not plan.policy.regenerate_above:
        return []
    layouts = _rebuild_targets(plan)
    reencodes: list[tuple[Layout, dict]] = []
    for stream in kept_audio:
        layout = layouts.get(stream.get("channels"))
        if layout is None:
            continue
        if (why := _oversized_reason(plan, stream, layout)) is not None:
            _record(plan, "regenerate", why)
            reencodes.append((layout, stream))
    return reencodes


def _oversized_reason(plan: Plan, stream: dict, layout: Layout) -> str | None:
    """Why a track is re-encoded from itself, or None to leave it.

    REGENERATE_ABOVE_PERCENT of the layout's rate is the line, so a track at
    its rate is never touched and the pass is idempotent. A generated track is
    judged by the rate its tag records, since Matroska reports none for one; a
    real track by what it reports, and only under REGENERATE_SCOPE=all, as on
    the low-bitrate side. A lossless layout is left alone, since re-encoding
    into one grows the track this is here to shrink, and so is a lossless
    track, whatever it reports.
    """
    target = bitrate_bps(layout.bitrate) or 0
    encoder = CODECS.get(layout.codec)
    if not target or (encoder and encoder.lossless):
        return None
    # A commentary is not the layout's track, and a master is over every line
    # there is: a 4Mb/s DTS-HD MA 5.1 would come out as the layout's 640k, which
    # is the one loss a re-rip cannot undo. Removing a layout is where that is
    # asked for, in writing.
    if is_commentary(stream, plan.policy) or is_lossless(stream):
        return None
    if (recorded := generated_settings(stream)) is not None:
        rate = settings_bitrate(recorded)
        named = f"{layout.name} downmix"
    elif plan.policy.regenerate_scope == "all":
        rate = stream_bitrate(stream)
        named = f"{layout.name} track"
    else:
        return None
    if rate is None or rate * 100 < target * plan.policy.regenerate_above:
        return None
    return (
        f"re-encode high-bitrate {named} {_stream_label(stream, f'{rate // 1000}k')} "
        f"from itself as {encode_settings(layout.codec, layout.bitrate)}"
    )


def _wanted_langs(plan: Plan, rebuild_langs: set[str]) -> list[str]:
    """The languages every layout is guaranteed in, in LANGUAGES order. A
    language owed a rebuild is wanted whatever its row says."""
    wanted = [lang.name for lang in plan.langs if lang.downmixes]
    return wanted + sorted(rebuild_langs - set(wanted))


def _choose_downmixes(
    plan: Plan, kept_audio: list[dict], rebuild_langs: set[str] | None = None
) -> list[tuple[Layout, dict]]:
    """The ``(layout, source)`` pairs the file is missing.

    Every layout is guaranteed in every :func:`_wanted_langs` language, from
    the best bigger track of that language. A layout no wanted language could
    fill falls back to the best source of any language. Nothing is upmixed.
    """
    if not plan.policy.downmixed():
        return []
    real = [stream for stream in kept_audio if not is_commentary(stream, plan.policy)]
    wanted = _wanted_langs(plan, rebuild_langs or set())
    order = [lang.name for lang in plan.langs]
    # Two layouts of one channel count would generate identical tracks, so a
    # satisfied count satisfies both. Per language: a German 2.0 does not
    # answer for a French one.
    present = {(stream.get("channels"), stream_lang(stream)) for stream in real}
    sizes = {channels for channels, _ in present}
    chosen: list[tuple[Layout, dict]] = []
    for layout in plan.policy.downmixed():
        covered = False
        for lang in wanted:
            if (layout.channels, lang) in present:
                covered = True
                continue
            candidates = [
                stream
                for stream in _downmix_sources(kept_audio, layout.channels, plan.policy)
                if stream_lang(stream) == lang
            ]
            if not candidates:
                continue
            # One language, so the rank reduces to channel count.
            src = min(candidates, key=lambda stream: _downmix_rank(stream, order))
            chosen.append((layout, src))
            present.add((layout.channels, lang))
            sizes.add(layout.channels)
            covered = True
        if covered or layout.channels in sizes:
            continue
        candidates = _downmix_sources(kept_audio, layout.channels, plan.policy)
        if not candidates:
            continue
        src = min(candidates, key=lambda stream: _downmix_rank(stream, order))
        chosen.append((layout, src))
        present.add((layout.channels, stream_lang(src)))
        sizes.add(layout.channels)
    return chosen


def _downmix_rank(stream: dict, order: list[str]) -> tuple[int, int]:
    """Prefer languages in LANGUAGES order, unlisted last; within a language,
    the most channels."""
    lang = stream_lang(stream)
    pref = order.index(lang) if lang in order else len(order)
    return (pref, -(stream.get("channels") or 0))


def _drop_layouts(
    plan: Plan, kept_audio: list[dict], generated: list[tuple[Layout, dict]]
) -> list[dict]:
    """Remove every track at a size AUDIO_LAYOUTS sets to remove.

    No rule mode: the row is the switch, and removing a mix is worth its own
    rewrite. Runs after the downmix rule and takes what it will generate, so a
    track on its way out is still the source for the ones replacing it.

    Nothing goes if it would take the last audio track with it. A generated
    track counts, which is what lets a 7.1-only file lose its 7.1.
    """
    # Widened for the lookup: a broken stream reports no channels.
    named: dict[int | None, str] = {
        layout.channels: layout.name for layout in plan.policy.removed()
    }
    if not named:
        return kept_audio
    keep = [stream for stream in kept_audio if stream.get("channels") not in named]
    # The plan says nothing either: reasons are what a rewrite does.
    if not keep and not generated:
        return kept_audio
    for stream in kept_audio:
        if (layout := named.get(stream.get("channels"))) is not None:
            _record(
                plan,
                DROP_LAYOUTS_RULE,
                f"drop {layout} audio {_stream_label(stream, stream_lang(stream) or 'und')}",
            )
    return keep
