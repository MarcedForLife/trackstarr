"""Missing audio is sourced from the title's other variants, measured once and then reused."""

import os
import types
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import pytest

from conftest import audio, probe_data, set_langs, set_layouts, set_rules, video
from trackstarr import alignment, audio_sources, inventory, library, sourcing
from trackstarr.alignment import AlignmentResult
from trackstarr.inventory import ContentKey, Inventory, Variant
from trackstarr.library import Source, Title
from trackstarr.media import ProbeError
from trackstarr.planner import FileRevision, PlanInput, TimingMapping, new_plan, plan_from_probe
from trackstarr.sourcing import (
    ANALYSIS_REQUIRED,
    FAILED,
    MISSING_SOURCE,
    NEEDS_REVIEW,
    READY,
    SATISFIED,
    UNAVAILABLE,
    Resolution,
    SourcingAssessment,
)

DUNE = ContentKey("tmdb", "movie", "438631")
SEQUEL = ContentKey("tmdb", "movie", "693134")
TITLE = Title("arr:radarr:1", "Dune", (Source("/movies/Dune"),), "movie", provider_id="438631")
SHIFT = TimingMapping(Fraction(1), 250_000)
SPEED_UP = TimingMapping(Fraction(25, 24), 0)
#: What the matcher says alongside each status.
REASONS = {
    alignment.ACCEPTED: "mapping_confirmed",
    alignment.NEEDS_REVIEW: "ambiguous_offset",
    alignment.UNSUPPORTED: "no_video",
    alignment.FAILED: "signature_export_failed",
}


def timed_video() -> dict:
    """A main video same_timeline can compare, which a bare video() is not."""
    return video() | {"r_frame_rate": "24000/1001", "start_time": "0", "duration": "5400"}


def truehd(index: int, lang: str) -> dict:
    """Immersive audio, which preparation refuses to retime."""
    return audio(index, 8, lang) | {"codec_name": "truehd"}


def result(
    target: FileRevision, source: FileRevision, status: str, mapping=SHIFT, **fields
) -> AlignmentResult:
    accepted = status == alignment.ACCEPTED
    return AlignmentResult(
        target, source, status, REASONS[status], mapping if accepted else None, **fields
    )


@dataclass
class Holdings:
    """The files the faked *arrs list, their probes and the stored verdicts."""

    root: Path
    variants: list[Variant] = field(default_factory=list)
    unavailable: tuple[str, ...] = ()
    probes: dict[str, dict] = field(default_factory=dict)
    entries: dict[str, dict] = field(default_factory=dict)
    titled: bool = True
    listings: int = 0
    probed: list[str] = field(default_factory=list)

    def take(self, title: Title) -> Inventory:
        self.listings += 1
        return Inventory(title.id, tuple(self.variants), self.unavailable)

    def probe(self, path: str) -> dict:
        self.probed.append(path)
        if path not in self.probes:
            raise ProbeError("Invalid data found when processing input")
        return self.probes[path]

    def add(
        self,
        name: str,
        *streams: dict,
        instance_id: str,
        keys: tuple[ContentKey, ...] = (DUNE,),
        excluded: str | None = None,
        listed: bool = True,
    ) -> str:
        path = str(self.root / name)
        Path(path).write_bytes(b"media")
        self.probes[path] = probe_data(*streams)
        if listed:
            file_id = len(self.variants) + 1
            revision = FileRevision.of(path)
            self.variants.append(
                Variant(
                    path, instance_id, 1, file_id, keys, revision=revision, excluded=excluded
                )
            )
        return path

    def target(self, *streams: dict, name: str = "Dune.mkv", **listing) -> str:
        """The file being assessed, holding English audio unless told otherwise."""
        held = streams or (video(), audio(1, 2, "eng"))
        return self.add(name, *held, instance_id="radarr", **listing)

    def source(self, *streams: dict, picture: dict | None = None, **listing) -> str:
        """Another variant, each in a connection of its own unless told otherwise."""
        number = len(self.probes)
        listing.setdefault("instance_id", f"radarr-{number}")
        return self.add(f"Dune.{number}.mkv", picture or video(), *streams, **listing)

    def store(self, target: str, source: str, status: str, **fields) -> dict:
        """A measurement in the target's stored verdict, as an earlier run left it."""
        found = result(FileRevision.of(target), FileRevision.of(source), status, **fields)
        stored = alignment.record(found)
        verdict = self.entries.setdefault(target, {"sourcing": {"alignments": []}})
        verdict["sourcing"]["alignments"].append(stored)
        return stored

    def assess(self, target: str, measure=None) -> SourcingAssessment:
        info = self.probes[target]
        plan = plan_from_probe(new_plan(target, None), info)
        return audio_sources.assess(plan, info, measure=measure)


