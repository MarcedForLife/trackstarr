"""What main audio a target lacks and which variant should supply it, without any I/O."""

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction

import pytest

from conftest import (
    audio,
    probe_data,
    set_langs,
    set_layouts,
    set_rules,
    subtitle,
    video,
)
from trackstarr import inventory
from trackstarr.command import ffmpeg_args
from trackstarr.inventory import ContentKey, Inventory, Variant
from trackstarr.media import IMPORTED_TAG
from trackstarr.planner import FileRevision, PlanInput, TimingMapping, new_plan, plan_from_probe
from trackstarr.policy import Policy
from trackstarr.sourcing import (
    ANALYSIS_REQUIRED,
    COMPLETE,
    INCOMPLETE,
    MISSING_SOURCE,
    NEEDS_REVIEW,
    READY,
    SATISFIED,
    UNAVAILABLE,
    UNKNOWN,
    AudioRequirement,
    Resolution,
    SourcingAssessment,
    TrackRef,
    awaits_measurement,
    candidates,
    digest,
    open_requirements,
    requirements,
    tracks_in,
)

DUNE = ContentKey("tmdb", "movie", "438631")
SEQUEL = ContentKey("tmdb", "movie", "693134")
TITLE_ID = "arr:radarr:1"


def listed(path: str, instance_id: str, file_id: int, **fields) -> Variant:
    """A readable Dune file in one connection, its inode the file id."""
    revision = FileRevision(path, 1, file_id, 100, 1_000, 1_000)
    return replace(Variant(path, instance_id, 1, file_id, (DUNE,), revision=revision), **fields)


TARGET = listed("/movies/Dune.mkv", "radarr", 1)
OTHER = listed("/movies-4k/Dune.mkv", "radarr-4k", 2)


def _plan(*streams, original="eng"):
    return plan_from_probe(new_plan("/media/film.mkv", original), probe_data(video(), *streams))


@pytest.fixture
def sourcing_enabled():
    set_rules(source_audio="always")
    set_langs("eng:keep", "fre:keep", "original:keep")
    set_layouts()


def test_sourcing_defaults_off():
    assert "source_audio" in Policy.from_config().rules_in("never")
    assert requirements(_plan(audio(1, 2))) == ()


def test_requirements_use_main_audio_and_preserve_language_order(sourcing_enabled):
    plan = _plan(audio(1, 2, "fre"), original="jpn")
    assert requirements(plan) == (
        AudioRequirement("eng", False),
        AudioRequirement("fre", True),
        AudioRequirement("jpn", False),
    )


def test_original_resolves_without_repeating_an_explicit_requirement(sourcing_enabled):
    assert requirements(_plan(audio(1, 2, "eng"))) == (
        AudioRequirement("eng", True),
        AudioRequirement("fre", False),
    )


def test_unknown_original_applies_only_the_explicit_languages(sourcing_enabled):
    assert requirements(_plan(audio(1, 2, "eng"), original=None)) == (
        AudioRequirement("eng", True),
        AudioRequirement("fre", False),
    )


@pytest.mark.parametrize("marking", ["comment", "visual_impaired", "descriptions", "title"])
def test_retained_commentary_or_description_does_not_satisfy_a_requirement(
    sourcing_enabled, marking
):
    stream = audio(1, 2, "eng")
    if marking == "title":
        stream["tags"]["title"] = "Isolated Score"
    else:
        stream["disposition"][marking] = 1
    plan = _plan(stream)
    assert any(output.kind == "audio" for output in plan.streams)
    assert requirements(plan)[0] == AudioRequirement("eng", False)


def test_subtitles_and_untagged_audio_cannot_supply_a_language(sourcing_enabled):
    plan = _plan(audio(1, 2, None), subtitle(2, "eng"))
    assert requirements(plan)[0] == AudioRequirement("eng", False)


