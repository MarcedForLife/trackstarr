"""Environment parsing: malformed values must reach errors(), never raise.

The checks that need the rule and layout vocabulary live beside it, in
test_policy.py.
"""

import os

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


def test_a_boolean_typo_keeps_the_default_and_records_an_error(monkeypatch):
    """DRY_RUN read as false by a typo is the worst misread config could make:
    the owner believes nothing will be rewritten."""
    monkeypatch.setenv("DRY_RUN", "enalbed")
    monkeypatch.setenv("SKIP_HARDLINKS", "ture")
    assert config._bool("DRY_RUN") is False
    assert config._bool("SKIP_HARDLINKS", "true") is True
    errors = config.errors()
    assert len(errors) == 2
    assert "DRY_RUN" in errors[0]
    assert "SKIP_HARDLINKS" in errors[1]


@pytest.mark.parametrize("raw", ["1", "true", "Yes", " ON "])
def test_every_spelling_of_true_reads_as_true(monkeypatch, raw):
    monkeypatch.setenv("DRY_RUN", raw)
    assert config._bool("DRY_RUN") is True
    assert config.errors() == []


@pytest.mark.parametrize("raw", ["0", "false", "No", " OFF "])
def test_every_spelling_of_false_reads_as_false(monkeypatch, raw):
    monkeypatch.setenv("SKIP_HARDLINKS", raw)
    assert config._bool("SKIP_HARDLINKS", "true") is False
    assert config.errors() == []


def test_an_empty_boolean_reads_as_its_default(monkeypatch):
    """A leftover ``DRY_RUN: ${DRY_RUN}`` in a compose file expands to empty,
    which is a leftover, not a typo, and must not block startup."""
    monkeypatch.setenv("DRY_RUN", "")
    monkeypatch.setenv("SKIP_HARDLINKS", " ")
    assert config._bool("DRY_RUN") is False
    assert config._bool("SKIP_HARDLINKS", "true") is True
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
    """Which one is live would be invisible, and the wrong key looks exactly like
    a revoked one from the far end."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("from-the-file")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RADARR_API_KEY", "from-the-environment")
    assert config._secret("RADARR_API_KEY") == ""
    errors = config.errors()
    assert len(errors) == 1
    assert "RADARR_API_KEY_FILE" in errors[0] and "RADARR_API_KEY" in errors[0]


def test_a_credential_left_expanding_to_nothing_is_not_a_conflict(monkeypatch, tmp_path):
    """A forgotten ``RADARR_API_KEY: ${RADARR_API_KEY}`` line is a leftover, not an
    ambiguity, and must not block startup."""
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
    """A blank key reads the same as one never set, so the *arr would be silently
    disabled rather than reported."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("\n")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    assert config._secret("RADARR_API_KEY") == ""
    errors = config.errors()
    assert len(errors) == 1
    assert "is empty" in errors[0]


@pytest.mark.parametrize("value", ["04:00", "0 4 * *", "60 4 * * *", "0 4 * * mon"])
def test_a_bad_sweep_schedule_is_refused(monkeypatch, value):
    """The scheduler would otherwise die alone. "04:00" matters most: it is what
    SWEEP_AT took before it became a cron schedule."""
    monkeypatch.setattr(config, "SWEEP_AT", value)
    errors = config.errors()
    assert len(errors) == 1
    assert "SWEEP_AT" in errors[0]


@pytest.mark.parametrize("value", ["", "0 4 * * *", "*/30 * * * *", "0 6 * * 1-5"])
def test_a_valid_sweep_schedule_passes(monkeypatch, value):
    monkeypatch.setattr(config, "SWEEP_AT", value)
    assert config.errors() == []


@pytest.mark.parametrize(
    "raw",
    ["cover_art", "cover-art", "Cover-Art", " cover-art , sdh "],
    ids=["underscore", "dash", "mixed case", "spaced list"],
)
def test_a_rule_can_be_named_with_either_separator(monkeypatch, raw):
    """cover_art is the one rule name with a separator in it, so it is the
    one anybody has to guess at, and guessing wrong refuses to start."""
    monkeypatch.setenv("DISABLED_RULES", raw)
    assert "cover_art" in config._rules("DISABLED_RULES", "")
    # Still only a spelling: policy.errors() is what refuses a real typo,
    # and test_policy.py covers that.


