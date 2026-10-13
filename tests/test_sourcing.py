"""Requested main audio is assessed independently of local rewrite reasons."""

from dataclasses import FrozenInstanceError

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
from trackstarr.command import ffmpeg_args
from trackstarr.planner import FileRevision, PlanInput, new_plan, plan_from_probe
from trackstarr.policy import Policy
from trackstarr.sourcing import AudioRequirement, requirements


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