def test_original_tagging_counts_only_when_the_rule_acts(sourcing_enabled):
    set_rules(tag_original="alongside")
    plan = _plan(audio(1, 2, None))
    assert not plan.needed
    assert requirements(plan)[0] == AudioRequirement("eng", False)
    set_rules(tag_original="always")
    plan = _plan(audio(1, 2, None))
    assert plan.needed
    assert requirements(plan)[0] == AudioRequirement("eng", True)


def test_a_removed_layout_does_not_count(sourcing_enabled):
    set_layouts("7.1:remove")
    plan = _plan(audio(1, 2, "eng"), audio(2, 8, "fre"))
    assert requirements(plan)[1] == AudioRequirement("fre", False)


def test_a_surviving_downmix_satisfies_the_removed_source_language(sourcing_enabled):
    set_langs("eng", "fre")
    set_layouts("2.0", "7.1:remove")
    plan = _plan(audio(1, 2, "eng"), audio(2, 8, "fre"))
    assert any(stream.encode and stream.lang == "fre" for stream in plan.streams)
    assert requirements(plan)[1] == AudioRequirement("fre", True)


@pytest.mark.parametrize("mode", ["alongside", "always"])
def test_missing_audio_never_bootstraps_a_local_rewrite(sourcing_enabled, mode):
    set_rules(source_audio=mode)
    plan = _plan(audio(1, 2, "eng"))
    before = ffmpeg_args(plan, "output.mkv")
    assert requirements(plan)[1] == AudioRequirement("fre", False)
    assert not plan.needed
    assert not plan.reasons
    assert not plan.incidental
    assert ffmpeg_args(plan, "output.mkv") == before


def test_alongside_assesses_the_outputs_of_an_independent_rewrite(sourcing_enabled):
    set_rules(source_audio="alongside", languages="alongside")
    set_layouts("2.0")
    set_langs("eng", "fre:keep")
    plan = _plan(audio(1, 6, "eng"), audio(2, 2, "jpn"), original="jpn")
    assert plan.needed
    assert requirements(plan) == (AudioRequirement("eng", True), AudioRequirement("fre", False))


def test_external_inputs_cannot_satisfy_their_own_prerequisites(sourcing_enabled):
    plan = _plan(audio(1, 2))
    plan.inputs = (PlanInput(FileRevision("/source.mkv", 1, 2, 3, 4, 5), 1),)
    with pytest.raises(ValueError, match="local-only"):
        requirements(plan)


def test_requirements_are_immutable():
    requirement = AudioRequirement("eng", True)
    with pytest.raises(FrozenInstanceError):
        requirement.satisfied = False


def test_requirements_use_the_plans_policy_snapshot(sourcing_enabled):
    plan = _plan(audio(1, 2, "eng"))
    set_langs("jpn")
    assert requirements(plan) == (AudioRequirement("eng", True), AudioRequirement("fre", False))


@pytest.mark.parametrize(
    ("states", "completeness"),
    [
        ((), COMPLETE),
        ((SATISFIED, SATISFIED), COMPLETE),
        ((SATISFIED, MISSING_SOURCE), INCOMPLETE),
        ((READY, NEEDS_REVIEW), INCOMPLETE),
        ((MISSING_SOURCE, UNAVAILABLE), UNKNOWN),
    ],
)
def test_completeness_is_unknown_while_a_possible_source_could_not_be_read(
    states, completeness
):
    resolutions = tuple(
        Resolution(lang, state) for lang, state in zip(("eng", "fre"), states, strict=False)
    )
    assert SourcingAssessment(resolutions).completeness == completeness


def test_inputs_are_the_ready_imports_in_requirement_order():
    shift = TimingMapping(Fraction(1), 250_000)
    german = TrackRef(OTHER, OTHER.revision, audio(2, 6, "ger"))
    french = TrackRef(OTHER, OTHER.revision, audio(1, 2, "fre"))
    assessment = SourcingAssessment(
        (
            Resolution("eng", SATISFIED),
            Resolution("ger", READY, track=german, mapping=shift, measured=True),
            Resolution("jpn", ANALYSIS_REQUIRED, "timing not yet measured", french),
            Resolution("fre", READY, track=french, mapping=TimingMapping()),
        )
    )
    assert assessment.inputs == (
        PlanInput(OTHER.revision, 2, shift),
        PlanInput(OTHER.revision, 1, TimingMapping()),
    )


