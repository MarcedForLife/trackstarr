"""Environment parsing: malformed values must reach errors(), never raise.

The checks that need the rule and layout vocabulary live beside it, in
test_policy.py.
"""

import os

import pytest

from conftest import set_config
from trackstarr import config


def source(**file: str) -> config._Source:
    """One read of the environment, with these settings-file entries behind
    it. Reading the file itself has its own tests below."""
    read = config._Source()
    read.file = dict(file)
    return read


def test_bad_int_keeps_default_and_records_error(monkeypatch):
    monkeypatch.setenv("HARDLINK_RECHECK", "soon")
    read = source()
    assert read._int("HARDLINK_RECHECK", "900") == 900
    assert any("HARDLINK_RECHECK" in problem for problem in read.problems)


def test_bad_regex_keeps_default_and_records_error(monkeypatch):
    monkeypatch.setenv("SDH_PATTERN", "(unclosed")
    read = source()
    assert read._regex("SDH_PATTERN", r"\bsdh\b").search("SDH")
    assert any("SDH_PATTERN" in problem for problem in read.problems)


def test_good_values_record_nothing(monkeypatch):
    monkeypatch.setenv("LISTEN_PORT", "9090")
    monkeypatch.setenv("FORCED_PATTERN", "forced|signs")
    read = source()
    assert read._int("LISTEN_PORT", "5120") == 9090
    assert read._regex("FORCED_PATTERN", r"\bforced\b").search("signs")
    assert read.problems == []


def test_a_boolean_typo_keeps_the_default_and_records_an_error(monkeypatch):
    """SKIP_HARDLINKS read as false by a typo is among the worst misreads
    config could make: every rewrite breaks a seeding torrent's hard link and
    the file costs disk twice."""
    monkeypatch.setenv("IMDB_RATINGS", "enalbed")
    monkeypatch.setenv("SKIP_HARDLINKS", "ture")
    read = source()
    assert read._bool("IMDB_RATINGS") is False
    assert read._bool("SKIP_HARDLINKS", "true") is True
    assert len(read.problems) == 2
    assert "IMDB_RATINGS" in read.problems[0]
    assert "SKIP_HARDLINKS" in read.problems[1]


@pytest.mark.parametrize("raw", ["1", "true", "Yes", " ON "])
def test_every_spelling_of_true_reads_as_true(monkeypatch, raw):
    monkeypatch.setenv("SKIP_HARDLINKS", raw)
    read = source()
    assert read._bool("SKIP_HARDLINKS") is True
    assert read.problems == []


@pytest.mark.parametrize("raw", ["0", "false", "No", " OFF "])
def test_every_spelling_of_false_reads_as_false(monkeypatch, raw):
    monkeypatch.setenv("SKIP_HARDLINKS", raw)
    read = source()
    assert read._bool("SKIP_HARDLINKS", "true") is False
    assert read.problems == []


def test_an_empty_boolean_reads_as_its_default(monkeypatch):
    """A leftover ``IMDB_RATINGS: ${IMDB_RATINGS}`` in a compose
    file expands to empty, which is a leftover, not a typo, and must not block
    startup."""
    monkeypatch.setenv("IMDB_RATINGS", "")
    monkeypatch.setenv("SKIP_HARDLINKS", " ")
    read = source()
    assert read._bool("IMDB_RATINGS") is False
    assert read._bool("SKIP_HARDLINKS", "true") is True
    assert read.problems == []


@pytest.mark.parametrize(
    ("name", "written", "expected"),
    [
        ("LANGUAGES", "original, jpn:keep ,eng", ("original", "jpn:keep", "eng")),
        ("AUDIO_LAYOUTS", " 5.1 , 2.0 ,7.1", ("5.1", "2.0", "7.1")),
        ("AUDIO_LAYOUTS", "5.1,2.0,5.1", ("5.1", "2.0")),
    ],
    ids=["downmix source preference", "layout order", "a repeat keeps its first place"],
)
def test_an_ordered_setting_keeps_what_was_written(monkeypatch, name, written, expected):
    """Order is the downmix source preference and the order the audio tracks are
    laid out in, so neither can be a set. A repeat kept in its later position
    would win the dicts built from this and silently move the track."""
    monkeypatch.setenv(name, written)
    assert source()._ordered(name, "") == expected


