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
    """SKIP_HARDLINKS read as false by a typo is among the worst misreads
    config could make: every rewrite breaks a seeding torrent's hard link and
    the file costs disk twice."""
    monkeypatch.setenv("IMDB_RATINGS", "enalbed")
    monkeypatch.setenv("SKIP_HARDLINKS", "ture")
    assert config._bool("IMDB_RATINGS") is False
    assert config._bool("SKIP_HARDLINKS", "true") is True
    errors = config.errors()
    assert len(errors) == 2
    assert "IMDB_RATINGS" in errors[0]
    assert "SKIP_HARDLINKS" in errors[1]


@pytest.mark.parametrize("raw", ["1", "true", "Yes", " ON "])
def test_every_spelling_of_true_reads_as_true(monkeypatch, raw):
    monkeypatch.setenv("SKIP_HARDLINKS", raw)
    assert config._bool("SKIP_HARDLINKS") is True
    assert config.errors() == []


@pytest.mark.parametrize("raw", ["0", "false", "No", " OFF "])
def test_every_spelling_of_false_reads_as_false(monkeypatch, raw):
    monkeypatch.setenv("SKIP_HARDLINKS", raw)
    assert config._bool("SKIP_HARDLINKS", "true") is False
    assert config.errors() == []


def test_an_empty_boolean_reads_as_its_default(monkeypatch):
    """A leftover ``IMDB_RATINGS: ${IMDB_RATINGS}`` in a compose
    file expands to empty, which is a leftover, not a typo, and must not block
    startup."""
    monkeypatch.setenv("IMDB_RATINGS", "")
    monkeypatch.setenv("SKIP_HARDLINKS", " ")
    assert config._bool("IMDB_RATINGS") is False
    assert config._bool("SKIP_HARDLINKS", "true") is True
    assert config.errors() == []


def test_the_language_list_keeps_its_written_order(monkeypatch):
    """Order is the downmix source preference, so it cannot be a set."""
    monkeypatch.setenv("LANGUAGES", "original, jpn:keep ,eng")
    assert config._ordered("LANGUAGES", "") == ("original", "jpn:keep", "eng")


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
    "variable",
    ["RULE_COVER_ART", "RULE_COVER-ART"],
    ids=["underscore", "dash"],
)
def test_a_rule_variable_can_be_named_either_way(monkeypatch, variable):
    """cover_art is the one rule name with a separator in it, so it is the one
    anybody has to guess at, and guessing wrong refuses to start."""
    monkeypatch.setenv(variable, "  Alongside ")
    assert config._rule_modes()["cover_art"] == "alongside"
    # Still only a spelling: policy.errors() is what refuses a real typo,
    # and test_policy.py covers that.


def test_a_rule_variable_stating_nothing_leaves_the_default(monkeypatch):
    """A leftover ``RULE_SDH: ${RULE_SDH}`` in a compose file expands to
    empty, which must not read as a mode nobody chose."""
    monkeypatch.setenv("RULE_SDH", "  ")
    assert "sdh" not in config._rule_modes()


def test_the_environment_wins_over_the_file_for_one_rule(monkeypatch):
    monkeypatch.setattr(config, "_SETTINGS", {"RULE_REMUX": "always"})
    monkeypatch.setenv("RULE_REMUX", "never")
    assert config._rule_modes()["remux"] == "never"


