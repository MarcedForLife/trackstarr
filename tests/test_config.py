"""Environment parsing: malformed values must reach errors(), never raise."""

from __future__ import annotations

import pytest

from trackstarr import config, policy


@pytest.fixture(autouse=True)
def _clean_load_errors(monkeypatch):
    monkeypatch.setattr(config, "_LOAD_ERRORS", [])


def test_bad_int_keeps_default_and_records_error(monkeypatch):
    monkeypatch.setenv("HARDLINK_RECHECK", "soon")
    assert config._int("HARDLINK_RECHECK", "900") == 900
    assert any("HARDLINK_RECHECK" in problem for problem in config.errors())


def test_bad_regex_keeps_default_and_records_error(monkeypatch):
    monkeypatch.setenv("SDH_PATTERN", "(unclosed")
    pattern = config._regex("SDH_PATTERN", r"\bsdh\b")
    assert pattern.search("SDH")
    assert any("SDH_PATTERN" in problem for problem in config.errors())


def test_good_values_record_nothing(monkeypatch):
    monkeypatch.setenv("LISTEN_PORT", "9090")
    monkeypatch.setenv("FORCED_PATTERN", "forced|signs")
    assert config._int("LISTEN_PORT", "8080") == 9090
    assert config._regex("FORCED_PATTERN", r"\bforced\b").search("signs")
    assert config.errors() == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eng", {"eng"}),  # already 639-2/B
        ("en", {"eng"}),  # 639-1
        ("English", {"eng"}),  # an *arr-style name
        ("fra", {"fre"}),  # 639-2/T
        ("en,Japanese,kor", {"eng", "jpn", "kor"}),
        ("klingon", {"klingon"}),  # unrecognised: kept verbatim, matches nothing
    ],
)
def test_always_keep_entries_normalise_to_639_2b(monkeypatch, raw, expected):
    monkeypatch.setenv("ALWAYS_KEEP_LANGS", raw)
    assert config._langs("ALWAYS_KEEP_LANGS", "eng") == expected


def test_unknown_rule_names_are_refused(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"languages", "subtitles"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "subtitles" in errors[0]


def test_unknown_regenerate_mode_is_refused(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "everything")
    errors = policy.errors()
    assert len(errors) == 1
    assert "REGENERATE_DOWNMIXES" in errors[0]


@pytest.mark.parametrize("value", ["4am", "0400", "24:30", "12:60", "not-a-time"])
def test_a_bad_sweep_time_is_refused(monkeypatch, value):
    """The scheduler would otherwise die alone ("not-a-time") or mktime would
    quietly normalise the value ("24:30" sweeps at 00:30)."""
    monkeypatch.setattr(config, "SWEEP_AT", value)
    errors = config.errors()
    assert len(errors) == 1
    assert "SWEEP_AT" in errors[0]


@pytest.mark.parametrize("value", ["", "04:00", "4:05", "23:59"])
def test_a_valid_sweep_time_passes(monkeypatch, value):
    monkeypatch.setattr(config, "SWEEP_AT", value)
    assert config.errors() == []


@pytest.mark.parametrize("value", [0, -1])
def test_a_rewrite_budget_below_one_is_refused(monkeypatch, value):
    """Zero leaves the pool with no workers, so the sweep would hang rather
    than fail, which is the worse way to find out."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", value)
    errors = config.errors()
    assert len(errors) == 1
    assert "MAX_CONCURRENT_REWRITES" in errors[0]