@pytest.mark.parametrize("variable", ["RADARR_API_KEY_FILE", "FILE__RADARR_API_KEY"])
def test_a_credential_can_come_from_a_file(monkeypatch, tmp_path, variable):
    """Both conventions, because every *arr beside us uses one or the other."""
    secret_file = tmp_path / "radarr_api_key"
    # Trailing newline included: echo > file puts one there, and it would
    # otherwise be sent as part of the key and rejected as a bad one.
    secret_file.write_text("abc123\n")
    monkeypatch.setenv(variable, str(secret_file))
    read = source()
    assert read._secret("RADARR_API_KEY") == "abc123"
    assert read.problems == []


def test_a_credential_still_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("RADARR_API_KEY", " abc123 ")
    read = source()
    assert read._secret("RADARR_API_KEY") == "abc123"
    assert read.problems == []


def test_an_unset_credential_is_blank(monkeypatch):
    monkeypatch.delenv("RADARR_API_KEY", raising=False)
    read = source()
    assert read._secret("RADARR_API_KEY") == ""
    assert read.problems == []


def test_naming_a_credential_two_ways_is_refused(monkeypatch, tmp_path):
    """Which one is live would be invisible, and the wrong key looks exactly like
    a revoked one from the far end."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("from-the-file")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RADARR_API_KEY", "from-the-environment")
    read = source()
    assert read._secret("RADARR_API_KEY") == ""
    (problem,) = read.problems
    assert "RADARR_API_KEY_FILE" in problem and "RADARR_API_KEY" in problem


def test_a_credential_left_expanding_to_nothing_is_not_a_conflict(monkeypatch, tmp_path):
    """A forgotten ``RADARR_API_KEY: ${RADARR_API_KEY}`` line is a leftover, not an
    ambiguity, and must not block startup."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("from-the-file")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RADARR_API_KEY", "")
    read = source()
    assert read._secret("RADARR_API_KEY") == "from-the-file"
    assert read.problems == []


def test_an_unreadable_credential_file_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(tmp_path / "absent"))
    read = source()
    assert read._secret("RADARR_API_KEY") == ""
    (problem,) = read.problems
    assert "RADARR_API_KEY_FILE" in problem


def test_an_empty_credential_file_is_refused(monkeypatch, tmp_path):
    """A blank key reads the same as one never set, so the *arr would be silently
    disabled rather than reported."""
    secret_file = tmp_path / "radarr_api_key"
    secret_file.write_text("\n")
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(secret_file))
    read = source()
    assert read._secret("RADARR_API_KEY") == ""
    (problem,) = read.problems
    assert "is empty" in problem


@pytest.mark.parametrize("value", ["04:00", "0 4 * *", "60 4 * * *", "0 4 * * mon"])
def test_a_bad_sweep_schedule_is_refused(value):
    """The scheduler would otherwise die alone. "04:00" matters most: it is what
    SWEEP_AT took before it became a cron schedule."""
    set_config(SWEEP_AT=value)
    (problem,) = config.errors()
    assert "SWEEP_AT" in problem


@pytest.mark.parametrize("value", ["", "0 4 * * *", "*/30 * * * *", "0 6 * * 1-5"])
def test_a_valid_sweep_schedule_passes(value):
    set_config(SWEEP_AT=value)
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
    assert source()._rule_modes()["cover_art"] == "alongside"
    # Still only a spelling: policy.errors() is what refuses a real typo,
    # and test_policy.py covers that.


def test_a_rule_variable_stating_nothing_leaves_the_default(monkeypatch):
    """A leftover ``RULE_SDH: ${RULE_SDH}`` in a compose file expands to
    empty, which must not read as a mode nobody chose."""
    monkeypatch.setenv("RULE_SDH", "  ")
    assert "sdh" not in source()._rule_modes()


