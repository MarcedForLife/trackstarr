"""Where a target's missing audio can come from: the title's other variants,
their tracks and how their timelines line up.

The I/O half of :mod:`trackstarr.sourcing`. A title's listing is reused for a
few minutes, as the catalogue reuses its own, and any delivery drops them.
"""

import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from . import alignment, executor, inventory, library, paths, sourcing
from .alignment import AlignmentResult
from .inventory import Inventory, Variant
from .library import Title
from .media import ProbeError, probe
from .planner import FileRevision, Plan, PlanInput, TimingMapping
from .policy import ALONGSIDE, ALWAYS, Policy
from .sourcing import (
    ANALYSIS_REQUIRED,
    FAILED,
    MISSING_SOURCE,
    NEEDS_REVIEW,
    READY,
    SATISFIED,
    UNAVAILABLE,
    Resolution,
    SourcingAssessment,
    TrackRef,
)

log = logging.getLogger(__name__)

#: How long one title's listing is reused, and how soon one that could not be
#: completed is asked again.
LISTING_TTL = 300.0
RETRY_SECONDS = 15.0

#: Failed measurements of one pair before it waits for a change instead.
MAX_ATTEMPTS = 3

#: Measurements held in memory, so a deferred rewrite does not measure again.
_REMEMBERED = 64

#: Measures a source against the target, claiming whatever the work needs.
type Measure = Callable[[FileRevision, FileRevision], AlignmentResult]

_lock = threading.Lock()
_listings: dict[str, tuple[float, Inventory]] = {}
_measured: OrderedDict[tuple, AlignmentResult] = OrderedDict()
#: Moved by every :func:`forget`, so a listing asked for before one is not
#: kept after it.
_generation = 0


def forget() -> None:
    """Drop every listing, so the next assessment asks the *arrs again."""
    global _generation
    with _lock:
        _listings.clear()
        _generation += 1


def reset() -> None:
    """Drop listings and remembered measurements, for tests."""
    forget()
    with _lock:
        _measured.clear()


def _inventory(title: Title) -> Inventory:
    now = time.monotonic()
    with _lock:
        held = _listings.get(title.id)
        if held is not None and now < held[0]:
            return held[1]
        asked = _generation
    # Asked outside the lock: a slow *arr must not hold up other titles.
    taken = inventory.take(title)
    with _lock:
        if asked == _generation:
            expires = now + (LISTING_TTL if taken.complete else RETRY_SECONDS)
            _listings[title.id] = (expires, taken)
    return taken


@dataclass(frozen=True)
class _Located:
    """The target among its title's variants, or why it cannot be placed."""

    inventory: Inventory | None = None
    target: Variant | None = None
    title: Title | None = None


def _locate(path: str) -> _Located:
    title = library.title_for(path)
    if title is None:
        return _Located()
    taken = _inventory(title)
    canonical = paths.canonical(path)
    try:
        identity = FileRevision.of(path).identity
    except OSError:
        identity = None
    target = next(
        (
            variant
            for variant in taken.variants
            if variant.path == canonical
            or (variant.revision is not None and variant.revision.identity == identity)
        ),
        None,
    )
    return _Located(taken, target, title)


def _key(revision: FileRevision) -> tuple:
    """A revision's content, which a rename or a released hard link keeps."""
    return revision.device, revision.inode, revision.size, revision.mtime_ns


def _remember(result: AlignmentResult) -> None:
    with _lock:
        key = (_key(result.target), _key(result.source))
        _measured[key] = result
        _measured.move_to_end(key)
        while len(_measured) > _REMEMBERED:
            _measured.popitem(last=False)


def _remembered(target: FileRevision, source: FileRevision) -> AlignmentResult | None:
    with _lock:
        found = _measured.get((_key(target), _key(source)))
    return found if found is not None and found.current else None


@dataclass(frozen=True)
class _Timing:
    """How one source's timeline relates to the target's, as far as known."""

    state: str
    reason: str = ""
    mapping: TimingMapping | None = None
    measured: bool = False


