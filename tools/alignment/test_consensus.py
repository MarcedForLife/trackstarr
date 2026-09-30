"""Consensus recovers real mappings and sends ambiguity, cuts and wrong pictures to review."""

import pytest
from consensus import match_consensus
from test_visual import frames
from visual import Frame


def shifted(source, scale=1.0, offset_us=0):
    return [
        Frame(round(f.time_us * scale) + offset_us, f.confidence, f.lower, f.upper)
        for f in source
    ]


def test_recovers_speed_offset_and_nonzero_timestamps():
    source = frames()
    result = match_consensus(shifted(source, 25 / 24, 1_250_000), source)
    assert result["status"] == "proposed"
    assert result["mapping"]["scale"] == pytest.approx(25 / 24, abs=1e-6)
    assert result["mapping"]["offset_us"] == pytest.approx(1_250_000, abs=10)
    assert result["region_verdicts"] == ["confirms"] * 8
    assert {f["hypothesis"]["scale"] for f in result["folds"]} == {"25/24"}


def test_each_region_is_checked_only_by_the_fold_that_did_not_fit_it():
    source = frames()
    result = match_consensus(shifted(source, offset_us=500_000), source)
    fit_regions = [f["fit_parity"] for f in result["folds"]]
    assert fit_regions == [0, 1]
    assert result["folds"][0]["fit_probes"] + result["folds"][1]["fit_probes"] == len(source)
    assert result["confirmed_pairs"] == len(source)


def test_static_middle_regions_are_uninformative_not_contradicting():
    source = frames()
    still = source[60]
    # Regions 2 and 5 of 8 hold one repeated picture, as a static scene would.
    source = [
        Frame(f.time_us, f.confidence, still.lower, still.upper)
        if 48 <= i < 72 or 120 <= i < 144
        else f
        for i, f in enumerate(source)
    ]
    result = match_consensus(shifted(source), source)
    assert result["status"] == "proposed"
    assert result["region_verdicts"][2] == "insufficient"
    assert result["region_verdicts"][5] == "insufficient"


def test_static_opening_leaves_the_start_unconfirmed():
    source = frames()
    still = source[60]
    source = [
        Frame(f.time_us, f.confidence, still.lower, still.upper) if i < 24 else f
        for i, f in enumerate(source)
    ]
    result = match_consensus(shifted(source), source)
    assert result["reason"] == "unconfirmed_ends"


def test_fully_repeated_content_is_an_ambiguous_mapping():
    source = frames()
    target = source + shifted(source, offset_us=19_200_000)
    result = match_consensus(target, source)
    assert result["reason"] == "ambiguous_mapping"


@pytest.mark.parametrize("negative", ["reverse", "cut", "black", "unrelated"])
def test_wrong_picture_or_cut_never_proposed(negative):
    target = frames()
    if negative == "reverse":
        source = [
            Frame(f.time_us, g.confidence, g.lower, g.upper)
            for f, g in zip(target, reversed(target), strict=True)
        ]
    elif negative == "cut":
        source = [
            Frame(f.time_us - (2_000_000 if i >= 96 else 0), f.confidence, f.lower, f.upper)
            for i, f in enumerate(target)
            if not 76 <= i < 96
        ]
    elif negative == "black":
        source = [Frame(f.time_us, 0, 0, 0) for f in target]
    else:
        source = [Frame(f.time_us, 50, f.upper, f.lower) for f in target]
    assert match_consensus(target, source)["status"] == "review_required"


def test_internal_cut_is_reported_as_a_contradiction_or_disagreement():
    target = frames()
    source = [
        Frame(f.time_us - (2_000_000 if i >= 96 else 0), f.confidence, f.lower, f.upper)
        for i, f in enumerate(target)
        if not 76 <= i < 96
    ]
    result = match_consensus(target, source)
    assert result["reason"] in {"contradicting_region", "folds_disagree", "ambiguous_mapping"}


@pytest.mark.parametrize(("first", "last"), [(10, 20), (180, 188), (186, 188), (100, 101)])
def test_cuts_near_the_ends_or_one_frame_long_are_caught(first, last):
    target = frames()
    removed = (last - first) * 100_000
    source = [
        Frame(f.time_us - (removed if i >= last else 0), f.confidence, f.lower, f.upper)
        for i, f in enumerate(target)
        if not first <= i < last
    ]
    result = match_consensus(target, source)
    assert result["reason"] in {"contradicting_region", "folds_disagree"}


@pytest.mark.parametrize(("period", "status"), [(4, "proposed"), (2, "review_required")])
def test_frames_tied_with_a_neighbour_show_coverage_but_not_timing(period, status):
    original = frames()
    # A repeated frame, as a slow scene gives at 24 fps. With period 2 nothing pins the time.
    source = [
        Frame(f.time_us, f.confidence, original[i - 1].lower, original[i - 1].upper)
        if i % period == 1
        else f
        for i, f in enumerate(original)
    ]
    result = match_consensus(shifted(source, offset_us=300_000), source)
    assert result["status"] == status
    assert result["confirmed_pairs"] == len(source)
    assert result["exact_anchors"] == (len(source) // 2 if period == 4 else 0)
    if status == "proposed":
        assert result["mapping"]["offset_us"] == pytest.approx(300_000, abs=1)
    else:
        assert result["reason"] == "inconsistent_timeline"