def test_a_missing_media_dir_is_a_warning_not_an_error(monkeypatch, tmp_path):
    """Quiet otherwise: the sweep says so as it walks, hours later, and an install
    with no SWEEP_AT never walks at all."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path), str(tmp_path / "absent")])
    warnings = config.media_dir_warnings()
    assert len(warnings) == 1
    assert str(tmp_path / "absent") in warnings[0]
    # A library mounted late, or a webhook-only install, must still start.
    assert config.errors() == []


def test_a_layout_carries_its_own_encoder_and_rate(monkeypatch):
    """One variable holds the whole audio policy, so a rate cannot be left
    beside a layout nothing makes."""
    monkeypatch.setenv("AUDIO_LAYOUTS", "2.0:libopus:192k,7.1:remove")
    assert config._ordered("AUDIO_LAYOUTS", "") == ("2.0:libopus:192k", "7.1:remove")


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
    """A leftover ``SKIP_HARDLINKS: ${SKIP_HARDLINKS}`` expands to empty, which is
    a leftover, not a choice, and must not mask a setting the file states."""
    monkeypatch.setenv("SKIP_HARDLINKS", "")
    monkeypatch.setattr(config, "_SETTINGS", {"SKIP_HARDLINKS": "true"})
    assert config._bool("SKIP_HARDLINKS") is True


def test_an_empty_media_dirs_still_means_no_dirs(monkeypatch):
    """MEDIA_DIRS="" is a webhook-only install's choice. With no settings-file
    entry behind it, it must keep meaning "walk nothing", never the shipped
    defaults."""
    monkeypatch.setenv("MEDIA_DIRS", "")
    assert config._list("MEDIA_DIRS", "/data/media/movies:/data/media/tv") == []


def test_a_path_map_reads_longest_prefix_first(monkeypatch):
    """Order is the whole contract: /data/media/tv has to win over /data/media
    or every episode would be mapped to the movies mount."""
    monkeypatch.setenv(
        "PLEX_PATH_MAP", "/data/media=/mnt/content/media, /data/media/tv/=/mnt/tv/"
    )
    assert config._path_map("PLEX_PATH_MAP") == [
        ("/data/media/tv", "/mnt/tv"),
        ("/data/media", "/mnt/content/media"),
    ]
    assert config.errors() == []


def test_a_half_written_path_map_entry_is_refused(monkeypatch):
    """Refreshes are best effort, so a pair missing its remote half would
    otherwise be a silent no-op for that library."""
    monkeypatch.setenv("PLEX_PATH_MAP", "/data/media,/data/tv=/mnt/tv")
    assert config._path_map("PLEX_PATH_MAP") == [("/data/tv", "/mnt/tv")]
    assert any("PLEX_PATH_MAP" in problem for problem in config.errors())


def test_no_path_map_is_no_mapping(monkeypatch):
    monkeypatch.delenv("PLEX_PATH_MAP", raising=False)
    assert config._path_map("PLEX_PATH_MAP") == []
    assert config.errors() == []


def test_an_ordered_setting_keeps_what_was_written(monkeypatch):
    """AUDIO_LAYOUTS is read in the order it was written, since that order
    is what the order rule lays the audio tracks out in."""
    monkeypatch.setenv("AUDIO_LAYOUTS", " 5.1 , 2.0 ,7.1")
    assert config._ordered("AUDIO_LAYOUTS", "") == ("5.1", "2.0", "7.1")


def test_an_ordered_setting_keeps_a_repeat_where_it_first_appeared(monkeypatch):
    """Two spellings of one position mean nothing; the later would win the
    dicts built from this and silently move the track."""
    monkeypatch.setenv("AUDIO_LAYOUTS", "5.1,2.0,5.1")
    assert config._ordered("AUDIO_LAYOUTS", "") == ("5.1", "2.0")


def test_settings_numbers_and_booleans_read_as_their_literals(tmp_path, monkeypatch):
    """A hand-written file naturally says 5120 and true; they arrive spelled
    the way the same-named variable would hold them."""
    _write_settings(tmp_path, monkeypatch, '{"LISTEN_PORT": 5120, "SKIP_HARDLINKS": true}')
    assert config._load_settings() == {"LISTEN_PORT": "5120", "SKIP_HARDLINKS": "true"}
    assert config.errors() == []


def test_a_structured_settings_value_is_refused(tmp_path, monkeypatch):
    """A dropped setting has to reach errors(), never quietly mean its
    default; the rest of the file still loads."""
    _write_settings(tmp_path, monkeypatch, '{"MEDIA_DIRS": ["/a"], "SKIP_HARDLINKS": "true"}')
    assert config._load_settings() == {"SKIP_HARDLINKS": "true"}
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
    _write_settings(tmp_path, monkeypatch, '["SKIP_HARDLINKS"]')
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


def test_a_dotenv_hands_back_what_it_held(tmp_path, monkeypatch):
    """TZ is read before .env can be loaded, since applying a saved zone means
    writing the variable itself, so STATED_TZ asks the file rather than the
    environment it has just filled in."""
    monkeypatch.delenv("TZ", raising=False)
    held = config._load_dotenv(_write_env(tmp_path, "TZ=Pacific/Auckland\n# a comment\n"))
    assert held == {"TZ": "Pacific/Auckland"}


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


def test_a_settings_key_nothing_reads_is_a_warning_not_an_error(monkeypatch):
    """An unread key is a typo silently meaning its default, but everything else
    still applies. A name retired under a running install would otherwise refuse
    every start."""
    monkeypatch.setattr(config, "_SETTINGS", {"MEDIA_DIRZ": "/data"})
    assert config.errors() == []
    (problem,) = config.warnings()
    assert "MEDIA_DIRZ" in problem
    # The settings pages write known names only, so they cannot clear this one:
    # the line has to carry the file's whole path and what to do with it.
    assert config._settings_path() in problem
    assert "remove or rename them" in problem


def test_a_settings_file_layout_list_is_read_and_not_flagged_unread(monkeypatch):
    """One name holds the whole audio policy now, so the unread-key check sees
    it asked for like any other setting."""
    monkeypatch.setattr(config, "_SETTINGS", {"AUDIO_LAYOUTS": "2.0,7.1:aac:1280k"})
    assert config._ordered("AUDIO_LAYOUTS", "") == ("2.0", "7.1:aac:1280k")
    assert config.errors() == []


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


@pytest.mark.parametrize(
    "name", ["RADARR_URL", "SONARR_URL", "PLEX_URL", "JELLYFIN_URL", "WEBHOOK_URL"]
)
def test_an_address_without_a_scheme_is_refused(monkeypatch, name):
    """A scheme is the one thing a hand-typed address always loses, and
    urllib's complaint about it lands on a background thread once per call."""
    monkeypatch.setattr(config, name, "radarr:7878")
    errors = config.errors()
    assert len(errors) == 1
    assert name in errors[0]


