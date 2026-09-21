"""Structured connection edits preserve identity, credentials and atomic saves."""

import json

import pytest

from trackstarr import arr, config, settings


def update(*operations, **ordinary):
    return settings.update({**ordinary, "arr_instances": list(operations)})


def create(identity="radarr-4k", **values):
    return {"id": identity, "create": True, "values": values}


def test_connection_lifecycle_preserves_identity_and_redacts_credentials(settings_state):
    assert update(create(url="http://4k/", api_key="private", name="UHD")) == []
    instance = config.current().arr_instance("radarr-4k")
    assert instance.url == "http://4k"
    snapshot = settings.snapshot()
    assert "RADARR_4K_URL" not in snapshot["settings"]
    assert "RADARR_API_KEY" not in snapshot["secrets"]
    assert "private" not in json.dumps(snapshot)
    assert "private" not in (settings_state / "settings.json").read_text()
    assert update({"id": instance.id, "values": {"name": "Films", "api_key": ""}}) == []
    assert config.current().arr_instance(instance.id).api_key == "private"
    assert config.current().arr_instance(instance.id).name == "Films"
    assert update({"id": instance.id, "values": {"api_key": None}}) == []
    assert config.current().arr_instance(instance.id).api_key == ""
    assert update({"id": instance.id, "remove": True}) == []
    assert config.current().arr_instance(instance.id) is None
    assert not any(name.startswith("RADARR_4K_") for name in config.current().file)


def test_default_removal_clears_fields_but_keeps_legacy_identity(settings_state):
    assert update({"id": "radarr", "values": {"url": "http://arr", "api_key": "key"}}) == []
    assert update({"id": "radarr", "remove": True}) == []
    assert config.current().arr_instance("radarr") == config.ArrInstanceConfig("radarr")


def test_empty_creation_is_retained_and_unrelated_sources_survive(settings_state):
    assert update(create(), create("sonarr-remote", name="TV")) == []
    assert config.current().arr_instance("radarr-4k") is not None
    assert update({"id": "radarr-4k", "remove": True}, IMDB_RATINGS=False) == []
    assert config.current().arr_instance("sonarr-remote").name == "TV"
    assert not config.current().IMDB_RATINGS


@pytest.mark.parametrize(
    "operation",
    [
        {"id": "radarr", "values": {"url": "http://edited"}},
        {"id": "radarr", "remove": True},
    ],
)
def test_env_locked_edits_reject_entire_batch(settings_state, monkeypatch, operation):
    monkeypatch.setenv("RADARR_URL", "http://environment")
    config.apply(config.load())
    field = settings.snapshot()["arr_instances"][0]["fields"]["url"]
    assert field == {"value": "http://environment", "env": True, "env_name": "RADARR_URL"}
    assert update(create(), operation)
    assert config.current().arr_instance("radarr-4k") is None
    assert not (settings_state / "settings.json").exists()


def test_invalid_fields_roll_back_connections_and_ordinary_settings(settings_state):
    previous = config.current()
    assert update(create(url="invalid"), IMDB_RATINGS=not previous.IMDB_RATINGS)
    assert config.current().arr_instances == previous.arr_instances
    assert config.current().IMDB_RATINGS == previous.IMDB_RATINGS


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"arr_instances": {}}, "must be a list"),
        ({"arr_instances": [None]}, "invalid connection change"),
        ({"arr_instances": [{"id": "radarr", "typo": "x"}]}, "invalid connection change"),
        ({"arr_instances": [{}]}, "needs an id"),
        ({"arr_instances": [{"id": 4}]}, "needs an id"),
        ({"arr_instances": [{"id": "radarr-public"}]}, "invalid connection id"),
        ({"arr_instances": [{"id": "RADARR"}]}, "invalid connection id"),
        ({"arr_instances": [{"id": "radarr"}, {"id": "radarr"}]}, "more than once"),
        ({"RADARR_NAME": "Main", "arr_instances": [{"id": "radarr"}]}, "more than once"),
        ({"arr_instances": [{"id": "radarr", "create": "yes"}]}, "must be boolean"),
        ({"arr_instances": [{"id": "radarr", "remove": "yes"}]}, "must be boolean"),
        ({"arr_instances": [{"id": "radarr", "create": True, "remove": True}]}, "cannot both"),
        ({"arr_instances": [{"id": "radarr", "create": True}]}, "already exists"),
        ({"arr_instances": [{"id": "radarr-missing"}]}, "no longer exists"),
        ({"arr_instances": [{"id": "radarr", "type": "sonarr"}]}, "cannot change type"),
        ({"arr_instances": [{"id": "radarr", "values": []}]}, "unknown fields"),
        ({"arr_instances": [{"id": "radarr", "values": {"password": "x"}}]}, "unknown fields"),
        (
            {"arr_instances": [{"id": "radarr", "remove": True, "values": {"name": "x"}}]},
            "cannot also",
        ),
        ({"arr_instances": [{"id": "radarr", "values": {"api_key": False}}]}, "string or null"),
    ],
)
def test_invalid_operations_do_not_write(settings_state, changes, message):
    assert message in settings.update(changes)[0]
    assert not (settings_state / "settings.json").exists()


def test_creation_conflict_and_stale_update_do_not_retarget_a_connection(settings_state):
    assert update(create(api_key="first")) == []
    assert "already exists" in update(create(api_key="second"))[0]
    assert config.current().arr_instance("radarr-4k").api_key == "first"
    assert update({"id": "radarr-4k", "remove": True}) == []
    assert "no longer exists" in update({"id": "radarr-4k", "values": {"name": "Late"}})[0]


def test_generated_identity_is_not_used_as_a_display_name(settings_state):
    identity = "radarr-" + "a1" * 16
    assert update(create(identity)) == []
    assert arr.source_name(identity) == "Radarr"
    assert settings.snapshot()["arr_instances"][2]["fields"]["name"]["value"] == ""
