"""Environment parsing: malformed values must reach errors(), never raise.

The checks that need the rule and layout vocabulary live beside it, in
test_policy.py.
"""

import pytest

from trackstarr import config


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
    assert config._int("LISTEN_PORT", "5120") == 9090
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


@pytest.mark.parametrize("variable", ["RADARR_API_KEY_FILE", "FILE__RADARR_API_KEY"])
def test_a_credential_can_come_from_a_file(monkeypatch, tmp_path, variable):
    """Both conventions, because every *arr beside us uses one or the other."""
    secret_file = tmp_path / "radarr_api_key"
    # Trailing newline included: echo > file puts one there, and it would
    # otherwise be sent as part of the key and rejected as a bad one.
    secret_file.write_text("abc123\n")
    monkeypatch.setenv(variable, str(secret_file))
    assert config._secret("RADARR_API_KEY") == "abc123"
    assert config.errors() == []


def test_a_credential_still_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("RADARR_API_KEY", " abc123 ")
    assert config._secret("RADARR_API_KEY") == "abc123"
    assert config.errors() == []


def test_an_unset_credential_is_blank(monkeypatch):
    monkeypatch.delenv("RADARR_API_KEY", raising=False)
    assert config._secret("RADARR_API_KEY") == ""
    assert config.errors() == []


def test_naming_a_credential_two_ways_is_refused(monkeypatch, tmp_path):
    """Which one is live would otherwise be invisible, and the wrong key
    looks exactly like a revoked one from the far end."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("from-the-file")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RADARR_API_KEY", "from-the-environment")
    assert config._secret("RADARR_API_KEY") == ""
    errors = config.errors()
    assert len(errors) == 1
    assert "RADARR_API_KEY_FILE" in errors[0] and "RADARR_API_KEY" in errors[0]


def test_a_credential_left_expanding_to_nothing_is_not_a_conflict(monkeypatch, tmp_path):
    """A forgotten RADARR_API_KEY: ${RADARR_API_KEY} line expands to empty.
    That is a leftover, not an ambiguity, and must not block startup."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("from-the-file")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RADARR_API_KEY", "")
    assert config._secret("RADARR_API_KEY") == "from-the-file"
    assert config.errors() == []


def test_an_unreadable_credential_file_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(tmp_path / "absent"))
    assert config._secret("RADARR_API_KEY") == ""
    errors = config.errors()
    assert len(errors) == 1
    assert "RADARR_API_KEY_FILE" in errors[0]


def test_an_empty_credential_file_is_refused(monkeypatch, tmp_path):
    """A blank key reads the same as one never set, so the *arr would be
    silently disabled rather than reported as misconfigured."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("\n")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    assert config._secret("RADARR_API_KEY") == ""
    errors = config.errors()
    assert len(errors) == 1
    assert "is empty" in errors[0]


@pytest.mark.parametrize("value", ["04:00", "0 4 * *", "60 4 * * *", "0 4 * * mon"])
def test_a_bad_sweep_schedule_is_refused(monkeypatch, value):
    """The scheduler would otherwise die alone; "04:00" matters most, it is
    the format SWEEP_AT took before it became a cron schedule."""
    monkeypatch.setattr(config, "SWEEP_AT", value)
    errors = config.errors()
    assert len(errors) == 1
    assert "SWEEP_AT" in errors[0]


@pytest.mark.parametrize("value", ["", "0 4 * * *", "*/30 * * * *", "0 6 * * 1-5"])
def test_a_valid_sweep_schedule_passes(monkeypatch, value):
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