def test_an_address_with_a_scheme_records_nothing(monkeypatch):
    monkeypatch.setattr(config, "PLEX_URL", "https://plex.example.com")
    assert config.errors() == []


@pytest.mark.parametrize(
    ("set_name", "unset_name"),
    [("PLEX_URL", "PLEX_TOKEN"), ("SONARR_API_KEY", "SONARR_URL")],
)
def test_half_a_service_is_a_warning_naming_both_halves(monkeypatch, set_name, unset_name):
    """Either half alone leaves the service silently off. A warning rather
    than an error: clearing an address is how one is switched off."""
    monkeypatch.setattr(config, set_name, "set")
    (problem,) = config.warnings()
    assert set_name in problem
    assert unset_name in problem


# REWRITE_MODE, the ladder that replaced the DRY_RUN and SWEEP_APPLY pair.


@pytest.mark.parametrize("value", ["report", "IMPORTS", " all "])
def test_every_rung_of_the_ladder_is_read(monkeypatch, value):
    monkeypatch.setenv("REWRITE_MODE", value)
    assert (
        config._choice("REWRITE_MODE", "imports", config.REWRITE_MODES) == value.strip().lower()
    )
    assert config.errors() == []


def test_a_mode_typo_is_refused_rather_than_read_as_the_default(monkeypatch):
    """Read as its default, a typo would put a library its owner had asked to
    be reported on back on the rung that rewrites imports."""
    monkeypatch.setenv("REWRITE_MODE", "reprot")
    assert config._choice("REWRITE_MODE", "imports", config.REWRITE_MODES) == "imports"
    (problem,) = config.errors()
    assert "reprot" in problem
    assert "report, imports, all" in problem


def test_an_empty_mode_reads_as_its_default(monkeypatch):
    """A leftover ``REWRITE_MODE: ${REWRITE_MODE}`` expands to empty, which is
    a leftover, not a typo, and must not block startup."""
    monkeypatch.setenv("REWRITE_MODE", "  ")
    assert config._choice("REWRITE_MODE", "imports", config.REWRITE_MODES) == "imports"
    assert config.errors() == []


def test_the_probe_pool_no_longer_borrows_the_rewrite_budget():
    """Two settings that used to be one: probing is short and IO-bound where a
    rewrite is long and disk-bound, so the disk that wants one rewrite at a
    time still wants several probes."""
    assert config.PROBE_WORKERS == 4
    assert config.MAX_CONCURRENT_REWRITES == 1


@pytest.mark.parametrize("name", ["MAX_CONCURRENT_REWRITES", "PROBE_WORKERS"])
def test_a_pool_below_one_is_refused(monkeypatch, name):
    """A rewrite budget of zero hangs the slot loop; a probe pool of zero is a
    ThreadPoolExecutor that refuses to start."""
    monkeypatch.setattr(config, name, 0)
    assert any(name in problem for problem in config.errors())


@pytest.mark.parametrize("percent", [9, 91])
def test_a_low_bitrate_threshold_outside_the_band_is_refused(monkeypatch, percent):
    """Near the rate itself every honest variable-rate track reads as low-bitrate and
    the library rewrites itself to gain nothing; near zero none ever does,
    which is the mode being off and has its own spelling."""
    monkeypatch.setattr(config, "REGENERATE_BELOW_PERCENT", percent)
    errors = config.errors()
    assert len(errors) == 1
    assert "REGENERATE_BELOW_PERCENT" in errors[0]


@pytest.mark.parametrize("percent", [10, 90])
def test_the_edges_of_the_low_bitrate_band_are_allowed(monkeypatch, percent):
    """The band the settings page offers is the band the service takes, ends
    included, or the field's own limits would refuse a save it invited."""
    monkeypatch.setattr(config, "REGENERATE_BELOW_PERCENT", percent)
    assert config.errors() == []