@dataclass
class _Search:
    """One assessment's facts, so each source is probed and measured once
    however many languages it could supply."""

    plan: Plan
    info: dict
    target: FileRevision
    measure: Measure | None
    #: The stored verdict's measurements, read on first need.
    recorded: list[dict] | None = None
    observed: dict[str, tuple[FileRevision, dict] | None] = field(default_factory=dict)
    timings: dict[tuple, _Timing] = field(default_factory=dict)
    #: The measurements this assessment rests on, to store with it.
    records: dict[tuple, dict] = field(default_factory=dict)

    def observe(self, variant: Variant) -> tuple[FileRevision, dict] | None:
        """The variant's revision and probe, or None where it cannot be read."""
        if variant.path not in self.observed:
            try:
                revision = FileRevision.of(variant.path)
                with executor.read_source(revision):
                    self.observed[variant.path] = revision, probe(revision.path)
            except (OSError, ProbeError, InterruptedError) as err:
                log.info("cannot read %s as an audio source: %s", variant.path, err)
                self.observed[variant.path] = None
        return self.observed[variant.path]

    def timing(self, revision: FileRevision, info: dict) -> _Timing:
        key = _key(revision)
        if key not in self.timings:
            self.timings[key] = self._timing(revision, info)
        return self.timings[key]

    def _timing(self, revision: FileRevision, info: dict) -> _Timing:
        if alignment.same_timeline(self.info, info):
            return _Timing(READY, mapping=TimingMapping())
        if self.recorded is None:
            self.recorded = stored_alignments(self.plan.path)
        known = _remembered(self.target, revision) or alignment.recalled(
            self.recorded, self.target, revision
        )
        due = known is None or (
            known.status == alignment.FAILED and known.attempts < MAX_ATTEMPTS
        )
        if due and self.measure is not None:
            measured = self.measure(self.target, revision)
            if known is not None and measured.status == alignment.FAILED:
                measured = replace(measured, attempts=known.attempts + 1)
            _remember(measured)
            known = measured
        if known is None:
            return _Timing(ANALYSIS_REQUIRED, "timing not yet measured")
        # Kept whatever it says, or a retry's attempt count would start over.
        self.records[_key(revision)] = alignment.record(known)
        if known.status == alignment.ACCEPTED:
            return _Timing(READY, mapping=known.mapping, measured=True)
        if known.status == alignment.FAILED:
            if known.attempts < MAX_ATTEMPTS:
                return _Timing(ANALYSIS_REQUIRED, f"measuring again after: {known.reason}")
            return _Timing(FAILED, f"measuring timing failed: {known.reason}")
        return _Timing(NEEDS_REVIEW, alignment.refusal(known.reason))


def assess(
    plan: Plan,
    info: dict,
    *,
    measure: Measure | None = None,
) -> SourcingAssessment | None:
    """Where each requirement of a local plan stands, or None where the rule
    asks for nothing.

    ``info`` is the probe the plan came from. Without ``measure`` nothing is
    measured, and a source that needs it is left analysis_required. What the
    stored verdict measured before is reused.
    """
    needed = sourcing.requirements(plan)
    if not needed:
        return None
    resolutions = {
        requirement.lang: Resolution(requirement.lang, SATISFIED)
        for requirement in needed
        if requirement.satisfied
    }
    missing = [requirement.lang for requirement in needed if not requirement.satisfied]
    digest = ""
    search = None
    if missing:
        # Located even where the container refuses: without the digest,
        # changed() and affected() would take every listing as a change.
        located = _locate(plan.path)
        if located.inventory is not None:
            digest = sourcing.digest(located.inventory, located.target)
        blocked = _blocked(plan, located)
        # Narrowed again for the type checker: no block means both were found.
        if blocked is None and located.inventory is not None and located.target is not None:
            search = _Search(plan, info, FileRevision.of(plan.path), measure)
            sources = sourcing.candidates(located.inventory, located.target)
            for lang in missing:
                resolutions[lang] = _resolve(search, lang, sources, located.inventory.complete)
        else:
            state, reason = blocked or (NEEDS_REVIEW, "")
            resolutions |= {lang: Resolution(lang, state, reason) for lang in missing}
    return SourcingAssessment(
        tuple(resolutions[item.lang] for item in needed),
        digest,
        tuple(search.records.values()) if search else (),
    )


def _container_refused(plan: Plan) -> bool:
    """Imports need Matroska output, which only the remux rule makes of an MP4."""
    return os.path.splitext(plan.path)[1].lower() != ".mkv" and "remux" not in (
        plan.policy.rules_in(ALWAYS, ALONGSIDE)
    )