def test_the_environment_wins_over_the_file_for_one_rule(monkeypatch):
    monkeypatch.setenv("RULE_REMUX", "never")
    assert source(RULE_REMUX="always")._rule_modes()["remux"] == "never"


def test_a_missing_media_dir_is_a_warning_not_an_error(tmp_path):
    """Quiet otherwise: the sweep says so as it walks, hours later, and an install
    with no SWEEP_AT never walks at all."""
    set_config(MEDIA_DIRS=[str(tmp_path), str(tmp_path / "absent")])
    (warning,) = config.media_dir_warnings()
    assert str(tmp_path / "absent") in warning
    # A library mounted late, or a webhook-only install, must still start.
    assert config.errors() == []


def test_a_layout_carries_its_own_encoder_and_rate(monkeypatch):
    """One variable holds the whole audio policy, so a rate cannot be left
    beside a layout nothing makes."""
    monkeypatch.setenv("AUDIO_LAYOUTS", "2.0:libopus:192k,7.1:remove")
    assert source()._ordered("AUDIO_LAYOUTS", "") == ("2.0:libopus:192k", "7.1:remove")


def _write_settings(tmp_path, monkeypatch, text: str) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    (tmp_path / config.SETTINGS_FILE).write_text(text)


def test_a_settings_file_value_is_read_when_the_environment_is_silent(monkeypatch):
    monkeypatch.delenv("HARDLINK_RECHECK", raising=False)
    read = source(HARDLINK_RECHECK="600")
    assert read._int("HARDLINK_RECHECK", "900") == 600
    assert read.problems == []


def test_the_environment_beats_the_settings_file(monkeypatch):
    monkeypatch.setenv("HARDLINK_RECHECK", "300")
    assert source(HARDLINK_RECHECK="600")._int("HARDLINK_RECHECK", "900") == 300


def test_a_blank_environment_value_yields_to_the_settings_file(monkeypatch):
    """A leftover ``SKIP_HARDLINKS: ${SKIP_HARDLINKS}`` expands to empty, which is
    a leftover, not a choice, and must not mask a setting the file states."""
    monkeypatch.setenv("SKIP_HARDLINKS", "")
    assert source(SKIP_HARDLINKS="true")._bool("SKIP_HARDLINKS") is True


def test_an_empty_media_dirs_still_means_no_dirs(monkeypatch):
    """MEDIA_DIRS="" is a webhook-only install's choice. With no settings-file
    entry behind it, it must keep meaning "walk nothing", never the shipped
    defaults."""
    monkeypatch.setenv("MEDIA_DIRS", "")
    assert source()._list("MEDIA_DIRS", "/data/media/movies:/data/media/tv") == []


def test_a_path_map_reads_longest_prefix_first(monkeypatch):
    """Order is the whole contract: /data/media/tv has to win over /data/media
    or every episode would be mapped to the movies mount."""
    monkeypatch.setenv(
        "PLEX_PATH_MAP", "/data/media=/mnt/content/media, /data/media/tv/=/mnt/tv/"
    )
    read = source()
    assert read._path_map("PLEX_PATH_MAP") == [
        ("/data/media/tv", "/mnt/tv"),
        ("/data/media", "/mnt/content/media"),
    ]
    assert read.problems == []


def test_a_half_written_path_map_entry_is_refused(monkeypatch):
    """Refreshes are best effort, so a pair missing its remote half would
    otherwise be a silent no-op for that library."""
    monkeypatch.setenv("PLEX_PATH_MAP", "/data/media,/data/tv=/mnt/tv")
    read = source()
    assert read._path_map("PLEX_PATH_MAP") == [("/data/tv", "/mnt/tv")]
    assert any("PLEX_PATH_MAP" in problem for problem in read.problems)


def test_no_path_map_is_no_mapping(monkeypatch):
    monkeypatch.delenv("PLEX_PATH_MAP", raising=False)
    read = source()
    assert read._path_map("PLEX_PATH_MAP") == []
    assert read.problems == []