@pytest.mark.parametrize("value", ["off", "none", "false", "no", "0", "", "  OFF  "])
def test_every_spelling_of_off_reads_as_unset(monkeypatch, value):
    """Startup refuses anything that is neither a mode nor off, so guessing one
    would be a failed boot rather than the default."""
    monkeypatch.setenv("REGENERATE_DOWNMIXES", value)
    assert config._mode("REGENERATE_DOWNMIXES") == ""


@pytest.mark.parametrize("value", ["generated", "ALL", " all "])
def test_a_named_mode_survives_normalisation(monkeypatch, value):
    """Case and spacing are this function's business; whether the name means
    anything is policy.errors()'."""
    monkeypatch.setenv("REGENERATE_DOWNMIXES", value)
    assert config._mode("REGENERATE_DOWNMIXES") == value.strip().lower()


def test_a_missing_media_dir_is_a_warning_not_an_error(monkeypatch, tmp_path):
    """Quiet otherwise: the sweep says so as it walks, hours later, and an install
    with no SWEEP_AT never walks at all."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path), str(tmp_path / "absent")])
    warnings = config.warnings()
    assert len(warnings) == 1
    assert str(tmp_path / "absent") in warnings[0]
    # A library mounted late, or a webhook-only install, must still start.
    assert config.errors() == []


def test_a_rate_for_a_layout_nobody_asked_for_is_a_warning(monkeypatch, tmp_path):
    """Half an edit: the variable added, the layout not. Silent otherwise."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "7.1": "1280k"})
    monkeypatch.setenv("AUDIO_BITRATE_7_1", "1280k")
    warnings = config.warnings()
    assert len(warnings) == 1
    assert "AUDIO_BITRATE_7_1" in warnings[0]


