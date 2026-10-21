"""Which audio a target lacks, which variant should supply it, and how far it
has got. Pure: :mod:`trackstarr.audio_sources` gathers the facts."""

import hashlib
import json
from dataclasses import dataclass, field

from .inventory import Inventory, Variant
from .media import IMPORTED_TAG, is_commentary, stream_lang, tag_value
from .planner import FileRevision, Plan, PlanInput, TimingMapping
from .policy import ALONGSIDE, ALWAYS, Policy

#: A retained track in the target already has the language.
SATISFIED = "satisfied"
#: A complete inventory holds no eligible source.
MISSING_SOURCE = "missing_source"
#: A connection or file that could hold one cannot be read right now.
UNAVAILABLE = "unavailable"
#: The preferred source needs its timing measured first.
ANALYSIS_REQUIRED = "analysis_required"
#: A source and a mapping are settled, so a rewrite can import it.
READY = "ready"
#: Identity, timing or the rules stand in the way until a person decides.
NEEDS_REVIEW = "needs_review"
#: Measuring or preparing failed, which says nothing about the content.
FAILED = "failed"

#: The words a target's whole assessment comes to. A file the rule does not
#: ask about carries no assessment at all.
COMPLETE = "complete"
INCOMPLETE = "incomplete"
#: Something could not be read, so the answer may still be complete.
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AudioRequirement:
    """One LANGUAGES language as main audio. Satisfied describes the local
    plan's output, not a completed rewrite."""

    lang: str
    satisfied: bool


def active(policy: Policy) -> bool:
    """Whether the rule asks for anything on this policy."""
    return "source_audio" in policy.rules_in(ALWAYS, ALONGSIDE)


def requirements(plan: Plan) -> tuple[AudioRequirement, ...]:
    """Assess retained main audio before selecting any external tracks.

    Alongside requirements remain visible without a rewrite. This check never
    adds rewrite reasons or authorises copying. The caller supplies a probed,
    local-only plan so imported audio cannot satisfy its own prerequisite.
    """
    if not active(plan.policy):
        return ()
    if plan.inputs:
        raise ValueError("audio requirements need a local-only plan")
    tracks = {track["index"]: track for track in plan.tracks}
    present = set()
    for stream in plan.streams:
        if stream.kind != "audio":
            continue
        track = tracks.get(stream.source.stream_index, {})
        if "commentary" in track.get("flags", ()):
            continue
        if language := stream.lang or track.get("lang"):
            present.add(language)
    return tuple(AudioRequirement(lang.name, lang.name in present) for lang in plan.langs)


@dataclass(frozen=True)
class TrackRef:
    """One audio stream of a source file as it was observed."""

    variant: Variant
    revision: FileRevision
    stream: dict

    @property
    def index(self) -> int:
        return self.stream["index"]


@dataclass(frozen=True)
class Resolution:
    """Where one requirement stands. ``track`` and ``mapping`` name the import
    once ready, and ``track`` alone the source still to be measured."""

    lang: str
    state: str
    reason: str = ""
    track: TrackRef | None = None
    mapping: TimingMapping | None = None
    #: Whether the mapping came from measurement rather than matching timelines.
    measured: bool = False


@dataclass(frozen=True)
class SourcingAssessment:
    """Every requirement of one target, in LANGUAGES order."""

    resolutions: tuple[Resolution, ...]
    #: :func:`digest` of what the assessment was judged against.
    inventory: str = ""
    #: Measurements of this target, as :func:`trackstarr.alignment.record` stores them.
    alignments: tuple[dict, ...] = field(default=())

    @property
    def completeness(self) -> str:
        states = {resolution.state for resolution in self.resolutions}
        if states <= {SATISFIED}:
            return COMPLETE
        if UNAVAILABLE in states:
            return UNKNOWN
        return INCOMPLETE

    @property
    def inputs(self) -> tuple[PlanInput, ...]:
        """The ready imports, in requirement order."""
        return tuple(
            PlanInput(resolution.track.revision, resolution.track.index, resolution.mapping)
            for resolution in self.resolutions
            if resolution.state == READY
            and resolution.track is not None
            and resolution.mapping is not None
        )

    def project(self) -> dict:
        """The assessment as a verdict stores it."""
        told: dict = {
            "completeness": self.completeness,
            "requirements": [_resolution(resolution) for resolution in self.resolutions],
        }
        if self.inventory:
            told["inventory"] = self.inventory
        if self.alignments:
            told["alignments"] = list(self.alignments)
        return told