@dataclass
class Measurer:
    """Answers each source with its status in ``answers``, accepting the rest."""

    answers: dict[str, str] = field(default_factory=dict)
    mapping: TimingMapping = SHIFT
    version: int = alignment.VERSION
    asked: list[str] = field(default_factory=list)

    def __call__(self, target: FileRevision, source: FileRevision) -> AlignmentResult:
        self.asked.append(source.path)
        status = self.answers.get(source.path, alignment.ACCEPTED)
        return result(target, source, status, self.mapping, version=self.version)


@pytest.fixture(autouse=True)
def _forgotten():
    audio_sources.reset()
    yield
    audio_sources.reset()


@pytest.fixture
def holdings(monkeypatch, tmp_path):
    set_rules(source_audio="always")
    set_langs("eng:keep", "fre:keep")
    set_layouts()
    held = Holdings(Path(os.path.realpath(tmp_path)))
    monkeypatch.setattr(library, "title_for", lambda path: TITLE if held.titled else None)
    monkeypatch.setattr(library, "stored_entries", lambda: held.entries)
    monkeypatch.setattr(inventory, "take", held.take)
    monkeypatch.setattr(audio_sources, "probe", held.probe)
    return held


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock the test moves by hand."""
    held = types.SimpleNamespace(now=1_000.0)
    monkeypatch.setattr(
        audio_sources, "time", types.SimpleNamespace(monotonic=lambda: held.now)
    )
    return held


def resolved(assessment: SourcingAssessment, lang: str = "fre") -> Resolution:
    return next(resolution for resolution in assessment.resolutions if resolution.lang == lang)


def test_a_file_the_rule_does_not_ask_about_has_no_assessment(holdings):
    set_rules(source_audio="never")
    assert holdings.assess(holdings.target()) is None
    assert holdings.listings == 0


def test_a_target_holding_every_language_is_complete_without_listing_the_title(holdings):
    target = holdings.target(video(), audio(1, 2, "eng"), audio(2, 2, "fre"))
    assessment = holdings.assess(target)
    assert assessment.project() == {
        "completeness": sourcing.COMPLETE,
        "requirements": [
            {"lang": "eng", "state": SATISFIED},
            {"lang": "fre", "state": SATISFIED},
        ],
    }
    assert holdings.listings == 0


def test_a_title_no_connection_lists_waits_for_review(holdings):
    holdings.titled = False
    assessment = holdings.assess(holdings.target())
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (NEEDS_REVIEW, "no connection lists this title")
    assert assessment.inventory == "" and holdings.listings == 0


@pytest.mark.parametrize(
    ("unavailable", "state", "reason", "completeness"),
    [
        ((), NEEDS_REVIEW, "no connection lists this file", sourcing.INCOMPLETE),
        (("radarr-4k",), UNAVAILABLE, "a connection could not be listed", sourcing.UNKNOWN),
    ],
)
def test_a_file_no_connection_lists_waits_for_review_unless_one_is_down(
    holdings, unavailable, state, reason, completeness
):
    holdings.unavailable = unavailable
    target = holdings.target(listed=False)
    holdings.source(audio(1, 2, "fre"))
    assessment = holdings.assess(target)
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (state, reason)
    assert assessment.completeness == completeness
    listing = Inventory(TITLE.id, tuple(holdings.variants), unavailable)
    assert assessment.inventory == sourcing.digest(listing, None)
    assert holdings.probed == []


@pytest.mark.parametrize(
    ("listing", "reason"),
    [
        ({"excluded": inventory.SAME_FILE}, "identity in doubt (same_file)"),
        ({"keys": ()}, "the connection has no provider id for this file"),
    ],
)
def test_a_target_whose_identity_is_in_doubt_waits_for_review(holdings, listing, reason):
    target = holdings.target(**listing)
    holdings.source(audio(1, 2, "fre"))
    waiting = resolved(holdings.assess(target))
    assert (waiting.state, waiting.reason) == (NEEDS_REVIEW, reason)
    assert holdings.probed == []


def test_an_mp4_target_takes_imports_only_under_the_remux_rule(holdings):
    target = holdings.target(timed_video(), audio(1, 2, "eng"), name="Dune.mp4")
    holdings.source(audio(1, 2, "fre"), picture=timed_video())
    refused = resolved(holdings.assess(target))
    assert (refused.state, refused.reason) == (
        NEEDS_REVIEW,
        "imports need Matroska output, which RULE_REMUX allows",
    )
    assert holdings.probed == []
    set_rules(remux="always")
    assert resolved(holdings.assess(target)).state == READY


def test_a_source_on_the_same_timeline_is_ready_without_measuring(holdings):
    target = holdings.target(timed_video(), audio(1, 2, "eng"))
    source = holdings.source(audio(1, 6, "fre"), picture=timed_video())
    measure = Measurer()
    assessment = holdings.assess(target, measure)
    ready = resolved(assessment)
    assert (ready.state, ready.mapping, ready.measured) == (READY, TimingMapping(), False)
    assert assessment.inputs == (PlanInput(FileRevision.of(source), 1, TimingMapping()),)
    assert measure.asked == [] and assessment.alignments == ()


def test_an_unmeasured_source_waits_for_analysis_naming_its_best_track(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"), audio(2, 6, "fre"))
    assessment = holdings.assess(target)
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (ANALYSIS_REQUIRED, "timing not yet measured")
    assert (waiting.track.variant.path, waiting.track.index) == (source, 2)
    assert waiting.mapping is None and assessment.inputs == () and assessment.alignments == ()
    assert assessment.completeness == sourcing.INCOMPLETE


def test_several_languages_from_one_source_are_probed_and_measured_once(holdings):
    set_langs("eng:keep", "fre:keep", "ger:keep")
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"), audio(2, 2, "ger"))
    measure = Measurer()
    assessment = holdings.assess(target, measure)
    assert [(item.lang, item.state, item.measured) for item in assessment.resolutions] == [
        ("eng", SATISFIED, False),
        ("fre", READY, True),
        ("ger", READY, True),
    ]
    assert [(item.stream_index, item.mapping) for item in assessment.inputs] == [
        (1, SHIFT),
        (2, SHIFT),
    ]
    assert measure.asked == [source] and holdings.probed == [source]
    (stored,) = assessment.alignments
    assert (stored["status"], stored["source"]["path"]) == (alignment.ACCEPTED, source)


def test_the_first_connection_listed_is_preferred(holdings):
    target = holdings.target()
    preferred = holdings.source(audio(1, 2, "fre"))
    holdings.source(audio(1, 6, "fre"))
    measure = Measurer()
    assert resolved(holdings.assess(target, measure)).track.variant.path == preferred
    assert measure.asked == [preferred]


def test_a_source_without_the_language_is_passed_over_unmeasured(holdings):
    target = holdings.target()
    holdings.source(audio(1, 2, "ger"))
    supplier = holdings.source(audio(1, 2, "fre"))
    measure = Measurer()
    assert resolved(holdings.assess(target, measure)).track.variant.path == supplier
    assert measure.asked == [supplier]


def test_a_refused_source_falls_through_to_the_next(holdings):
    target = holdings.target()
    refused = holdings.source(audio(1, 2, "fre"))
    fallback = holdings.source(audio(1, 2, "fre"))
    measure = Measurer({refused: alignment.NEEDS_REVIEW})
    assessment = holdings.assess(target, measure)
    assert resolved(assessment).track.variant.path == fallback
    assert measure.asked == [refused, fallback]
    assert [stored["status"] for stored in assessment.alignments] == [
        alignment.NEEDS_REVIEW,
        alignment.ACCEPTED,
    ]


@pytest.mark.parametrize(
    ("status", "state", "reason"),
    [
        (
            alignment.NEEDS_REVIEW,
            NEEDS_REVIEW,
            "timing could not be confirmed: ambiguous_offset",
        ),
        (alignment.UNSUPPORTED, NEEDS_REVIEW, "timing could not be confirmed: no_video"),
    ],
)
def test_a_measurement_that_confirms_no_mapping_imports_nothing(
    holdings, status, state, reason
):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    assessment = holdings.assess(target, Measurer({source: status}))
    refused = resolved(assessment)
    assert (refused.state, refused.reason, refused.track) == (state, reason, None)
    assert assessment.inputs == ()


def test_when_every_source_is_refused_the_preferred_sources_reason_stands(holdings):
    target = holdings.target()
    preferred = holdings.source(audio(1, 2, "fre"))
    other = holdings.source(audio(1, 2, "fre"))
    measure = Measurer({preferred: alignment.NEEDS_REVIEW, other: alignment.UNSUPPORTED})
    refused = resolved(holdings.assess(target, measure))
    assert (refused.state, refused.reason) == (
        NEEDS_REVIEW,
        "timing could not be confirmed: ambiguous_offset",
    )


def test_a_complete_listing_without_the_language_reports_it_missing(holdings):
    target = holdings.target()
    other_language = holdings.source(audio(1, 2, "ger"))
    holdings.source(audio(1, 2, "fre"), keys=(SEQUEL,))
    measure = Measurer()
    assessment = holdings.assess(target, measure)
    assert resolved(assessment).state == MISSING_SOURCE
    assert assessment.completeness == sourcing.INCOMPLETE
    assert holdings.probed == [other_language] and measure.asked == []


def test_an_incomplete_listing_leaves_a_missing_language_unknown(holdings):
    holdings.unavailable = ("radarr-4k",)
    target = holdings.target()
    holdings.source(audio(1, 2, "ger"))
    assessment = holdings.assess(target)
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (UNAVAILABLE, "a connection could not be listed")
    assert assessment.completeness == sourcing.UNKNOWN


@pytest.mark.parametrize("damage", ["deleted", "corrupt"])
def test_an_unreadable_source_leaves_the_answer_unknown(holdings, damage):
    target = holdings.target()
    unreadable = holdings.source(audio(1, 2, "fre"))
    if damage == "deleted":
        os.remove(unreadable)
    else:
        del holdings.probes[unreadable]
    assessment = holdings.assess(target, Measurer())
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (
        UNAVAILABLE,
        "a possible source could not be read",
    )
    assert assessment.completeness == sourcing.UNKNOWN


def test_an_unreadable_source_gives_way_to_a_readable_one(holdings):
    target = holdings.target()
    unreadable = holdings.source(audio(1, 2, "fre"))
    readable = holdings.source(audio(1, 2, "fre"))
    del holdings.probes[unreadable]
    chosen = resolved(holdings.assess(target, Measurer()))
    assert (chosen.state, chosen.track.variant.path) == (READY, readable)


def test_a_target_listed_under_another_name_is_found_by_its_inode(holdings):
    target = holdings.target(listed=False)
    holdings.variants.append(
        Variant(str(holdings.root / "Dune.old.mkv"), "radarr", 1, 8, (DUNE,))
    )
    linked = str(holdings.root / "Dune.linked.mkv")
    os.link(target, linked)
    revision = FileRevision.of(linked)
    holdings.variants.append(Variant(linked, "radarr", 1, 9, (DUNE,), revision=revision))
    source = holdings.source(audio(1, 2, "fre"))
    measure = Measurer()
    chosen = resolved(holdings.assess(target, measure))
    assert (chosen.state, chosen.track.variant.path) == (READY, source)
    assert measure.asked == [source]


def test_a_track_preparation_refuses_gives_way_to_the_next_track(holdings):
    set_layouts("2.0:flac:192k")
    target = holdings.target()
    holdings.source(truehd(1, "fre"), audio(2, 2, "fre"))
    chosen = resolved(holdings.assess(target, Measurer(mapping=SPEED_UP)))
    assert (chosen.state, chosen.track.index, chosen.mapping) == (READY, 2, SPEED_UP)


def test_a_source_whose_every_track_preparation_refuses_waits_for_review_or_the_next(holdings):
    set_layouts("2.0:flac:192k")
    target = holdings.target()
    holdings.source(truehd(1, "fre"))
    measure = Measurer(mapping=SPEED_UP)
    refused = resolved(holdings.assess(target, measure))
    assert (refused.state, refused.reason) == (
        NEEDS_REVIEW,
        "retiming this codec could discard immersive audio",
    )
    fallback = holdings.source(audio(1, 2, "fre"))
    audio_sources.forget()
    chosen = resolved(holdings.assess(target, measure))
    assert (chosen.state, chosen.track.variant.path) == (READY, fallback)


@pytest.mark.parametrize(
    ("status", "attempts", "state"),
    [
        (alignment.ACCEPTED, 1, READY),
        (alignment.NEEDS_REVIEW, 1, NEEDS_REVIEW),
        (alignment.FAILED, audio_sources.MAX_ATTEMPTS, FAILED),
    ],
)
def test_a_recorded_measurement_is_reused_without_measuring(holdings, status, attempts, state):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    stored = holdings.store(target, source, status, attempts=attempts)
    measure = Measurer()
    assessment = holdings.assess(target, measure)
    assert resolved(assessment).state == state
    assert measure.asked == [] and assessment.alignments == (stored,)


def test_a_recorded_acceptance_supplies_its_mapping_as_measured(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    later = TimingMapping(Fraction(1), 1_500_000)
    holdings.store(target, source, alignment.ACCEPTED, mapping=later)
    ready = resolved(holdings.assess(target))
    assert (ready.state, ready.mapping, ready.measured) == (READY, later, True)


def test_a_first_failed_measurement_waits_to_be_measured_again(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    assessment = holdings.assess(target, Measurer({source: alignment.FAILED}))
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (
        ANALYSIS_REQUIRED,
        "measuring again after: signature_export_failed",
    )
    assert waiting.track.variant.path == source
    (stored,) = assessment.alignments
    assert stored["status"] == alignment.FAILED and "attempts" not in stored


def test_a_failed_measurement_is_retried_until_the_attempts_run_out(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    holdings.store(target, source, alignment.FAILED)
    measure = Measurer({source: alignment.FAILED})
    outcomes = []
    for _attempt in range(3):
        assessment = holdings.assess(target, measure)
        found = resolved(assessment)
        outcomes.append((found.state, found.reason, assessment.alignments[0]["attempts"]))
    given_up = (FAILED, "measuring timing failed: signature_export_failed", 3)
    assert outcomes == [
        (ANALYSIS_REQUIRED, "measuring again after: signature_export_failed", 2),
        given_up,
        given_up,
    ]
    assert measure.asked == [source, source]


def test_a_retry_that_succeeds_drops_the_failure_count(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    holdings.store(target, source, alignment.FAILED, attempts=2)
    assessment = holdings.assess(target, Measurer())
    assert resolved(assessment).state == READY
    assert "attempts" not in assessment.alignments[0]


def test_a_record_from_another_matcher_is_measured_again(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    holdings.store(target, source, alignment.ACCEPTED, backend="frame-hash")
    measure = Measurer()
    assessment = holdings.assess(target, measure)
    assert measure.asked == [source]
    assert assessment.alignments[0]["backend"] == alignment.BACKEND


def test_discovery_keeps_a_failed_record_and_its_attempt_count(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    stored = holdings.store(target, source, alignment.FAILED, attempts=2)
    assessment = holdings.assess(target)
    waiting = resolved(assessment)
    assert (waiting.state, waiting.reason) == (
        ANALYSIS_REQUIRED,
        "measuring again after: signature_export_failed",
    )
    assert waiting.track.variant.path == source
    assert assessment.alignments == (stored,) and stored["attempts"] == 2


@pytest.mark.parametrize(
    "entry", ["damaged", {"sourcing": "damaged"}, {"sourcing": {"alignments": "damaged"}}]
)
def test_a_damaged_stored_verdict_offers_no_measurements(holdings, entry):
    holdings.entries["/movies/Dune.mkv"] = entry
    assert audio_sources.stored_alignments("/movies/Dune.mkv") == []


def test_a_deferred_rewrite_reuses_the_measurement_held_in_memory(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    measure = Measurer()
    holdings.assess(target, measure)
    audio_sources.forget()
    ready = resolved(holdings.assess(target))
    assert (ready.state, ready.measured) == (READY, True)
    assert measure.asked == [source] and holdings.listings == 2


def test_a_measurement_held_from_another_matcher_version_is_taken_again(holdings):
    target = holdings.target()
    source = holdings.source(audio(1, 2, "fre"))
    measure = Measurer(version=alignment.VERSION - 1)
    holdings.assess(target, measure)
    holdings.assess(target, measure)
    assert measure.asked == [source, source]


def test_only_the_latest_measurements_are_held_in_memory(holdings, monkeypatch):
    monkeypatch.setattr(audio_sources, "_REMEMBERED", 1)
    set_langs("eng:keep", "fre:keep", "ger:keep")
    target = holdings.target()
    holdings.source(audio(1, 2, "fre"))
    holdings.source(audio(1, 2, "ger"))
    holdings.assess(target, Measurer())
    later = holdings.assess(target)
    assert [resolution.state for resolution in later.resolutions] == [
        SATISFIED,
        ANALYSIS_REQUIRED,
        READY,
    ]


def test_a_titles_listing_is_reused_until_it_expires(holdings, clock):
    target = holdings.target()
    audio_sources.changed(target, {})
    clock.now += audio_sources.LISTING_TTL - 1
    audio_sources.changed(target, {})
    assert holdings.listings == 1
    clock.now += 1
    audio_sources.changed(target, {})
    assert holdings.listings == 2


def test_an_incomplete_listing_is_asked_again_sooner(holdings, clock):
    holdings.unavailable = ("radarr-4k",)
    target = holdings.target()
    audio_sources.changed(target, {})
    clock.now += audio_sources.RETRY_SECONDS
    audio_sources.changed(target, {})
    assert holdings.listings == 2


def test_an_assessment_changes_only_when_variants_of_the_same_content_do(holdings):
    target = holdings.target()
    holdings.source(audio(1, 2, "ger"))
    projection = holdings.assess(target).project()
    assert not audio_sources.changed(target, projection)
    holdings.source(audio(1, 2, "fre"), keys=(SEQUEL,))
    audio_sources.forget()
    assert not audio_sources.changed(target, projection)
    holdings.source(audio(1, 2, "fre"))
    audio_sources.forget()
    assert audio_sources.changed(target, projection)


def test_a_title_no_connection_lists_changes_only_what_was_judged_against_one(holdings):
    holdings.titled = False
    target = holdings.target()
    assert not audio_sources.changed(target, holdings.assess(target).project())
    assert audio_sources.changed(target, {"inventory": "0123456789ab"})


def test_a_target_gone_from_disk_is_still_placed_by_its_listed_path(holdings):
    target = holdings.target()
    holdings.source(audio(1, 2, "ger"))
    projection = holdings.assess(target).project()
    os.remove(target)
    audio_sources.forget()
    # Unplaced, the digest would cover no variants and differ.
    assert not audio_sources.changed(target, projection)


def test_an_mp4_refused_for_its_container_waits_until_its_variants_change(holdings):
    target = holdings.target(name="Dune.mp4")
    sibling = holdings.source(audio(1, 2, "eng"))
    projection = holdings.assess(target).project()
    holdings.entries[target] = {"sourcing": projection}
    assert not audio_sources.changed(target, projection)
    assert audio_sources.affected(sibling) == []
    holdings.source(audio(1, 2, "fre"))
    assert [item.variant.path for item in audio_sources.affected(sibling)] == [target]


def test_a_delivery_affects_open_variants_of_the_same_content_judged_against_others(
    holdings,
):
    delivered = holdings.target()
    stale = holdings.source(audio(1, 2, "eng"))
    current = holdings.source(audio(1, 2, "eng"))
    settled = holdings.source(audio(1, 2, "eng"))
    sequel = holdings.source(audio(1, 2, "eng"), keys=(SEQUEL,))
    damaged = holdings.source(audio(1, 2, "eng"))
    holdings.source(audio(1, 2, "eng"))
    listing = Inventory(TITLE.id, tuple(holdings.variants))
    held = {variant.path: variant for variant in holdings.variants}
    waiting = [{"lang": "fre", "state": MISSING_SOURCE}]
    outdated = {"inventory": "0123456789ab", "requirements": waiting}
    holdings.entries |= {
        delivered: {"sourcing": outdated},
        stale: {"sourcing": outdated},
        current: {
            "sourcing": {
                "inventory": sourcing.digest(listing, held[current]),
                "requirements": waiting,
            }
        },
        settled: {
            "sourcing": {
                "inventory": "0123456789ab",
                "requirements": [{"lang": "fre", "state": SATISFIED}],
            }
        },
        sequel: {"sourcing": outdated},
        damaged: "damaged",
    }
    audio_sources.changed(delivered, {})
    found = audio_sources.affected(delivered)
    assert [(item.variant.path, item.title) for item in found] == [(stale, TITLE)]
    # Listed afresh, since the delivery has just changed what the *arrs hold.
    assert holdings.listings == 2


def test_a_delivery_nobody_places_affects_nothing(holdings):
    delivered = holdings.target(listed=False)
    holdings.source(audio(1, 2, "eng"))
    assert audio_sources.affected(delivered) == []
    holdings.titled = False
    assert audio_sources.affected(delivered) == []