def test_dropping_a_default_layout_is_not_a_leftover(monkeypatch, tmp_path):
    """2.0 and 5.1 always carry a rate, so running one would otherwise report the
    other's default as an orphan every startup."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0"})
    monkeypatch.delenv("AUDIO_BITRATE_5_1", raising=False)
    assert config.warnings() == []


@pytest.mark.parametrize(
    ("variable", "name"),
    [("AUDIO_BITRATE_2_0", "2.0"), ("AUDIO_BITRATE_7_1", "7.1")],
)
def test_a_layout_rate_is_read_from_its_own_variable(monkeypatch, variable, name):
    """A dot is not allowed in an environment variable name, so the layout is
    spelled with underscores and mapped back here."""
    monkeypatch.setenv(variable, "448k")
    assert config.bitrate_variable(name) == variable
    assert config._bitrates()[name] == "448k"


def test_the_shipped_rates_apply_when_nothing_states_one(monkeypatch):
    monkeypatch.delenv("AUDIO_BITRATE_2_0", raising=False)
    monkeypatch.delenv("AUDIO_BITRATE_5_1", raising=False)
    assert config._bitrates() == {"2.0": "320k", "5.1": "640k"}


def _write_settings(tmp_path, monkeypatch, text: str) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    (tmp_path / config.SETTINGS_FILE).write_text(text)


def test_a_settings_file_value_is_read_when_the_environment_is_silent(monkeypatch):
    monkeypatch.delenv("HARDLINK_RECHECK", raising=False)
    monkeypatch.setattr(config, "_SETTINGS", {"HARDLINK_RECHECK": "600"})
    assert config._int("HARDLINK_RECHECK", "900") == 600
    assert config.errors() == []


def test_the_environment_beats_the_settings_file(monkeypatch):
    monkeypatch.setenv("HARDLINK_RECHECK", "300")
    monkeypatch.setattr(config, "_SETTINGS", {"HARDLINK_RECHECK": "600"})
    assert config._int("HARDLINK_RECHECK", "900") == 300


def test_a_blank_environment_value_yields_to_the_settings_file(monkeypatch):
    """A leftover ``DRY_RUN: ${DRY_RUN}`` expands to empty, which is a
    leftover, not a choice, and must not mask a setting the file states."""
    monkeypatch.setenv("DRY_RUN", "")
    monkeypatch.setattr(config, "_SETTINGS", {"DRY_RUN": "true"})
    assert config._bool("DRY_RUN") is True


def test_an_empty_media_dirs_still_means_no_dirs(monkeypatch):
    """MEDIA_DIRS="" is a webhook-only install's choice. With no settings-file
    entry behind it, it must keep meaning "walk nothing", never the shipped
    defaults."""
    monkeypatch.setenv("MEDIA_DIRS", "")
    assert config._list("MEDIA_DIRS", "/data/media/movies:/data/media/tv") == []


def test_settings_numbers_and_booleans_read_as_their_literals(tmp_path, monkeypatch):
    """A hand-written file naturally says 5120 and true; they arrive spelled
    the way the same-named variable would hold them."""
    _write_settings(tmp_path, monkeypatch, '{"LISTEN_PORT": 5120, "DRY_RUN": true}')
    assert config._load_settings() == {"LISTEN_PORT": "5120", "DRY_RUN": "true"}
    assert config.errors() == []


def test_a_structured_settings_value_is_refused(tmp_path, monkeypatch):
    """A dropped setting has to reach errors(), never quietly mean its
    default; the rest of the file still loads."""
    _write_settings(tmp_path, monkeypatch, '{"MEDIA_DIRS": ["/a"], "DRY_RUN": "true"}')
    assert config._load_settings() == {"DRY_RUN": "true"}
    errors = config.errors()
    assert len(errors) == 1
    assert "MEDIA_DIRS" in errors[0]


def test_an_unreadable_settings_file_is_refused(tmp_path, monkeypatch):
    """Silently ignored, a damaged file would run the library on defaults its
    owner did not choose."""
    _write_settings(tmp_path, monkeypatch, "{not json")
    assert config._load_settings() == {}
    errors = config.errors()
    assert len(errors) == 1
    assert config.SETTINGS_FILE in errors[0]


def test_a_settings_file_must_hold_one_object(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, '["DRY_RUN"]')
    assert config._load_settings() == {}
    assert len(config.errors()) == 1


def test_a_missing_settings_file_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    assert config._load_settings() == {}
    assert config.errors() == []


def _write_env(tmp_path, text: str) -> str:
    path = tmp_path / config.ENV_FILE
    path.write_text(text)
    return str(path)


def test_a_dotenv_value_fills_an_unset_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("WORK_DIR", raising=False)
    config._load_dotenv(_write_env(tmp_path, "WORK_DIR=./dev/data\n"))
    assert os.environ["WORK_DIR"] == "./dev/data"
    assert config.errors() == []


def test_a_dotenv_value_never_overwrites_the_environment(tmp_path, monkeypatch):
    """The environment is the deploy's word; a checkout's .env must not take it
    back, the same precedence _raw gives it over the settings file."""
    monkeypatch.setenv("WORK_DIR", "/data/trackstarr-work")
    config._load_dotenv(_write_env(tmp_path, "WORK_DIR=./dev/data\n"))
    assert os.environ["WORK_DIR"] == "/data/trackstarr-work"


def test_dotenv_skips_blanks_and_comments_and_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("WORK_DIR", raising=False)
    monkeypatch.delenv("STATE_DIR", raising=False)
    config._load_dotenv(
        _write_env(tmp_path, '\n# a comment\nWORK_DIR="./dev/data"\nSTATE_DIR=./dev/config\n')
    )
    assert os.environ["WORK_DIR"] == "./dev/data"
    assert os.environ["STATE_DIR"] == "./dev/config"
    assert config.errors() == []


def test_a_dotenv_line_without_an_equals_is_refused(tmp_path):
    """A typo has to reach errors(), never pass unseen, the same as a
    settings-file one."""
    config._load_dotenv(_write_env(tmp_path, "WORK_DIR\n"))
    errors = config.errors()
    assert len(errors) == 1
    assert "NAME=VALUE" in errors[0]


def test_a_missing_dotenv_is_silent(tmp_path):
    config._load_dotenv(str(tmp_path / "nope.env"))
    assert config.errors() == []


def test_an_unreadable_dotenv_is_refused(tmp_path):
    """A directory where the file should be raises OSError, not
    FileNotFoundError, and must be reported rather than crash import."""
    (tmp_path / config.ENV_FILE).mkdir()
    config._load_dotenv(str(tmp_path / config.ENV_FILE))
    errors = config.errors()
    assert len(errors) == 1
    assert config.ENV_FILE in errors[0]


def test_a_settings_key_nothing_reads_refuses_startup(monkeypatch):
    """Unlike the environment, the file's names are a closed set, so an
    unread key is a typo silently meaning its default. Refused like a
    DISABLED_RULES typo, and by errors() rather than warnings() so plan and
    fix, which read the same file, report it too."""
    monkeypatch.setattr(config, "_SETTINGS", {"MEDIA_DIRZ": "/data"})
    errors = config.errors()
    assert len(errors) == 1
    assert "MEDIA_DIRZ" in errors[0]


def test_a_settings_file_bitrate_is_read_and_not_flagged_unread(monkeypatch):
    """AUDIO_BITRATE_ names are read by prefix scan rather than by asking, so
    the unread-key check has to know they count as read."""
    monkeypatch.setattr(config, "_SETTINGS", {"AUDIO_BITRATE_7_1": "1280k"})
    assert config._bitrates()["7.1"] == "1280k"
    assert config.errors() == []


def test_an_environment_bitrate_beats_the_settings_file(monkeypatch):
    monkeypatch.setenv("AUDIO_BITRATE_5_1", "448k")
    monkeypatch.setattr(config, "_SETTINGS", {"AUDIO_BITRATE_5_1": "768k"})
    assert config._bitrates()["5.1"] == "448k"


def test_a_file_rate_for_a_layout_nobody_asked_for_is_a_warning(monkeypatch, tmp_path):
    """The same half-edit the environment check catches, stated in the file."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "7.1": "1280k"})
    monkeypatch.setattr(config, "_SETTINGS", {"AUDIO_BITRATE_7_1": "1280k"})
    warnings = config.warnings()
    assert len(warnings) == 1
    assert "AUDIO_BITRATE_7_1" in warnings[0]