def _resolution(resolution: Resolution) -> dict:
    told: dict = {"lang": resolution.lang, "state": resolution.state}
    if resolution.reason:
        told["reason"] = resolution.reason
    if track := resolution.track:
        stream = track.stream
        told["track"] = {
            name: value
            for name, value in {
                "path": track.variant.path,
                "instance_id": track.variant.instance_id,
                "index": track.index,
                "codec": stream.get("codec_name"),
                "channels": stream.get("channels"),
                "title": (stream.get("tags") or {}).get("title"),
            }.items()
            if value not in (None, "")
        }
    if mapping := resolution.mapping:
        told["mapping"] = {"scale": str(mapping.scale), "offset_us": mapping.offset_us}
        told["measured"] = resolution.measured
    return told


#: Requirement states that ask nothing of a source change: settled one way or
#: the other by what the target holds.
_SETTLED = frozenset({SATISFIED})


def open_requirements(projection: dict) -> bool:
    """Whether a stored assessment still waits on something outside the target."""
    return any(
        isinstance(item, dict) and item.get("state") not in _SETTLED
        for item in projection.get("requirements") or []
    )


def awaits_measurement(projection: dict) -> bool:
    """Whether a stored assessment has a source still to be measured."""
    return any(
        isinstance(item, dict) and item.get("state") == ANALYSIS_REQUIRED
        for item in projection.get("requirements") or []
    )


def candidates(inventory: Inventory, target: Variant) -> list[Variant]:
    """The variants that may supply ``target``, preferred first.

    Inventory order is connection order, which is the preference. Identity
    must match exactly and neither file may be in doubt.
    """
    if target.excluded or not target.content_keys:
        return []
    return [
        variant
        for variant in inventory.variants
        if variant.locator != target.locator
        and variant.content_keys == target.content_keys
        and not variant.excluded
        and variant.revision is not None
        and not _editions_conflict(variant, target)
    ]


def _editions_conflict(first: Variant, second: Variant) -> bool:
    """Two named editions that differ are different cuts. An unnamed one may
    be either, so measurement decides."""
    return bool(first.edition and second.edition) and (
        first.edition.casefold() != second.edition.casefold()
    )


def tracks_in(info: dict, lang: str, policy: Policy) -> list[dict]:
    """A source's main audio in ``lang``, best first.

    Native before imported, then the most channels, as a downmix source is
    chosen. Commentary, description and a removed layout never count.
    """
    removed = {layout.channels for layout in policy.removed()}
    found = [
        stream
        for stream in info.get("streams") or []
        if stream.get("codec_type") == "audio"
        and stream_lang(stream) == lang
        and not is_commentary(stream, policy)
        and stream.get("channels") not in removed
    ]
    return sorted(
        found,
        key=lambda stream: (
            tag_value(stream, IMPORTED_TAG) is not None,
            -(stream.get("channels") or 0),
            stream["index"],
        ),
    )


def digest(inventory: Inventory, target: Variant | None) -> str:
    """What could change the answer for ``target``: the files that share its
    identity, as they stand, and the connections that could not say. The
    target's own contents are left out, since its verdict's key covers them
    and a listing taken before our own rewrite would name the old file."""
    held = [
        [
            variant.instance_id,
            variant.path,
            variant.excluded,
            variant.edition,
            *_stamp(None if variant.locator == target.locator else variant.revision),
        ]
        for variant in inventory.variants
        if target is not None
        and (variant.content_keys == target.content_keys or variant.locator == target.locator)
    ]
    canonical = json.dumps(
        {"variants": held, "unavailable": sorted(inventory.unavailable)}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _stamp(revision: FileRevision | None) -> tuple:
    if revision is None:
        return (None, None, None, None)
    return (revision.device, revision.inode, revision.size, revision.mtime_ns)