def test_a_projection_names_each_import_and_what_it_was_judged_against():
    untagged = {"index": 4, "codec_type": "audio", "codec_name": "flac"}
    assessment = SourcingAssessment(
        (
            Resolution("eng", SATISFIED),
            Resolution(
                "ger",
                READY,
                track=TrackRef(OTHER, OTHER.revision, untagged),
                mapping=TimingMapping(Fraction(25, 24), -40_000),
                measured=True,
            ),
            Resolution(
                "fre",
                ANALYSIS_REQUIRED,
                "timing not yet measured",
                TrackRef(OTHER, OTHER.revision, audio(3, 2, "fre", title="Stereo")),
            ),
        ),
        "0123456789ab",
        ({"status": "accepted"},),
    )
    assert assessment.project() == {
        "completeness": INCOMPLETE,
        "requirements": [
            {"lang": "eng", "state": SATISFIED},
            {
                "lang": "ger",
                "state": READY,
                "track": {
                    "path": "/movies-4k/Dune.mkv",
                    "instance_id": "radarr-4k",
                    "index": 4,
                    "codec": "flac",
                },
                "mapping": {"scale": "25/24", "offset_us": -40_000},
                "measured": True,
            },
            {
                "lang": "fre",
                "state": ANALYSIS_REQUIRED,
                "reason": "timing not yet measured",
                "track": {
                    "path": "/movies-4k/Dune.mkv",
                    "instance_id": "radarr-4k",
                    "index": 3,
                    "codec": "ac3",
                    "channels": 2,
                    "title": "Stereo",
                },
            },
        ],
        "inventory": "0123456789ab",
        "alignments": [{"status": "accepted"}],
    }


def test_a_projection_without_a_listing_or_measurements_leaves_them_out():
    blocked = SourcingAssessment(
        (Resolution("fre", NEEDS_REVIEW, "no connection lists this title"),)
    )
    assert blocked.project() == {
        "completeness": INCOMPLETE,
        "requirements": [
            {"lang": "fre", "state": NEEDS_REVIEW, "reason": "no connection lists this title"}
        ],
    }


@pytest.mark.parametrize(
    ("projection", "waiting"),
    [
        ({}, False),
        ({"requirements": None}, False),
        ({"requirements": ["damaged", {"lang": "eng", "state": SATISFIED}]}, False),
        ({"requirements": [{"lang": "eng", "state": SATISFIED}, {"lang": "fre"}]}, True),
        ({"requirements": [{"lang": "fre", "state": MISSING_SOURCE}]}, True),
        ({"requirements": [{"lang": "fre", "state": UNAVAILABLE}]}, True),
    ],
)
def test_only_a_requirement_the_target_does_not_settle_stays_open(projection, waiting):
    assert open_requirements(projection) is waiting


@pytest.mark.parametrize(
    ("projection", "waiting"),
    [
        ({}, False),
        ({"requirements": None}, False),
        ({"requirements": ["damaged", {"lang": "fre", "state": NEEDS_REVIEW}]}, False),
        (
            {"requirements": [{"lang": "eng"}, {"lang": "fre", "state": ANALYSIS_REQUIRED}]},
            True,
        ),
    ],
)
def test_only_a_source_still_to_be_measured_awaits_measurement(projection, waiting):
    assert awaits_measurement(projection) is waiting


def test_candidates_keep_inventory_order_and_need_exactly_the_same_identity():
    target = replace(TARGET, edition="IMAX")
    unnamed = OTHER
    same_cut = listed("/movies-imax/Dune.mkv", "radarr-imax", 3, edition="imax")
    held = (
        listed("/movies-cut/Dune.mkv", "radarr-cut", 4, edition="Director's Cut"),
        target,
        unnamed,
        listed("/movies/Dune Part Two.mkv", "radarr", 5, content_keys=(SEQUEL,)),
        listed("/movies/Dune/Dune.mkv", "radarr", 1),
        listed("/shared/Dune.mkv", "radarr-shared", 6, excluded=inventory.SHARED_FOLDER),
        listed("/movies-remote/Dune.mkv", "radarr-remote", 7, revision=None),
        same_cut,
    )
    assert candidates(Inventory(TITLE_ID, held), target) == [unnamed, same_cut]