def test_settings_numbers_and_booleans_read_as_their_literals(tmp_path, monkeypatch):
    """A hand-written file naturally says 5120 and true; they arrive spelled
    the way the same-named variable would hold them."""
    _write_settings(tmp_path, monkeypatch, '{"LISTEN_PORT": 5120, "SKIP_HARDLINKS": true}')
    read = config._Source()
    assert read.file == {"LISTEN_PORT": "5120", "SKIP_HARDLINKS": "true"}
    assert read.problems == []


def test_a_structured_settings_value_is_refused(tmp_path, monkeypatch):
    """A dropped setting has to reach errors(), never quietly mean its
    default; the rest of the file still loads."""
    _write_settings(tmp_path, monkeypatch, '{"MEDIA_DIRS": ["/a"], "SKIP_HARDLINKS": "true"}')
    read = config._Source()
    assert read.file == {"SKIP_HARDLINKS": "true"}
    (problem,) = read.problems
    assert "MEDIA_DIRS" in problem


def test_an_unreadable_settings_file_is_refused(tmp_path, monkeypatch):
    """Silently ignored, a damaged file would run the library on defaults its
    owner did not choose."""
    _write_settings(tmp_path, monkeypatch, "{not json")
    read = config._Source()
    assert read.file == {}
    (problem,) = read.problems
    assert config.SETTINGS_FILE in problem


def test_a_settings_file_must_hold_one_object(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, '["SKIP_HARDLINKS"]')
    read = config._Source()
    assert read.file == {}
    assert len(read.problems) == 1


def test_a_missing_settings_file_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    read = config._Source()
    assert read.file == {}
    assert read.problems == []


def _write_env(tmp_path, text: str) -> str:
    path = tmp_path / config.ENV_FILE
    path.write_text(text)
    return str(path)


def test_a_dotenv_value_fills_an_unset_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("WORK_DIR", raising=False)
    _, problems = config._load_dotenv(_write_env(tmp_path, "WORK_DIR=./dev/data\n"))
    assert os.environ["WORK_DIR"] == "./dev/data"
    assert problems == []


def test_a_dotenv_value_never_overwrites_the_environment(tmp_path, monkeypatch):
    """The environment is the deploy's word; a checkout's .env must not take it
    back, the same precedence _raw gives it over the settings file."""
    monkeypatch.setenv("WORK_DIR", "/data/trackstarr-work")
    config._load_dotenv(_write_env(tmp_path, "WORK_DIR=./dev/data\n"))
    assert os.environ["WORK_DIR"] == "/data/trackstarr-work"


def test_dotenv_skips_blanks_and_comments_and_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("WORK_DIR", raising=False)
    monkeypatch.delenv("STATE_DIR", raising=False)
    _, problems = config._load_dotenv(
        _write_env(tmp_path, '\n# a comment\nWORK_DIR="./dev/data"\nSTATE_DIR=./dev/config\n')
    )
    assert os.environ["WORK_DIR"] == "./dev/data"
    assert os.environ["STATE_DIR"] == "./dev/config"
    assert problems == []


def test_a_dotenv_line_without_an_equals_is_refused(tmp_path):
    """A typo has to reach errors(), never pass unseen, the same as a
    settings-file one."""
    _, problems = config._load_dotenv(_write_env(tmp_path, "WORK_DIR\n"))
    (problem,) = problems
    assert "NAME=VALUE" in problem


def test_a_dotenv_hands_back_what_it_held(tmp_path, monkeypatch):
    """TZ is read before .env can be loaded, since applying a saved zone means
    writing the variable itself, so STATED_TZ asks the file rather than the
    environment it has just filled in."""
    monkeypatch.delenv("TZ", raising=False)
    held, _ = config._load_dotenv(_write_env(tmp_path, "TZ=Pacific/Auckland\n# a comment\n"))
    assert held == {"TZ": "Pacific/Auckland"}


def test_a_missing_dotenv_is_silent(tmp_path):
    values, problems = config._load_dotenv(str(tmp_path / "nope.env"))
    assert (values, problems) == ({}, [])


