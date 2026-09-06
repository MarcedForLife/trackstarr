"""The settings API's model: effective values out, live file writes in."""

import importlib
import json
import os
import time
import zoneinfo

import pytest

import trackstarr
from conftest import read_events
from trackstarr import config, events, executor, keystore, policy, processing, settings


def mode(name: str) -> int:
    """A file in the state directory's permission bits."""
    return os.stat(os.path.join(config.STATE_DIR, name)).st_mode & 0o777


def read_settings_file() -> dict:
    with open(os.path.join(config.STATE_DIR, config.SETTINGS_FILE)) as settings_file:
        return json.load(settings_file)


def test_snapshot_reports_defaults_and_sources(settings_state):
    shot = settings.snapshot()
    assert shot["settings"]["DOWNMIX_LAYOUTS"] == {"value": ["2.0", "5.1"], "env": False}
    assert shot["settings"]["AUDIO_BITRATE_2_0"] == {"value": "320k", "env": False}
    assert shot["settings"]["AUDIO_CODEC_2_0"] == {"value": "aac", "env": False}
    assert shot["settings"]["AUDIO_CODEC_5_1"] == {"value": "ac3", "env": False}
    assert shot["settings"]["REGENERATE_SCOPE"]["value"] == "generated"
    assert shot["settings"]["RULE_LANGUAGES"] == {"value": "always", "env": False}
    assert shot["settings"]["RULE_SDH"]["value"] == "alongside"
    assert shot["settings"]["RULE_REMUX"]["value"] == "never"
    assert shot["settings"]["ALWAYS_KEEP_LANGS"]["value"] == ["eng"]
    assert shot["settings"]["KEEP_ORIGINAL_LANG"]["value"] is True
    assert shot["settings"]["DOWNMIX_ORIGINAL_LANG"]["value"] is True
    assert shot["settings"]["DOWNMIX_LANGS"]["value"] == []
    assert set(shot["rules"]) == set(policy.RULES)
    assert shot["rules"]["sdh"]["default"] == "alongside"
    assert shot["modes"] == list(policy.MODES)


def test_snapshot_offers_languages_by_name(settings_state):
    """The page's picker, and the way a name typed into it becomes a tag."""
    languages = settings.snapshot()["languages"]
    assert languages["eng"] == "English"
    assert languages["jpn"] == "Japanese"
    # One name per code, and the plain one: both spellings normalise to por.
    assert languages["por"] == "Portuguese"
    assert list(languages.values()) == sorted(languages.values())


def test_update_applies_live_and_records_the_config(settings_state):
    changes = {"RULE_COMMENTARY": "always", "ALWAYS_KEEP_LANGS": ["eng", "jpn"]}
    assert settings.update(changes) == []
    assert config.RULE_MODES["commentary"] == "always"
    assert {"eng"} | {"jpn"} == config.ALWAYS_KEEP_LANGS
    assert read_settings_file() == {
        "RULE_COMMENTARY": "always",
        "ALWAYS_KEEP_LANGS": "eng,jpn",
    }
    # The save, then the rules it put in force: the change is the cause and
    # the new fingerprint is what it did.
    saved, applied = read_events()
    assert saved["event"] == "settings"
    assert saved["changed"]["RULE_COMMENTARY"] == {"from": "never", "to": "always"}
    assert saved["changed"]["ALWAYS_KEEP_LANGS"] == {"from": ["eng"], "to": ["eng", "jpn"]}
    assert applied["event"] == "config"
    assert ["commentary", "always"] in applied["config"]["rule_modes"]


def started() -> None:
    """What serve() does before anything else, so the rules in force are
    already pinned and a save that leaves them alone adds no config line."""
    events.record_config(policy.Policy.from_config())


def test_a_setting_outside_the_rules_still_leaves_a_line(settings_state):
    """The bug this test exists for: the fingerprint holds what decides a
    file's fate, so a schedule or an address changed nothing in it and the
    history had nothing to say about the save at all."""
    started()
    assert settings.update({"REWRITE_MODE": "all", "SWEEP_AT": "0 4 * * *"}, by="marc") == []
    _, entry = read_events()
    assert entry["event"] == "settings"
    assert entry["by"] == "marc"
    assert entry["changed"] == {
        "REWRITE_MODE": {"from": "imports", "to": "all"},
        "SWEEP_AT": {"from": "", "to": "0 4 * * *"},
    }