@pytest.mark.parametrize(
    "target",
    [replace(TARGET, excluded=inventory.SAME_FILE), replace(TARGET, content_keys=())],
    ids=["in doubt", "without a provider id"],
)
def test_a_target_in_doubt_or_without_identity_has_no_candidates(target):
    assert candidates(Inventory(TITLE_ID, (target, OTHER)), target) == []


def test_tracks_rank_native_before_imported_then_by_channels():
    set_layouts("7.1:remove")
    imported = audio(2, 6, "fre")
    imported["tags"][IMPORTED_TAG] = "radarr-4k"
    description = audio(6, 6, "fre")
    description["disposition"]["visual_impaired"] = 1
    channelless = {"index": 10, "codec_type": "audio", "tags": {"language": "fre"}}
    info = probe_data(
        video(),
        audio(1, 2, "fre"),
        imported,
        audio(3, 6, "fre"),
        audio(4, 6, "fre"),
        audio(5, 6, "fre", comment=1),
        description,
        audio(7, 8, "fre"),
        audio(8, 6, "eng"),
        subtitle(9, "fre"),
        channelless,
    )
    ranked = tracks_in(info, "fre", Policy.from_config())
    assert [stream["index"] for stream in ranked] == [3, 4, 1, 10, 2]


def test_a_probe_without_streams_offers_no_tracks():
    assert tracks_in({}, "fre", Policy.from_config()) == []


@pytest.mark.parametrize(
    "change",
    [
        {"edition": "IMAX"},
        {"excluded": inventory.SHARED_FOLDER},
        {"revision": None},
        {"revision": FileRevision("/movies-4k/Dune.mkv", 1, 2, 100, 2_000, 2_000)},
        {"path": "/movies-4k/Dune (2021).mkv"},
    ],
)
def test_the_digest_follows_every_file_sharing_the_targets_identity(change):
    sequel = listed("/movies/Dune Part Two.mkv", "radarr", 5, content_keys=(SEQUEL,))
    before = digest(Inventory(TITLE_ID, (TARGET, OTHER, sequel)), TARGET)
    after = digest(Inventory(TITLE_ID, (TARGET, replace(OTHER, **change), sequel)), TARGET)
    assert len(before) == 12 and after != before


def test_the_digest_ignores_other_content_and_the_order_connections_failed_in():
    sequel = listed("/movies/Dune Part Two.mkv", "radarr", 5, content_keys=(SEQUEL,))
    held = (TARGET, OTHER, sequel)
    down = ("radarr-remote", "radarr-anime")
    before = digest(Inventory(TITLE_ID, held, down), TARGET)
    renamed = replace(sequel, path="/movies/Dune Messiah.mkv")
    assert digest(Inventory(TITLE_ID, (TARGET, OTHER, renamed), down[::-1]), TARGET) == before
    assert digest(Inventory(TITLE_ID, held), TARGET) != before


def test_without_a_target_the_digest_follows_only_unavailable_connections():
    assert digest(Inventory(TITLE_ID, (TARGET, OTHER)), None) == digest(
        Inventory(TITLE_ID, ()), None
    )
    assert digest(Inventory(TITLE_ID, (), ("radarr-4k",)), None) != digest(
        Inventory(TITLE_ID, ()), None
    )


def test_the_digest_leaves_the_targets_own_contents_to_its_cache_key():
    rewritten = replace(
        TARGET, revision=FileRevision("/movies/Dune.mkv", 1, 9, 300, 9_000, 9_000)
    )
    before = digest(Inventory(TITLE_ID, (TARGET, OTHER)), TARGET)
    assert digest(Inventory(TITLE_ID, (rewritten, OTHER)), TARGET) == before
