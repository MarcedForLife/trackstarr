"""Temporal context must preserve ambiguity and held-out timing checks."""

import pytest
from temporal import match_temporal, window
from test_visual import frames
from visual import Frame, match


def test_sequence_recovers_speed_offset_and_nonzero_timestamps():
    source = frames()
    target = [
        Frame(round(f.time_us * 25 / 24) + 1_250_000, f.confidence, f.lower, f.upper)
        for f in source
    ]
    result = match_temporal(target, source)
    assert result["status"] == "proposed"
    assert result["mapping"]["scale"] == pytest.approx(25 / 24, abs=1e-7)
    assert result["mapping"]["offset_us"] == pytest.approx(1_250_000, abs=1)
    assert result["max_validation_residual_us"] < 1


def test_context_resolves_repeated_frames_with_different_neighbours():
    source = frames()
    # A second occurrence of every frame, in a different temporal order.
    distractors = [
        Frame(20_000_000 + i * 100_000, f.confidence, f.lower, f.upper)
        for i, f in enumerate(reversed(source))
    ]
    target = source + distractors
    assert not match(target, source)["anchors"]
    result = match_temporal(target, source)
    assert len(result["anchors"]) >= 40
    assert all(a["target_time_us"] == a["source_time_us"] for a in result["anchors"])
    # Local correspondence cannot authorise a mapping covering half the target.
    assert result["reason"] == "insufficient_coverage"


@pytest.mark.parametrize("negative", ["repeat", "static", "reverse", "cut", "black"])
def test_temporal_negatives_require_review(negative):
    target = frames()
    source = target
    if negative == "repeat":
        target = target + [
            Frame(f.time_us + 19_200_000, f.confidence, f.lower, f.upper) for f in target
        ]
    elif negative == "static":
        source = target = [
            Frame(f.time_us, 50, target[0].lower, target[0].upper) for f in target
        ]
    elif negative == "reverse":
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
    else:
        source = [Frame(f.time_us, 0, 0, 0) for f in target]
    assert match_temporal(target, source)["status"] == "review_required"


def test_context_does_not_bridge_timestamp_gaps_or_clamp_edges():
    source = frames()
    times = [f.time_us for f in source]
    assert not window(source, times, 0, 1)
    assert not window(source, times, times[-1], 1)
    sparse = source[:80] + source[90:]
    assert not window(sparse, [f.time_us for f in sparse], 8_500_000, 1)


def test_validation_search_cannot_follow_fit_through_an_internal_shift():
    source = frames()
    target = [
        Frame(f.time_us + (200_000 if 48 <= i < 70 else 0), f.confidence, f.lower, f.upper)
        for i, f in enumerate(source)
    ]
    target.sort(key=lambda f: f.time_us)
    result = match_temporal(target, source)
    assert result["status"] == "review_required"
    assert result["independent_validation_search"]
    assert result["max_validation_residual_us"] > 50_000