def test_an_unreadable_dotenv_is_refused(tmp_path):
    """A directory where the file should be raises OSError, not
    FileNotFoundError, and must be reported rather than crash import."""
    (tmp_path / config.ENV_FILE).mkdir()
    _, problems = config._load_dotenv(str(tmp_path / config.ENV_FILE))
    (problem,) = problems
    assert config.ENV_FILE in problem


def test_a_settings_key_nothing_reads_is_a_warning_not_an_error():
    """An unread key is a typo silently meaning its default, but everything else
    still applies. A name retired under a running install would otherwise refuse
    every start."""
    set_config(file={"MEDIA_DIRZ": "/data"})
    assert config.errors() == []
    (problem,) = config.warnings()
    assert "MEDIA_DIRZ" in problem
    # The settings pages write known names only, so they cannot clear this one:
    # the line has to carry the file's whole path and what to do with it.
    assert config._settings_path() in problem
    assert "Remove or rename them" in problem


def test_a_settings_file_layout_list_is_read_and_not_flagged_unread():
    """One name holds the whole audio policy now, so the unread-key check sees
    it asked for like any other setting."""
    read = source(AUDIO_LAYOUTS="2.0,7.1:aac:1280k")
    assert read._ordered("AUDIO_LAYOUTS", "") == ("2.0", "7.1:aac:1280k")
    assert "AUDIO_LAYOUTS" in read.read


def test_a_secret_can_come_from_the_settings_file(monkeypatch):
    monkeypatch.delenv("RADARR_API_KEY", raising=False)
    read = source(RADARR_API_KEY=" abc123 ")
    assert read._secret("RADARR_API_KEY") == "abc123"
    assert read.problems == []


def test_an_environment_secret_beats_the_settings_file(monkeypatch):
    monkeypatch.setenv("RADARR_API_KEY", "from-the-environment")
    read = source(RADARR_API_KEY="from-the-file")
    assert read._secret("RADARR_API_KEY") == "from-the-environment"


@pytest.mark.parametrize("value", [0, -1])
def test_a_rewrite_budget_below_one_is_refused(value):
    """Zero leaves the pool with no workers, so the sweep would hang rather
    than fail, the worse way to find out."""
    set_config(MAX_CONCURRENT_REWRITES=value)
    (problem,) = config.errors()
    assert "MAX_CONCURRENT_REWRITES" in problem


@pytest.mark.parametrize("name", ["FFMPEG_TIMEOUT", "PROBE_TIMEOUT"])
def test_a_timeout_below_one_is_refused(name):
    """Zero or negative fails every run as "timed out", hours after the
    restart that set it."""
    set_config(**{name: 0})
    (problem,) = config.errors()
    assert name in problem


@pytest.mark.parametrize(
    "name", ["RADARR_URL", "SONARR_URL", "PLEX_URL", "JELLYFIN_URL", "WEBHOOK_URL"]
)
def test_an_address_without_a_scheme_is_refused(name):
    """A scheme is the one thing a hand-typed address always loses, and
    urllib's complaint about it lands on a background thread once per call."""
    set_config(**{name: "radarr:7878"})
    (problem,) = config.errors()
    assert name in problem


def test_an_address_with_a_scheme_records_nothing():
    set_config(PLEX_URL="https://plex.example.com")
    assert config.errors() == []


@pytest.mark.parametrize(
    ("set_name", "unset_name"),
    [("PLEX_URL", "PLEX_TOKEN"), ("SONARR_API_KEY", "SONARR_URL")],
)
def test_half_a_service_is_a_warning_naming_both_halves(set_name, unset_name):
    """Either half alone leaves the service silently off. A warning rather
    than an error: clearing an address is how one is switched off."""
    set_config(**{set_name: "set"})
    (problem,) = config.warnings()
    assert set_name in problem
    assert unset_name in problem


# REWRITE_MODE, the ladder that replaced the DRY_RUN and SWEEP_APPLY pair.


@pytest.mark.parametrize("value", ["report", "IMPORTS", " all "])
def test_every_rung_of_the_ladder_is_read(monkeypatch, value):
    monkeypatch.setenv("REWRITE_MODE", value)
    read = source()
    mode = read._choice("REWRITE_MODE", "imports", config.REWRITE_MODES)
    assert mode == value.strip().lower()
    assert read.problems == []