def _blocked(plan: Plan, located: _Located) -> tuple[str, str] | None:
    """The state and reason of every requirement where no source may be
    chosen for this target at all, or None."""
    if _container_refused(plan):
        return NEEDS_REVIEW, "imports need Matroska output, which RULE_REMUX allows"
    if located.inventory is None:
        return NEEDS_REVIEW, "no connection lists this title"
    if located.target is None:
        if not located.inventory.complete:
            return UNAVAILABLE, "a connection could not be listed"
        return NEEDS_REVIEW, "no connection lists this file"
    if located.target.excluded:
        return NEEDS_REVIEW, f"identity in doubt ({located.target.excluded})"
    if not located.target.content_keys:
        return NEEDS_REVIEW, "the connection has no provider id for this file"
    return None


def _resolve(search: _Search, lang: str, sources: list[Variant], complete: bool) -> Resolution:
    """The first source, in preference order, that can supply ``lang``."""
    refused: list[_Timing] = []
    unreadable = False
    policy = search.plan.policy
    for variant in sources:
        observed = search.observe(variant)
        if observed is None:
            unreadable = True
            continue
        revision, info = observed
        tracks = sourcing.tracks_in(info, lang, policy)
        if not tracks:
            continue
        timing = search.timing(revision, info)
        if timing.state == ANALYSIS_REQUIRED:
            return Resolution(
                lang, ANALYSIS_REQUIRED, timing.reason, TrackRef(variant, revision, tracks[0])
            )
        if timing.state != READY or timing.mapping is None:
            refused.append(timing)
            continue
        for stream in tracks:
            if (problem := _unpreparable(revision, stream, timing.mapping, policy)) is None:
                track = TrackRef(variant, revision, stream)
                return Resolution(lang, READY, "", track, timing.mapping, timing.measured)
            refused.append(_Timing(NEEDS_REVIEW, problem))
    if refused:
        return Resolution(lang, refused[0].state, refused[0].reason)
    if unreadable:
        return Resolution(lang, UNAVAILABLE, "a possible source could not be read")
    if not complete:
        return Resolution(lang, UNAVAILABLE, "a connection could not be listed")
    return Resolution(lang, MISSING_SOURCE)


def _unpreparable(
    revision: FileRevision, stream: dict, mapping: TimingMapping, policy: Policy
) -> str | None:
    """Why preparation would refuse this stream under this mapping, or None."""
    try:
        executor.preparation_timing(
            PlanInput(revision, stream["index"], mapping), stream, policy
        )
    except ValueError as err:
        return str(err)
    return None


def _projection(entry: object) -> dict:
    """The stored assessment in an entry, or nothing for a damaged one."""
    found = entry.get("sourcing") if isinstance(entry, dict) else None
    return found if isinstance(found, dict) else {}


def stored_alignments(path: str) -> list[dict]:
    """What the stored verdict of ``path`` measured, for reuse."""
    found = _projection(library.stored_entries().get(path)).get("alignments")
    return found if isinstance(found, list) else []


def changed(path: str, projection: dict) -> bool:
    """Whether a stored assessment was judged against other variants than the
    title has now."""
    located = _locate(path)
    current = sourcing.digest(located.inventory, located.target) if located.inventory else ""
    return current != projection.get("inventory", "")


@dataclass(frozen=True)
class Affected:
    """Another variant whose open assessment a change may answer."""

    variant: Variant
    title: Title


def affected(path: str) -> list[Affected]:
    """The title's other variants sharing ``path``'s identity whose stored
    assessments are open and no longer match what the title holds. Listings
    are taken afresh, since a delivery has just changed them."""
    forget()
    located = _locate(path)
    if located.inventory is None or located.target is None or located.title is None:
        return []
    entries = library.stored_entries()
    found = []
    for variant in located.inventory.variants:
        if (
            variant.locator == located.target.locator
            or variant.content_keys != located.target.content_keys
        ):
            continue
        projection = _projection(entries.get(variant.path))
        if sourcing.open_requirements(projection) and projection.get(
            "inventory"
        ) != sourcing.digest(located.inventory, variant):
            found.append(Affected(variant, located.title))
    return found