def test_a_secret_can_come_from_the_settings_file(monkeypatch):
    monkeypatch.delenv("RADARR_API_KEY", raising=False)
    monkeypatch.setattr(config, "_SETTINGS", {"RADARR_API_KEY": " abc123 "})
    assert config._secret("RADARR_API_KEY") == "abc123"
    assert config.errors() == []


def test_an_environment_secret_beats_the_settings_file(monkeypatch):
    monkeypatch.setenv("RADARR_API_KEY", "from-the-environment")
    monkeypatch.setattr(config, "_SETTINGS", {"RADARR_API_KEY": "from-the-file"})
    assert config._secret("RADARR_API_KEY") == "from-the-environment"


@pytest.mark.parametrize("value", [0, -1])
def test_a_rewrite_budget_below_one_is_refused(monkeypatch, value):
    """Zero leaves the pool with no workers, so the sweep would hang rather
    than fail, the worse way to find out."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", value)
    errors = config.errors()
    assert len(errors) == 1
    assert "MAX_CONCURRENT_REWRITES" in errors[0]


@pytest.mark.parametrize("name", ["FFMPEG_TIMEOUT", "PROBE_TIMEOUT"])
def test_a_timeout_below_one_is_refused(monkeypatch, name):
    """Zero or negative fails every run as "timed out", hours after the
    restart that set it."""
    monkeypatch.setattr(config, name, 0)
    errors = config.errors()
    assert len(errors) == 1
    assert name in errors[0]
