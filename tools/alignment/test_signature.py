"""The native report must never be promoted to a verified timeline mapping."""

import pytest
from signature import diagnose, parse_measurement


def test_trim_offset_and_seconds_conversion():
    measured = parse_measurement(
        "[Parsed_signature_0] matching of video 0 at 1.292000 and 1 at 0.042000, "
        "1410 frames matching\nwhole video matching\n"
    )
    case = {"expected": {"scale": "1", "offset_us": 1_250_000}}
    result = diagnose(case, measured)
    assert measured["target_time_us"] == 1_292_000
    assert measured["source_time_us"] == 42_000
    assert result["observed_target_minus_source_us"] == 1_250_000
    assert result["ground_truth_pair_error_us"] == 0
    assert result["outcome"] == "single_pair_only"


def test_speed_is_only_scored_against_truth_not_inferred():
    measured = parse_measurement(
        "matching of video 0 at 25.000000 and 1 at 24.000000, 400 frames matching"
    )
    result = diagnose({"expected": {"scale": "25/24", "offset_us": 0}}, measured)
    assert result["ground_truth_pair_error_us"] == 0
    assert result["outcome"] == "single_pair_only"
    assert "scale" not in measured and "scale" not in result


def test_whole_match_is_not_acceptance_on_negative():
    measured = parse_measurement(
        "matching of video 0 at 0.000000 and 1 at 0.000000, 1400 frames matching\n"
        "whole video matching"
    )
    assert diagnose({"expected": None}, measured)["outcome"] == "match_on_negative_case"


def test_explicit_no_match():
    measured = parse_measurement("no matching of video 0 and 1")
    assert measured == {"matched": False}
    assert diagnose({"expected": None}, measured)["outcome"] == "no_match"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "whole video matching",
        "matching of video 0 at nan and 1 at 0.000000, 3 frames matching",
        "matching of video 0 at 0.000000 and 1 at 0.000000, 0 frames matching",
        "matching of video 1 at 0.000000 and 0 at 0.000000, 3 frames matching",
        "matching of video 0 at 0.000000 and 1 at 0.000000, 3 frames matching\n" * 2,
        (
            "matching of video 0 at 0.000000 and 1 at 0.000000, 3 frames matching\n"
            "no matching of video 0 and 1"
        ),
    ],
)
def test_incomplete_or_ambiguous_reports(raw):
    with pytest.raises(ValueError):
        parse_measurement(raw)