def test_a_mode_typo_is_refused_rather_than_read_as_the_default(monkeypatch):
    """Read as its default, a typo would put a library its owner had asked to
    be reported on back on the rung that rewrites imports."""
    monkeypatch.setenv("REWRITE_MODE", "reprot")
    read = source()
    assert read._choice("REWRITE_MODE", "imports", config.REWRITE_MODES) == "imports"
    (problem,) = read.problems
    assert "reprot" in problem
    assert "report, imports, all" in problem


def test_an_empty_mode_reads_as_its_default(monkeypatch):
    """A leftover ``REWRITE_MODE: ${REWRITE_MODE}`` expands to empty, which is
    a leftover, not a typo, and must not block startup."""
    monkeypatch.setenv("REWRITE_MODE", "  ")
    read = source()
    assert read._choice("REWRITE_MODE", "imports", config.REWRITE_MODES) == "imports"
    assert read.problems == []


def test_the_probe_pool_no_longer_borrows_the_rewrite_budget():
    """Two settings that used to be one: probing is short and IO-bound where a
    rewrite is long and disk-bound, so the disk that wants one rewrite at a
    time still wants several probes."""
    assert config.current().PROBE_WORKERS == 4
    assert config.current().MAX_CONCURRENT_REWRITES == 1


@pytest.mark.parametrize("name", ["MAX_CONCURRENT_REWRITES", "PROBE_WORKERS"])
def test_a_pool_below_one_is_refused(name):
    """A rewrite budget of zero hangs the slot loop; a probe pool of zero is a
    ThreadPoolExecutor that refuses to start."""
    set_config(**{name: 0})
    assert any(name in problem for problem in config.errors())


@pytest.mark.parametrize(
    ("name", "percent"),
    [
        ("REGENERATE_BELOW_PERCENT", 9),
        ("REGENERATE_BELOW_PERCENT", 91),
        ("REGENERATE_ABOVE_PERCENT", 109),
        ("REGENERATE_ABOVE_PERCENT", 401),
    ],
)
def test_a_bitrate_threshold_outside_its_band_is_refused(name, percent):
    """Near a layout's own rate, every honest variable-rate track qualifies and
    the library rewrites itself to gain nothing. Far outside it none ever does,
    which is the mode being off and has its own spelling."""
    set_config(**{name: percent})
    (problem,) = config.errors()
    assert name in problem


@pytest.mark.parametrize(
    ("name", "percent"),
    [
        ("REGENERATE_BELOW_PERCENT", 10),
        ("REGENERATE_BELOW_PERCENT", 90),
        ("REGENERATE_ABOVE_PERCENT", 0),
        ("REGENERATE_ABOVE_PERCENT", 110),
        ("REGENERATE_ABOVE_PERCENT", 400),
    ],
)
def test_the_edges_of_a_bitrate_band_are_allowed(name, percent):
    """The band the settings page offers is the band the service takes, ends
    included, or a field would refuse a save it invited. 0 is ABOVE switched
    off, which the page offers too."""
    set_config(**{name: percent})
    assert config.errors() == []


# The snapshot itself: one pointer every reader takes by reference.


def test_a_setting_read_twice_in_one_snapshot_cannot_change_between():
    """The point of the whole arrangement: a caller taking several settings
    holds one read of them, so a save landing mid-verdict cannot mix two."""
    held = config.current()
    set_config(REWRITE_MODE="report", ALLOWED_EXTS={".mkv"})
    assert held.REWRITE_MODE == "imports"
    assert ".mp4" in held.ALLOWED_EXTS
    assert config.current().REWRITE_MODE == "report"


def test_reset_hands_back_what_a_fresh_process_read():
    """What every test's teardown leans on, so nothing a test sets survives
    into the next one."""
    set_config(REWRITE_MODE="report")
    config.reset()
    assert config.current().REWRITE_MODE == "imports"