def test_a_save_that_alters_nothing_leaves_no_line(settings_state):
    started()
    assert settings.update({"SWEEP_AT": "0 4 * * *"}) == []
    assert settings.update({"SWEEP_AT": "0 4 * * *"}) == []
    assert [entry["event"] for entry in read_events()] == ["config", "settings"]


def test_a_credential_change_is_recorded_without_the_credential(settings_state):
    started()
    assert settings.update({"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "sekrit"}) == []
    _, entry = read_events()
    assert entry["changed"]["PLEX_TOKEN"] == {"from": "unset", "to": "set"}
    assert entry["changed"]["PLEX_URL"] == {"from": "", "to": "http://plex:32400"}
    assert "sekrit" not in json.dumps(entry)


def test_a_new_name_is_recorded_as_arriving_rather_than_as_null(settings_state):
    """A layout brings its own bitrate setting, which had no value before,
    and the history says so by leaving the near side out."""
    started()
    layouts = {
        "DOWNMIX_LAYOUTS": ["2.0", "5.1", "7.1"],
        "AUDIO_CODEC_7_1": "aac",
        "AUDIO_BITRATE_7_1": "640k",
    }
    assert settings.update(layouts) == []
    _, saved, _ = read_events()
    assert saved["changed"]["AUDIO_BITRATE_7_1"] == {"to": "640k"}


def test_a_name_that_goes_away_is_recorded_as_leaving(settings_state):
    """And the mirror: the layout is dropped, so its bitrate has a near side
    and no far one rather than a null the page would read as a value."""
    started()
    assert (
        settings.update(
            {
                "DOWNMIX_LAYOUTS": ["2.0", "5.1", "7.1"],
                "AUDIO_CODEC_7_1": "aac",
                "AUDIO_BITRATE_7_1": "640k",
            }
        )
        == []
    )
    dropped = {
        "DOWNMIX_LAYOUTS": ["2.0", "5.1"],
        "AUDIO_CODEC_7_1": None,
        "AUDIO_BITRATE_7_1": None,
    }
    assert settings.update(dropped) == []
    saved = [entry for entry in read_events() if entry["event"] == "settings"][-1]
    assert saved["changed"]["AUDIO_BITRATE_7_1"] == {"from": "640k"}


def test_a_change_startup_would_refuse_is_rolled_back_whole(settings_state):
    problems = settings.update({"RULE_LANGAUGES": "never", "RULE_COMMENTARY": "always"})
    assert any("names no rule" in problem for problem in problems)
    # The valid half must not survive alone; the write is one gesture.
    assert "langauges" not in config.RULE_MODES
    assert "commentary" not in config.RULE_MODES
    assert not os.path.exists(os.path.join(config.STATE_DIR, config.SETTINGS_FILE))
    assert read_events() == []


def test_null_unsets_a_name(settings_state):
    assert settings.update({"RULE_COMMENTARY": "always"}) == []
    assert settings.update({"RULE_COMMENTARY": None}) == []
    assert "commentary" not in config.RULE_MODES
    assert read_settings_file() == {}


def test_an_env_pinned_name_is_refused(settings_state):
    os.environ["AUDIO_CODEC_2_0"] = "libfdk_aac"
    try:
        importlib.reload(config)
        shot = settings.snapshot()
        assert shot["settings"]["AUDIO_CODEC_2_0"] == {"value": "libfdk_aac", "env": True}
        (problem,) = settings.update({"AUDIO_CODEC_2_0": "aac"})
        assert "environment" in problem
    finally:
        os.environ.pop("AUDIO_CODEC_2_0", None)
    # settings_state's teardown reloads config with the variable gone.


def test_only_the_editable_names_are_accepted(settings_state):
    # STATE_DIR alongside a plain one: it is environment-only by design, since
    # it says where the file the UI would write lives.
    for name in ("LISTEN_PORT", "STATE_DIR"):
        assert settings.update({name: "9"}) == [f"{name} is not a setting the UI edits"]
    assert settings.update({"AUDIO_CODEC_2_0": 640}) == [
        "AUDIO_CODEC_2_0 must be a string, boolean, list of strings or null"
    ]


def test_report_mode_toggles_the_latch_live(settings_state):
    assert settings.snapshot()["settings"]["REWRITE_MODE"] == {"value": "imports", "env": False}
    assert settings.update({"REWRITE_MODE": "report"}) == []
    assert config.REWRITE_MODE == "report"
    # A webhook import asks for a rewrite outright, so the mode only counts if
    # it reaches the latch every path goes through, without a restart.
    assert processing.effective_dry_run(False) is True
    assert settings.update({"REWRITE_MODE": "imports"}) == []
    assert processing.effective_dry_run(False) is False


def test_a_mode_nobody_spells_that_way_is_rolled_back(settings_state):
    """The ladder is a closed set, and a typo read as its default would rewrite
    a library its owner had only asked to be reported on."""
    assert settings.update({"REWRITE_MODE": "everything"}) == [
        "REWRITE_MODE='everything' is not one of: report, imports, all"
    ]
    assert config.REWRITE_MODE == "imports"


def test_layout_order_survives_the_round_trip(settings_state):
    """The UI's drag writes the order it was given, and reads it back; a
    snapshot that sorted like the other lists would undo every drag."""
    assert settings.update({"DOWNMIX_LAYOUTS": ["5.1", "2.0"]}) == []
    assert read_settings_file() == {"DOWNMIX_LAYOUTS": "5.1,2.0"}
    assert config.DOWNMIX_LAYOUTS == ("5.1", "2.0")
    assert settings.snapshot()["settings"]["DOWNMIX_LAYOUTS"]["value"] == ["5.1", "2.0"]


def test_the_low_bitrate_threshold_saves_and_is_held_to_its_band(settings_state):
    """The rules page's own field: it saves as the string the file holds, and a
    percent outside the band the page offers is rolled back whole."""
    assert settings.snapshot()["settings"]["REGENERATE_BELOW_PERCENT"]["value"] == "50"
    assert settings.update({"REGENERATE_BELOW_PERCENT": "75"}) == []
    assert config.REGENERATE_BELOW_PERCENT == 75
    (problem,) = settings.update({"REGENERATE_BELOW_PERCENT": "99"})
    assert "REGENERATE_BELOW_PERCENT" in problem
    assert config.REGENERATE_BELOW_PERCENT == 75


def test_a_credential_is_reported_as_set_never_echoed(settings_state):
    """/api/settings is a read any session may make, so a viewer must not be
    able to walk away with the *arrs' keys."""
    assert settings.snapshot()["settings"]["RADARR_API_KEY"] == {
        "value": "",
        "env": False,
        "set": False,
    }
    assert settings.update({"RADARR_API_KEY": "abc123"}) == []
    assert config.RADARR_API_KEY == "abc123"

    entry = settings.snapshot()["settings"]["RADARR_API_KEY"]
    assert entry == {"value": "", "env": False, "set": True}
    assert "abc123" not in json.dumps(settings.snapshot())


def test_a_saved_credential_is_sealed_on_the_volume(settings_state):
    """The connections page made settings.json where the *arr keys live, so
    what lands there is ciphertext and the key beside it is 0600."""
    assert settings.update({"RADARR_API_KEY": "abc123"}) == []
    held = read_settings_file()["RADARR_API_KEY"]
    assert held.startswith(keystore.PREFIX)
    assert "abc123" not in held
    # Sealed on disk, plain in hand: this is the value replayed to Radarr.
    assert config.RADARR_API_KEY == "abc123"
    assert mode(config.SETTINGS_FILE) == 0o600
    assert mode(keystore.KEY_FILE) == 0o600


def test_a_save_with_no_credential_in_it_mints_no_key(settings_state):
    """A deploy that sets every key by environment or *_FILE mount has
    nothing to seal, and should not grow a key file for the privilege."""
    assert settings.update({"RULE_COMMENTARY": "always"}) == []
    assert not os.path.exists(os.path.join(config.STATE_DIR, keystore.KEY_FILE))


def test_a_hand_written_credential_is_sealed_by_the_next_save(settings_state):
    """settings.json is documented as hand-editable, so a plain key typed
    into it has to keep working -- and stop being plain at the first save."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(os.path.join(config.STATE_DIR, config.SETTINGS_FILE), "w") as by_hand:
        json.dump({"RADARR_API_KEY": "typed-in", "PLEX_URL": "http://plex:32400"}, by_hand)
    importlib.reload(config)
    assert config.RADARR_API_KEY == "typed-in"

    assert settings.update({"RULE_COMMENTARY": "always"}) == []
    assert read_settings_file()["RADARR_API_KEY"].startswith(keystore.PREFIX)
    assert config.RADARR_API_KEY == "typed-in"


def test_a_credential_whose_key_is_gone_reads_as_unset_and_says_so(settings_state):
    """Losing the key disables the service with a warning, not a startup error:
    the listener must come up for the key to be re-entered."""
    assert settings.update({"RADARR_API_KEY": "abc123"}) == []
    os.remove(os.path.join(config.STATE_DIR, keystore.KEY_FILE))
    importlib.reload(config)
    assert config.RADARR_API_KEY == ""
    (told,) = [problem for problem in config.warnings() if "sealed" in problem]
    assert "RADARR_API_KEY" in told
    assert config.errors() == []


def test_a_key_that_cannot_be_minted_refuses_the_save(settings_state, monkeypatch):
    """A read-only volume, or a mounted key that did not appear. The save has
    to fail as a save: writing the credential unsealed would put in plain text
    exactly what the operator asked to have kept."""

    def refuse(state_dir: str) -> bytes:
        raise OSError("read-only file system")

    monkeypatch.setattr(keystore, "ensure", refuse)
    (problem,) = settings.update({"RADARR_API_KEY": "abc123"})
    assert "could not be sealed" in problem
    assert config.RADARR_API_KEY == ""
    assert not os.path.exists(os.path.join(config.STATE_DIR, config.SETTINGS_FILE))


def test_a_damaged_key_file_disables_the_credentials_and_says_so(settings_state):
    """A clipped key is not a missing one: nothing opens under it, and the
    start still has to come up far enough to serve the page that fixes it."""
    assert settings.update({"RADARR_API_KEY": "abc123"}) == []
    with open(os.path.join(config.STATE_DIR, keystore.KEY_FILE), "w") as clipped:
        clipped.write("abcd\n")
    importlib.reload(config)

    assert config.RADARR_API_KEY == ""
    assert any(keystore.KEY_FILE in problem for problem in config.warnings())
    assert config.errors() == []


def test_a_credential_sealed_under_another_key_reads_as_unset(settings_state):
    """A settings.json restored from one volume beside the key from another.
    Every value in it is intact and none of it opens, which has to read as
    the credentials being unset rather than as Radarr rejecting a good key."""
    assert settings.update({"RADARR_API_KEY": "abc123"}) == []
    with open(os.path.join(config.STATE_DIR, keystore.KEY_FILE), "w") as elsewhere:
        elsewhere.write("33" * 32 + "\n")
    importlib.reload(config)

    assert config.RADARR_API_KEY == ""
    (told,) = [problem for problem in config.warnings() if "different key" in problem]
    assert "RADARR_API_KEY" in told


def test_re_entering_a_credential_recovers_from_a_lost_key(settings_state):
    """The way out, and the reason the last test is not fatal: type the key
    into the page again and it seals under whatever key is in hand now."""
    assert settings.update({"RADARR_API_KEY": "abc123"}) == []
    os.remove(os.path.join(config.STATE_DIR, keystore.KEY_FILE))
    importlib.reload(config)

    assert settings.update({"RADARR_API_KEY": "def456"}) == []
    assert config.RADARR_API_KEY == "def456"
    assert not any("sealed" in problem for problem in config.warnings())


def test_a_credential_mounted_as_a_file_counts_as_pinned(settings_state, tmp_path):
    """A *_FILE mount overrides a saved value as flatly as the plain variable,
    so a page that let it be edited would show a key changing nothing."""
    secret = tmp_path / "key"
    secret.write_text("from-a-file\n")
    os.environ["RADARR_API_KEY_FILE"] = str(secret)
    try:
        importlib.reload(config)
        entry = settings.snapshot()["settings"]["RADARR_API_KEY"]
        assert entry == {"value": "", "env": True, "set": True}
        (problem,) = settings.update({"RADARR_API_KEY": "typed"})
        assert "environment" in problem
    finally:
        os.environ.pop("RADARR_API_KEY_FILE", None)
        importlib.reload(config)


def test_an_address_without_a_scheme_is_rolled_back(settings_state):
    (problem,) = settings.update({"RADARR_URL": "radarr:7878"})
    assert "must start with http:// or https://" in problem
    assert config.RADARR_URL == ""
    assert settings.update({"RADARR_URL": "http://radarr:7878"}) == []
    assert config.RADARR_URL == "http://radarr:7878"


def test_a_path_map_round_trips_as_the_pairs_it_was_written_as(settings_state):
    assert settings.update({"PLEX_PATH_MAP": ["/data/media=/srv/media"]}) == []
    assert read_settings_file() == {"PLEX_PATH_MAP": "/data/media=/srv/media"}
    assert config.PLEX_PATH_MAP == [("/data/media", "/srv/media")]
    shot = settings.snapshot()["settings"]["PLEX_PATH_MAP"]
    assert shot["value"] == ["/data/media=/srv/media"]


def test_a_new_layout_needs_its_encoder_and_rate(settings_state):
    """Both, and named separately, so the page can say which box is empty."""
    problems = settings.update({"DOWNMIX_LAYOUTS": ["2.0", "5.1", "7.1"]})
    assert any("7.1 with no rate" in problem for problem in problems)
    assert any("7.1 with no encoder" in problem for problem in problems)
    assert (
        settings.update(
            {
                "DOWNMIX_LAYOUTS": ["2.0", "5.1", "7.1"],
                "AUDIO_CODEC_7_1": "aac",
                "AUDIO_BITRATE_7_1": "768k",
            }
        )
        == []
    )
    assert config.AUDIO_BITRATES["7.1"] == "768k"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("{not json", "could not be read"),
        ('["a list"]', "must hold one JSON object"),
    ],
)
def test_a_damaged_settings_file_refuses_the_write(settings_state, content, expected):
    """Guessing here could roll "back" to nothing, so a file that cannot be
    read is reported rather than replaced with whatever the page sent."""
    settings_state.mkdir(parents=True, exist_ok=True)
    (settings_state / config.SETTINGS_FILE).write_text(content)

    (problem,) = settings.update({"RULE_COMMENTARY": "always"})
    assert expected in problem
    assert "commentary" not in config.RULE_MODES
    # Untouched: the damaged file is the operator's to look at.
    assert (settings_state / config.SETTINGS_FILE).read_text() == content


def test_the_sweep_schedule_and_dirs_are_editable(settings_state):
    """The sweep page's settings, applied without a restart. MEDIA_DIRS is
    written with the colon config._list splits it on, not the comma every other
    list here uses."""
    assert (
        settings.update(
            {
                "SWEEP_AT": "0 4 * * *",
                "MEDIA_DIRS": ["/data/media/movies", "/data/media/tv"],
            }
        )
        == []
    )
    assert config.SWEEP_AT == "0 4 * * *"
    assert config.MEDIA_DIRS == ["/data/media/movies", "/data/media/tv"]
    assert read_settings_file()["MEDIA_DIRS"] == "/data/media/movies:/data/media/tv"

    shot = settings.snapshot()["settings"]
    assert shot["SWEEP_AT"]["value"] == "0 4 * * *"
    # Order is the walk order, so it comes back as written rather than sorted.
    assert shot["MEDIA_DIRS"]["value"] == ["/data/media/movies", "/data/media/tv"]


def test_a_schedule_the_scheduler_could_not_read_is_rolled_back(settings_state):
    """Left to the scheduler a bad expression means no sweeps at all, so it is
    refused here, where the page can still say which field is wrong."""
    assert settings.update({"SWEEP_AT": "0 4 * * *"}) == []
    (problem,) = settings.update({"SWEEP_AT": "0 99 * * *"})
    assert "hour" in problem
    assert config.SWEEP_AT == "0 4 * * *"


def test_a_codec_this_ffmpeg_lacks_is_rolled_back(settings_state, monkeypatch):
    """The page accepted any string, and a typo surfaced as every rewrite
    failing. Checked only on a save that names a codec, since it shells out."""
    monkeypatch.setattr(executor, "audio_encoders", lambda: frozenset({"aac", "ac3"}))
    (problem,) = settings.update({"AUDIO_CODEC_2_0": "acc"})
    assert "acc" in problem
    assert config.AUDIO_CODECS["2.0"] == "aac"
    assert settings.update({"AUDIO_CODEC_2_0": "ac3"}) == []
    assert config.AUDIO_CODECS["2.0"] == "ac3"


def test_an_ffmpeg_that_cannot_be_asked_refuses_nothing(settings_state, monkeypatch):
    """A build whose encoder list could not be read is nobody's typo, and a
    save it blocked would be unfixable from the page."""
    monkeypatch.setattr(executor, "audio_encoders", lambda: None)
    assert settings.update({"AUDIO_CODEC_2_0": "whatever"}) == []


def test_an_unrelated_save_never_asks_ffmpeg_anything(settings_state, monkeypatch):
    """Every save would otherwise pay for a subprocess with a 30s timeout."""
    monkeypatch.setattr(
        executor, "audio_encoders", lambda: pytest.fail("only a codec save asks")
    )
    assert settings.update({"SWEEP_AT": "0 4 * * *"}) == []


#: A zone no developer here runs in, so a stale clock cannot pass for an
#: applied one. Two names, either side of daylight saving.
_ELSEWHERE = "America/New_York"
_NAMES = ("EST", "EDT")


def test_the_time_zone_is_applied_to_the_process_itself(settings_state):
    """The one setting the service has to write into its own environment:
    nothing of ours reads TZ, libc does, so a saved zone means nothing until
    it is there and tzset() has been called."""
    assert settings.update({"TZ": _ELSEWHERE}) == []
    assert config.TZ == _ELSEWHERE
    assert os.environ["TZ"] == _ELSEWHERE
    assert time.strftime("%Z") in _NAMES
    assert read_settings_file() == {"TZ": _ELSEWHERE}
    # Ours rather than the deploy's, so the page may still edit it: the write
    # above is exactly what a naive read of TZ would take for pinned.
    assert settings.snapshot()["settings"]["TZ"] == {"value": _ELSEWHERE, "env": False}


def test_clearing_the_time_zone_takes_ours_back_out(settings_state):
    """Removing the entry has to remove the variable too, or the zone this
    process applied would outlive the setting that asked for it."""
    system = time.strftime("%Z")
    assert settings.update({"TZ": _ELSEWHERE}) == []
    assert settings.update({"TZ": None}) == []
    assert config.TZ == ""
    assert "TZ" not in os.environ
    assert time.strftime("%Z") == system


def test_a_zone_nothing_knows_is_rolled_back(settings_state):
    """musl and glibc both read an unknown zone as UTC, silently, which would
    move every stamp and every sweep with nothing anywhere saying why."""
    assert settings.update({"TZ": _ELSEWHERE}) == []
    (problem,) = settings.update({"TZ": "Middle/Earth"})
    assert "is not a zone this system knows" in problem
    assert config.TZ == _ELSEWHERE
    assert time.strftime("%Z") in _NAMES


def test_a_stated_zone_wins_and_pins_the_field(settings_state):
    """What the deploy put in TZ is already in force, so it is neither
    re-applied nor editable, the same bargain every other setting strikes."""
    trackstarr.ENV_TZ = "Etc/UTC"
    try:
        importlib.reload(config)
        assert settings.snapshot()["settings"]["TZ"] == {"value": "Etc/UTC", "env": True}
        (problem,) = settings.update({"TZ": _ELSEWHERE})
        assert "environment" in problem
        # Untouched: the zone the deploy stated is the process's already.
        assert "TZ" not in os.environ
    finally:
        trackstarr.ENV_TZ = ""


def test_the_snapshot_offers_the_zones_by_name(settings_state):
    zones = settings.snapshot()["zones"]
    assert "Pacific/Auckland" in zones and "UTC" in zones
    assert zones == sorted(zones)


def test_a_build_with_no_tzdata_offers_no_zones_rather_than_failing(monkeypatch):
    """The picker goes empty and the field still takes a zone typed in. A
    snapshot that raised would take the whole settings page with it."""

    def missing():
        raise zoneinfo.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(zoneinfo, "available_timezones", missing)
    # Cached for the life of the process: cleared going in so this sees its
    # own answer, and going out so no later test reads the empty one.
    settings.zones.cache_clear()
    try:
        assert settings.zones() == []
    finally:
        settings.zones.cache_clear()


def test_a_media_dir_holding_the_separator_is_refused(settings_state):
    """It would come back as two directories, both wrong, and nothing
    downstream could tell that from what was meant."""
    (problem,) = settings.update({"MEDIA_DIRS": ["/data/films:2024"]})
    assert "cannot contain ':'" in problem
    assert not os.path.exists(os.path.join(config.STATE_DIR, config.SETTINGS_FILE))


def test_the_set_once_settings_are_editable(settings_state):
    """The parking pair, the whole numbers and a pattern all apply to the next
    webhook or sweep, not the next restart."""
    assert (
        settings.update(
            {
                "SKIP_HARDLINKS": False,
                "HARDLINK_RECHECK": "60",
                "MAX_CONCURRENT_REWRITES": "3",
                "FFMPEG_TIMEOUT": "3600",
                "PROBE_TIMEOUT": "30",
                "COMMENTARY_PATTERN": r"comment|kommentar",
            }
        )
        == []
    )
    assert config.SKIP_HARDLINKS is False
    assert config.HARDLINK_RECHECK == 60
    assert config.MAX_CONCURRENT_REWRITES == 3
    assert config.FFMPEG_TIMEOUT == 3600
    assert config.PROBE_TIMEOUT == 30
    assert config.COMMENTARY_RE.search("Kommentar")
    # The numbers travel as the strings the file holds either way, so the page
    # reads back exactly what it sent.
    shot = settings.snapshot()["settings"]
    assert shot["HARDLINK_RECHECK"]["value"] == "60"
    assert shot["MAX_CONCURRENT_REWRITES"]["value"] == "3"
    assert shot["COMMENTARY_PATTERN"]["value"] == r"comment|kommentar"


def test_the_containers_are_editable_from_the_offered_set(settings_state):
    assert settings.snapshot()["containers"] == [".m4v", ".mkv", ".mp4"]
    assert settings.update({"ALLOWED_EXTS": [".mkv"]}) == []
    assert {".mkv"} == config.ALLOWED_EXTS
    assert policy.Policy.from_config().allowed_container("/x/f.mp4") is False
    # One with no muxer would plan a rewrite ffmpeg then chokes on.
    (problem,) = settings.update({"ALLOWED_EXTS": [".mkv", ".avi"]})
    assert "no known muxer" in problem
    assert {".mkv"} == config.ALLOWED_EXTS


def test_a_pattern_that_does_not_compile_is_rolled_back(settings_state):
    assert settings.update({"SDH_PATTERN": r"\bsdh\b"}) == []
    (problem,) = settings.update({"SDH_PATTERN": "sdh("})
    assert "not a valid regex" in problem
    assert config.SDH_RE.pattern == r"\bsdh\b"


def test_clearing_a_pattern_hands_the_built_in_back(settings_state):
    """What the page sends for an emptied field: null, never "". An empty
    pattern compiles and matches every title, so every track would read as
    commentary."""
    built_in = config.COMMENTARY_RE.pattern
    assert settings.update({"COMMENTARY_PATTERN": "comment"}) == []
    assert settings.update({"COMMENTARY_PATTERN": None}) == []
    assert config.COMMENTARY_RE.pattern == built_in


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"MAX_CONCURRENT_REWRITES": "0"}, "must be at least 1"),
        ({"PROBE_TIMEOUT": "0"}, "must be at least 1"),
        ({"HARDLINK_RECHECK": "-1"}, "cannot be negative"),
        ({"FFMPEG_TIMEOUT": "soon"}, "is not a whole number"),
    ],
)
def test_a_number_the_service_could_not_run_on_is_refused(settings_state, change, expected):
    (problem,) = settings.update(change)
    assert expected in problem
    assert not os.path.exists(os.path.join(config.STATE_DIR, config.SETTINGS_FILE))
