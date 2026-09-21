"""Separate variants must never share credentials, IDs, paths or rescan targets."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from conftest import api, cache, configured_arr, pending, set_config
from trackstarr import api as api_module
from trackstarr import (
    arr,
    auth,
    config,
    connections,
    covers,
    jobs,
    library,
    links,
    pauses,
    settings,
)
from trackstarr.api import _serve_title, _serve_title_work
from trackstarr.status import Status
from trackstarr.sweep_cache import Verdict
from trackstarr.webhook import jobs_from_hook


def configure():
    set_config(
        RADARR_URL="http://radarr",
        RADARR_API_KEY="default-key",
        arr_settings={
            "RADARR_4K_URL": "http://radarr4k",
            "RADARR_4K_API_KEY": "4k-key",
            "RADARR_4K_PUBLIC_URL": "https://4k.example",
        },
    )


def hook(path="/movies4k/Film/file.mkv"):
    return {"eventType": "Download", "movie": {"id": 1}, "movieFile": {"path": path}}


def test_named_settings_are_sealed_redacted_and_removed(settings_state):
    changes = {"RADARR_4K_URL": "http://radarr4k/", "RADARR_4K_API_KEY": "private-key"}
    assert settings.update(changes) == []
    assert "private-key" not in (settings_state / "settings.json").read_text()
    assert config.value("RADARR_4K_API_KEY") == "private-key"
    assert settings.snapshot()["arr_instances"][2]["fields"]["api_key"] == {
        "value": "",
        "env": False,
        "set": True,
        "env_name": "RADARR_4K_API_KEY",
    }
    assert "private-key" not in json.dumps(settings._recorded())
    assert config.value("RADARR_4K_URL") == "http://radarr4k"
    assert not config.warnings()
    assert settings.update({"RADARR_4K_URL": "invalid"})
    assert config.value("RADARR_4K_URL") == "http://radarr4k"
    assert settings.update(dict.fromkeys(changes)) == []
    assert [instance.id for instance in config.current().arr_instances] == ["radarr", "sonarr"]


@pytest.mark.parametrize(
    "form",
    ["SONARR_REMOTE_API_KEY", "SONARR_REMOTE_API_KEY_FILE", "FILE__SONARR_REMOTE_API_KEY"],
)
def test_named_environment_secrets(settings_state, monkeypatch, tmp_path, form):
    secret_file = tmp_path / "key"
    secret_file.write_text("private-key")
    monkeypatch.setenv(
        form,
        "private-key"
        if form.endswith("API_KEY") and not form.startswith("FILE__")
        else str(secret_file),
    )
    monkeypatch.setenv("SONARR_REMOTE_URL", "http://sonarr-remote")
    config.apply(config.load())
    assert config.value("SONARR_REMOTE_API_KEY") == "private-key"
    assert settings.env_pinned("SONARR_REMOTE_API_KEY")
    assert settings.update({"SONARR_REMOTE_API_KEY": "replacement"})
    remote = arr.all_arrs()[-1]
    assert (remote.instance_id, remote.type, remote.body_key) == (
        "sonarr-remote",
        "sonarr",
        "series",
    )


def test_partial_instance_warns(settings_state):
    assert settings.update({"RADARR_4K_URL": "http://radarr4k"}) == []
    assert any("RADARR_4K_API_KEY" in warning for warning in config.warnings())


def test_names_are_read_as_an_answer_is_built(settings_state):
    assert arr.source_name("radarr") == "Radarr"
    assert arr.source_name("sonarr-remote") == "Sonarr remote"
    assert settings.update({"RADARR_NAME": "Main", "RADARR_4K_NAME": "UHD shelf"}) == []
    assert arr.source_name("radarr") == "Main"
    assert arr.source_name("radarr-4k") == "UHD shelf"
    assert connections.service_for("radarr").label == "Main"
    assert connections.service_for("radarr-4k").label == "UHD shelf"
    assert settings.snapshot()["arr_instances"][2]["fields"]["name"] == {
        "value": "UHD shelf",
        "env": False,
        "env_name": "RADARR_4K_NAME",
    }
    # A name is a grid line, so it is kept to what one can hold.
    (problem,) = settings.update({"RADARR_4K_NAME": "UHD (shelf)"})
    assert "letters, numbers" in problem
    assert arr.source_name("radarr-4k") == "UHD shelf"
    # A name alone does not stand an instance up; that still takes both halves.
    assert not any(arr_.instance_id == "radarr-4k" and arr_.enabled for arr_ in arr.all_arrs())


def test_public_url_remains_a_default_setting():
    assert not config.arr_setting("RADARR_PUBLIC_URL")
    assert not config.arr_setting("RADARR_../_URL")
    assert not settings.editable("RADARR_4K_PASSWORD")


def test_hooks_route_by_registered_identity_and_refuse_ambiguity(monkeypatch):
    configure()
    calls = []
    monkeypatch.setattr(
        arr,
        "request",
        lambda url, headers, payload, *args: calls.append((url, headers, payload)),
    )
    job = jobs_from_hook(hook(), caller="radarr-4k")[0]
    assert job.arr.instance_id == "radarr-4k"
    job.arr.rescan(job.item_id)
    assert calls == [
        (
            "http://radarr4k/api/v3/command",
            {"X-Api-Key": "4k-key"},
            {"name": "RescanMovie", "movieId": 1},
        )
    ]
    with pytest.raises(ValueError, match="ambiguous"):
        jobs_from_hook(hook())
    assert jobs_from_hook(hook(), caller="radarr")[0].arr.instance_id == "radarr"
    set_config(SONARR_URL="http://sonarr", SONARR_API_KEY="sonarr-key")
    assert jobs_from_hook(hook(), caller="sonarr") == []
    with pytest.raises(ValueError, match="no longer enabled"):
        jobs_from_hook(hook(), caller="radarr-removed")
    set_config(RADARR_URL="", RADARR_API_KEY="")
    assert jobs_from_hook(hook())[0].arr.instance_id == "radarr-4k"
    set_config(
        arr_settings={"RADARR_4K_URL": "", "RADARR_4K_API_KEY": "", "RADARR_4K_PUBLIC_URL": ""}
    )
    with pytest.raises(ValueError, match="no longer enabled"):
        jobs_from_hook(hook(), caller="radarr-4k")


@pytest.mark.parametrize("caller", ["radarr", "sonarr"])
@pytest.mark.parametrize("removed", [("URL",), ("API_KEY",), ("URL", "API_KEY")])
def test_default_hooks_refuse_disabled_connections(caller, removed):
    prefix = caller.upper()
    set_config(**{f"{prefix}_URL": "http://arr", f"{prefix}_API_KEY": "key"})
    body = {
        "eventType": "Download",
        "movie" if caller == "radarr" else "series": {"id": 1},
        "movieFile" if caller == "radarr" else "episodeFile": {"path": "/media/file.mkv"},
    }
    assert len(jobs_from_hook(body, caller=caller)) == 1
    set_config(**{f"{prefix}_{suffix}": "" for suffix in removed})
    with pytest.raises(ValueError, match="no longer enabled"):
        jobs_from_hook(body, caller=caller)


def test_http_hook_uses_callers_secret(listener, monkeypatch, tmp_path):
    configure()
    path = tmp_path / "film.mkv"
    path.touch()
    delivered = []
    monkeypatch.setattr(jobs, "enqueue", lambda job: delivered.append(job) or True)
    status, _, _ = api(
        listener,
        "POST",
        "/webhook",
        hook(str(path)),
        headers={"X-Api-Key": auth.mint("radarr-4k")},
    )
    assert status == 200
    assert delivered[0].arr.instance_id == "radarr-4k"
    status, _, _ = api(
        listener,
        "POST",
        "/webhook",
        hook(str(path)),
        headers={"X-Api-Key": auth.mint("manual")},
    )
    assert status == 400
    assert len(delivered) == 1


def test_same_provider_id_across_instances_is_one_title(monkeypatch):
    library.forget()
    one = configured_arr()
    two = replace(one, instance_id="radarr-4k", url="http://radarr4k")
    one.all_items = lambda: [{"id": 1, "tmdbId": 42, "title": "Film", "path": "/movies/Film"}]
    two.all_items = lambda: [{"id": 1, "tmdbId": 42, "title": "Film", "path": "/movies4k/Film"}]
    monkeypatch.setattr(library, "all_arrs", lambda: [one, two])
    cache(
        ("/movies/Film/1080p.mkv", pending()),
        ("/movies/Film/720p.mkv", pending()),
        ("/movies4k/Film/2160p.mkv", pending()),
    )
    # Imports still route by folder to the instance that claims it.
    index = arr.path_index([one, two])
    assert arr.match_path(index, "/movies4k/Film/2160p.mkv").arr.instance_id == "radarr-4k"
    detail = library.title("arr:radarr:1")
    assert detail["total"] == 3 and "variants" not in detail
    assert detail["folder"] == "/movies/Film"
    # Labels ride along with the IDs, read as the answer is built.
    assert detail["folders"] == [
        {"source": "Radarr", "folder": "/movies/Film"},
        {"source": "Radarr 4k", "folder": "/movies4k/Film"},
    ]
    assert {file["name"]: file["source"] for file in detail["files"]} == {
        "1080p.mkv": "Radarr",
        "720p.mkv": "Radarr",
        "2160p.mkv": "Radarr 4k",
    }
    # The secondary instance's own id is an alias for the same title.
    assert library.title("arr:radarr-4k:1")["id"] == "arr:radarr:1"
    assert [title.folders for title in library.selected(["arr:radarr-4k:1"])] == [
        ("/movies/Film", "/movies4k/Film")
    ]
    cards = library.shelf()["titles"]
    assert [(card["id"], card["source_count"], card["files"]) for card in cards] == [
        ("arr:radarr:1", 2, 3)
    ]
    assert "source" not in cards[0]
    owners, _ = library.cards_for_paths(["/movies4k/Film/2160p.mkv"])
    assert owners == {"/movies4k/Film/2160p.mkv": "arr:radarr:1"}
    assert library.covers_for_paths(["/movies4k/Film/2160p.mkv"]) == {
        "/movies4k/Film/2160p.mkv": {"id": "arr:radarr:1", "name": "Film"}
    }
    # Without the primary, the next source takes over the id.
    monkeypatch.setattr(library, "all_arrs", lambda: [two])
    library.forget()
    assert [card["id"] for card in library.shelf()["titles"]] == ["arr:radarr-4k:1"]
    assert "source_count" not in library.shelf()["titles"][0]
    library.forget()


def two_sources(monkeypatch):
    """A film held by the default Radarr and a named 4K one, with a file in each."""
    configure()
    one = configured_arr()
    two = replace(one, instance_id="radarr-4k", url="http://radarr4k")
    one.all_items = lambda: [
        {
            "id": 1,
            "tmdbId": 42,
            "imdbId": "tt1",
            "titleSlug": "film",
            "title": "Film",
            "path": "/movies/Film",
        }
    ]
    two.all_items = lambda: [
        {
            "id": 7,
            "tmdbId": 42,
            "imdbId": "tt1",
            "titleSlug": "film",
            "title": "Film",
            "path": "/movies4k/Film",
        }
    ]
    monkeypatch.setattr(library, "all_arrs", lambda: [one, two])
    cache(("/movies/Film/1080p.mkv", pending()), ("/movies4k/Film/2160p.mkv", pending()))
    return library.selected(["arr:radarr:1"])[0]


def test_title_actions_cover_every_source_folder(monkeypatch):
    library.forget()
    two_sources(monkeypatch)
    both = ["/movies/Film", "/movies4k/Film"]
    assert library.pause_targets(["arr:radarr:1"]) == [
        (folder, "arr:radarr:1", "Film") for folder in both
    ]
    targets, refusal = api_module._pause_targets({"ids": ["arr:radarr:1"]})
    assert refusal is None and [target[0] for target in targets] == both
    assert api_module._pause_targets({"ids": ["arr:radarr:1", "arr:radarr:2"]}) == (
        [],
        (404, "no such title"),
    )
    asked: list = []
    monkeypatch.setattr(
        api_module.lifecycle, "title_work", lambda folders: {"folders": folders}
    )
    _serve_title_work(SimpleNamespace(send_json=asked.append), "id=arr:radarr:1")
    assert asked == [{"folders": tuple(both)}]
    launched: list = []
    monkeypatch.setattr(
        api_module.lifecycle, "launch", lambda fn, args, name: launched.append(args) or True
    )
    handler = SimpleNamespace(
        read_json=lambda: {"ids": ["arr:radarr:1"], "mode": "apply"},
        send_json=asked.append,
        reply=lambda *args: asked.append(args),
    )
    api_module._recheck_titles(handler, SimpleNamespace(name="admin"))
    assert launched[0][0] == both and launched[0][1] is False
    assert asked[-1]["titles"] == 1
    library.forget()


def test_links_and_covers_come_from_every_source(monkeypatch):
    library.forget()
    title = two_sources(monkeypatch)
    subjects = api_module._subjects(title)
    assert [subject.arr for subject in subjects] == ["radarr", "radarr-4k"]
    default_radarr = {"server": "radarr", "label": "Radarr", "url": "http://radarr/movie/film"}
    named_radarr = {
        "server": "radarr",
        "label": "Radarr 4k",
        "url": "https://4k.example/movie/film",
    }
    imdb = {"server": "imdb", "label": "IMDb", "url": "https://www.imdb.com/title/tt1/"}
    assert links.offered(*subjects) == [default_radarr, named_radarr, imdb]
    # A media server is asked about each folder until one has the title.
    set_config(PLEX_URL="http://plex:32400", PLEX_TOKEN="token")
    monkeypatch.setattr(
        links,
        "_remembered",
        lambda server, subject: "http://plex/4k" if subject.folder == "/movies4k/Film" else "",
    )
    assert links.for_title(*subjects) == [
        {"server": "plex", "label": "Plex", "url": "http://plex/4k"},
        default_radarr,
        named_radarr,
        imdb,
    ]
    assert links.for_title(links.Subject("")) == []

    def answer(url, headers=None, timeout=30):
        if url.startswith("http://radarr4k/"):
            return b"\xff\xd8jpeg", "image/jpeg"
        raise OSError("no such poster")

    monkeypatch.setattr(covers, "fetch", answer)
    assert covers._fetch_cover("arr:radarr:1") == b"\xff\xd8jpeg"
    library.forget()


def test_titles_without_a_shared_id_stand_alone(monkeypatch):
    library.forget()
    set_config(MEDIA_DIRS=["/other"])
    one = configured_arr()
    two = replace(one, instance_id="radarr-4k", url="http://radarr4k")
    one.all_items = lambda: [{"id": 1, "title": "Film", "path": "/movies/Film"}]
    two.all_items = lambda: [{"id": 1, "title": "Film", "path": "/movies4k/Film"}]
    monkeypatch.setattr(library, "all_arrs", lambda: [one, two])
    cache(("/other/Film/Film.mkv", pending()))
    cards = library.shelf()["titles"]
    assert sorted(card["id"] for card in cards) == [
        "arr:radarr-4k:1",
        "arr:radarr:1",
        "dir:/other/Film",
    ]
    assert not any("source_count" in card for card in cards)
    swept = library.title("dir:/other/Film")
    assert swept["folders"] == [{"source": "", "folder": "/other/Film"}]
    assert "source" not in swept["files"][0]
    library.forget()


def test_imdb_id_merges_variants_and_any_source_on_disk_counts(monkeypatch):
    library.forget()
    one = configured_arr()
    two = replace(one, instance_id="radarr-4k", url="http://radarr4k")
    one.all_items = lambda: [
        {"id": 1, "imdbId": "tt1", "title": "Film", "path": "/movies/Film", "hasFile": False}
    ]
    two.all_items = lambda: [
        {"id": 9, "imdbId": "tt1", "title": "Film", "path": "/movies4k/Film", "hasFile": True}
    ]
    monkeypatch.setattr(library, "all_arrs", lambda: [one, two])
    cache()
    (card,) = library.shelf()["titles"]
    assert card["id"] == "arr:radarr:1" and card["source_count"] == 2
    assert card["state"] == "unchecked"
    two.all_items = lambda: [
        {"id": 9, "imdbId": "tt1", "title": "Film", "path": "/movies4k/Film", "hasFile": False}
    ]
    library.forget()
    assert library.shelf()["titles"][0]["state"] == "missing"
    library.forget()


def test_named_links_work_without_default_connection():
    configure()
    set_config(RADARR_URL="", RADARR_API_KEY="")
    subject = links.Subject("/movies4k/Film", arr="radarr-4k", slug="42")
    assert links.offered(subject) == [
        {"server": "radarr", "label": "Radarr 4k", "url": "https://4k.example/movie/42"}
    ]
    assert links.for_title(subject) == links.offered(subject)
    assert links.offered(replace(subject, arr="radarr-absent")) == []


def test_connection_check_targets_named_instance(monkeypatch):
    configure()
    calls = []
    monkeypatch.setattr(
        connections, "request", lambda url, *args, **kwargs: calls.append(url) or {}
    )
    monkeypatch.setattr(
        arr.Arr, "webhook_status", lambda self, url: arr.WebhookState(self.instance_id)
    )
    result = connections.check("radarr-4k")
    assert result.ok and result.webhook == "radarr-4k"
    assert calls == [
        "http://radarr4k/api/v3/system/status",
        "http://radarr4k/api/v3/rootfolder",
    ]
    assert connections.service_for("radarr-../") is None
    assert connections.service_for("RADARR_4K") is None
    with pytest.raises(KeyError):
        connections.check("missing")


def test_parked_jobs_keep_the_instance_across_restart():
    configure()
    job = jobs_from_hook(hook(), caller="radarr-4k")[0]
    jobs.park(job)
    jobs.forget()
    jobs.load_parked()
    restored = jobs.parked_jobs()[0]
    assert restored.item_id == 1
    assert restored.arr.instance_id == "radarr-4k"
    assert restored.arr.url == "http://radarr4k"


@pytest.mark.parametrize("identity_key", ["arr", "instance_id"])
def test_missing_parked_connection_keeps_identity_until_it_returns(identity_key):
    configure()
    record = {"path": "/movies4k/Film/file.mkv", "item_id": 1, identity_key: "radarr-4k"}
    path = Path(config.STATE_DIR) / jobs.PARKED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([record]))
    set_config(arr_settings={})
    jobs.load_parked()
    restored = jobs.parked_jobs()[0]
    assert restored.arr is None
    assert restored.instance_id == "radarr-4k"
    jobs.park(restored)
    saved = json.loads(path.read_text())[0]
    assert saved["instance_id"] == "radarr-4k"
    assert "arr" not in saved
    configure()
    jobs.forget()
    jobs.load_parked()
    assert jobs.parked_jobs()[0].arr.instance_id == "radarr-4k"


def test_shared_folder_reports_owner_and_all_competing_instances(monkeypatch, caplog):
    library.forget()
    sources = [
        configured_arr(),
        replace(configured_arr(), instance_id="radarr-4k"),
        replace(configured_arr(), instance_id="radarr-remote"),
    ]
    for source in sources:
        source.all_items = lambda: [{"id": 1, "title": "Film", "path": "/movies/Film/"}]
    monkeypatch.setattr(library, "all_arrs", lambda: sources)
    index = arr.path_index(sources)
    assert index.items["/movies/Film"].arr.instance_id == "radarr"
    assert "radarr owns it; radarr-4k also claims it" in caplog.text
    shelf = library.shelf()
    assert len(shelf["titles"]) == 1
    assert shelf["conflicts"] == [
        {
            "folder": "/movies/Film",
            "owner": {"id": "radarr", "name": "Radarr"},
            "others": [
                {"id": "radarr-4k", "name": "Radarr 4k"},
                {"id": "radarr-remote", "name": "Radarr remote"},
            ],
        }
    ]
    monkeypatch.setattr(library, "all_arrs", lambda: sources[:1])
    library.forget()
    assert "conflicts" not in library.shelf()
    library.forget()


def test_large_series_returns_complete_groups_and_loads_remaining_groups(monkeypatch):
    library.forget()
    source = configured_arr("sonarr")
    source.all_items = lambda: [{"id": 1, "title": "Show", "path": "/tv/Show"}]
    monkeypatch.setattr(library, "all_arrs", lambda: [source])
    cache(
        *((f"/tv/Show/S01E{n:03}.1080p.mkv", pending()) for n in range(1, 202)),
        ("/tv/Show/S01E001.2160p.mkv", Verdict(Status.CONFORM)),
    )
    first = library.title("arr:sonarr:1")
    assert first["total"] == 202
    assert len(first["files"]) == 200
    assert "E201" in first["files"][0]["path"]
    assert not any("E001" in file["path"] for file in first["files"])
    full = library.title("arr:sonarr:1", pages=2)
    assert len(full["files"]) == full["total"] == 202
    assert {file["path"] for file in first["files"]} < {file["path"] for file in full["files"]}
    # The HTTP handler passes the requested page depth to the library.
    answers = []
    handler = SimpleNamespace(send_json=answers.append)
    _serve_title(handler, "id=arr:sonarr:1&pages=2")
    assert len(answers[0]["files"]) == 202
    # Episode 1 stays on the second page when its verdict changes.
    # A cumulative two-page refresh must still contain both of its variants.
    cache(
        *((f"/tv/Show/S01E{n:03}.1080p.mkv", pending()) for n in range(2, 202)),
        ("/tv/Show/S01E001.1080p.mkv", Verdict(Status.CONFORM)),
        ("/tv/Show/S01E001.2160p.mkv", Verdict(Status.CONFORM)),
    )
    refreshed = library.title("arr:sonarr:1")
    assert len(refreshed["files"]) == 200
    assert not any("E001" in file["path"] for file in refreshed["files"])
    expanded = library.title("arr:sonarr:1", pages=2)
    assert {file["path"] for file in expanded["files"]} == {
        file["path"] for file in full["files"]
    }
    assert len([file for file in expanded["files"] if "E001" in file["path"]]) == 2
    library.forget()


@pytest.mark.parametrize("pages", ["0", "-1", "1.5", "no"])
def test_invalid_detail_pages_are_refused(pages):
    replies = []
    handler = SimpleNamespace(reply=lambda *args: replies.append(args))
    _serve_title(handler, f"id=arr:sonarr:1&pages={pages}")
    assert replies == [(400, "pages must be a positive whole number")]


def test_variant_groups_do_not_merge_unknown_or_multi_episode_files():
    fixture = Path(__file__).resolve().parents[1] / "web/src/fixtures/variant-groups.json"
    for case in json.loads(fixture.read_text()):
        assert library._variant_key(case["path"], case["kind"]) == case["key"], case
    assert library._variant_key("/movies/Film.mkv", "movie") == "film"
    assert library._variant_key("/tv/Show/s01e01.mkv", "series") == "S01E01"
    assert library._variant_key("/tv/Show/剧S01E01.mkv", "series") == "S01E01"
    assert library._variant_key("/tv/Show/S01E01-E02.mkv", "series") == "S01E01-E02"
    assert library._variant_key("/tv/Extra.mkv", "series") == "/tv/Extra.mkv"
    assert library._variant_key("/other/S01E01.mkv", "folder") == "/other/S01E01.mkv"


def configure_sonarr():
    set_config(
        SONARR_URL="http://sonarr",
        SONARR_API_KEY="default-key",
        arr_settings={
            "SONARR_REMOTE_URL": "http://sonarr-remote",
            "SONARR_REMOTE_API_KEY": "remote-key",
        },
    )


def episode_hook():
    return {
        "eventType": "Download",
        "series": {"id": 1, "path": "/tv-remote/Show"},
        "episodeFiles": [{"relativePath": "S01E01.mkv"}, {"relativePath": "S01E02.mkv"}],
    }


def test_sonarr_batch_imports_and_colliding_ids_keep_their_rescan_target(monkeypatch):
    configure_sonarr()
    calls = []
    monkeypatch.setattr(arr, "request", lambda *args: calls.append(args))
    remote = jobs_from_hook(episode_hook(), caller="sonarr-remote")
    default_body = {**episode_hook(), "series": {"id": 1, "path": "/tv/Show"}}
    default = jobs_from_hook(default_body, caller="sonarr")
    assert default[0].path == "/tv/Show/S01E01.mkv"
    assert default[0].arr.instance_id == "sonarr"
    assert [job.path for job in remote] == [
        "/tv-remote/Show/S01E01.mkv",
        "/tv-remote/Show/S01E02.mkv",
    ]
    for job in remote:
        assert job.item_id == default[0].item_id == 1
        job.arr.rescan(job.item_id)
    assert len(calls) == 2
    assert all(
        call[:3]
        == (
            "http://sonarr-remote/api/v3/command",
            {"X-Api-Key": "remote-key"},
            {"name": "RescanSeries", "seriesId": 1},
        )
        for call in calls
    )
    with pytest.raises(ValueError, match="ambiguous"):
        jobs_from_hook(episode_hook())
    set_config(arr_settings={})
    with pytest.raises(ValueError, match="no longer enabled"):
        jobs_from_hook(episode_hook(), caller="sonarr-remote")


@pytest.mark.parametrize("change", ["removed", "disabled", "reconfigured"])
def test_parked_sonarr_jobs_use_current_connection_without_falling_back(change, monkeypatch):
    configure_sonarr()
    for job in jobs_from_hook(episode_hook(), caller="sonarr-remote"):
        jobs.park(job)
    values = (
        {}
        if change == "removed"
        else {
            "SONARR_REMOTE_URL": "http://replacement",
            "SONARR_REMOTE_API_KEY": "new-key" if change == "reconfigured" else "",
        }
    )
    set_config(arr_settings=values)
    jobs.forget()
    jobs.load_parked()
    restored = jobs.parked_jobs()
    assert len(restored) == 2
    calls = []
    monkeypatch.setattr(arr, "request", lambda *args: calls.append(args))
    for job in restored:
        assert job.item_id == 1
        if change == "removed":
            assert job.arr is None
        else:
            assert job.arr.instance_id == "sonarr-remote"
            job.arr.rescan(job.item_id)
    if change == "reconfigured":
        assert len(calls) == 2
        assert all(
            call[0] == "http://replacement/api/v3/command"
            and call[1] == {"X-Api-Key": "new-key"}
            for call in calls
        )
    else:
        assert calls == []


def test_alias_pause_actions_share_read_identity_without_waiting(monkeypatch):
    library.forget()
    two_sources(monkeypatch)
    canonical, alias = "arr:radarr:1", "arr:radarr-4k:7"
    expected = [(folder, canonical, "Film") for folder in library.selected([alias])[0].folders]
    entered, release = Event(), Event()

    def blocked(arrs):
        entered.set()
        assert release.wait(5)
        return {}

    monkeypatch.setattr(library, "_from_arrs", blocked)
    monkeypatch.setattr(library, "_read_cache", lambda: pytest.fail("pause read verdicts"))
    library.forget()
    try:
        assert api_module._pause_targets({"ids": [alias]}) == (expected, None)
        assert entered.wait(5)
        # Canonical and alias IDs name the same folders once, and stored pauses
        # carry the canonical title ID regardless of the spelling requested.
        assert api_module._pause_targets({"ids": [canonical, alias, alias]}) == (expected, None)
        assert api_module._pause_targets({"ids": [alias, "absent"]}) == (
            [],
            (404, "no such title"),
        )
    finally:
        release.set()
        library._catalogue.future.result(timeout=5)


@pytest.mark.parametrize("change", ["offline", "removed", "reconfigured", "empty"])
def test_merged_identity_and_pauses_follow_source_changes(monkeypatch, change):
    library.forget()
    title = two_sources(monkeypatch)
    sources = [source.arr for source in title.sources]
    original = list(sources)
    monkeypatch.setattr(library, "all_arrs", lambda: sources)
    canonical, alias = title.id, "arr:radarr-4k:7"
    pauses.place_many(library.pause_targets([alias]), by="admin")
    old_pauses = pauses.as_json()
    assert {pause["title_id"] for pause in old_pauses} == {canonical}
    healthy = original[0].all_items

    def offline():
        raise OSError("offline")

    if change == "removed":
        sources.pop(0)
    elif change == "reconfigured":
        sources[0] = replace(original[0], key="new-key")
        sources[0].all_items = offline
    else:
        original[0].all_items = offline if change == "offline" else list
    library.forget()
    found = library.selected([alias])[0]
    assert found.id == (canonical if change == "offline" else alias)
    assert found.folders == (title.folders if change == "offline" else (title.folders[1],))
    assert library.known().complete == (change in ("removed", "empty"))
    assert library.pause_targets([alias]) == [
        (folder, found.id, found.name) for folder in found.folders
    ]
    # The on-disk pause still protects the surviving file despite its old ID.
    assert pauses.paused(title.folders[1] + "/2160p.mkv").title_id == canonical
    answers = []
    handler = SimpleNamespace(
        read_json=lambda: {"ids": [alias]},
        send_json=answers.append,
        reply=lambda *args: pytest.fail(str(args)),
    )
    api_module._resume_pause(handler, SimpleNamespace(name="admin"))
    assert answers[0]["resumed"] == len(found.sources)
    assert pauses.paused(title.folders[1] + "/2160p.mkv") is None

    original[0].all_items = healthy
    sources[:] = original
    library.forget()
    assert library.selected([alias])[0].id == canonical
    assert library.pause_targets([alias]) == [
        (folder, canonical, title.name) for folder in title.folders
    ]
    # Recovery exposes any hold left on the removed source; resuming the
    # reunited title clears it, without recreating the already lifted hold.
    api_module._resume_pause(handler, SimpleNamespace(name="admin"))
    assert not pauses.current()


def test_title_primary_fields_follow_sources():
    first = library.Source("/first", configured_arr(), 1, "film")
    second = library.Source("/second", configured_arr("sonarr"), 2, "show")
    title = library.Title("title", "Title", (first,), "movie")
    moved = replace(title, sources=(second, first))
    assert (moved.folder, moved.arr, moved.item_id, moved.slug) == (
        second.folder,
        second.arr,
        second.item_id,
        second.slug,
    )
    assert moved.folders == ("/second", "/first")
    with pytest.raises(ValueError, match="at least one source"):
        replace(title, sources=())


def test_arr_clients_share_one_settings_snapshot(monkeypatch):
    configure()
    captured = config.current()
    calls = []

    def current():
        calls.append(None)
        assert len(calls) == 1
        return captured

    monkeypatch.setattr(config, "current", current)
    clients = arr.all_arrs()
    assert [(client.instance_id, client.url, client.key) for client in clients] == [
        (instance.id, instance.url, instance.api_key) for instance in captured.arr_instances
    ]


def test_instances_normalize_defaults_partial_and_named_settings(settings_state, monkeypatch):
    monkeypatch.setenv("RADARR_4K_URL", "http://environment/")
    assert (
        settings.update(
            {
                "RADARR_URL": "http://default/",
                "RADARR_API_KEY": "default-secret",
                "RADARR_PUBLIC_URL": "https://default.example/",
                "SONARR_REMOTE_NAME": "Remote TV",
                "RADARR_4K_API_KEY": "named-secret",
            }
        )
        == []
    )
    captured = config.current()
    assert [instance.id for instance in captured.arr_instances] == [
        "radarr",
        "sonarr",
        "radarr-4k",
        "sonarr-remote",
    ]
    default, _, named, partial = captured.arr_instances
    assert default.browser_url == "https://default.example"
    assert named.url == named.browser_url == "http://environment"
    assert named.type == "radarr"
    assert partial.type == "sonarr"
    assert partial.name == "Remote TV"
    assert partial.url == partial.api_key == ""
    assert "default-secret" not in repr(default)
    with pytest.raises(FrozenInstanceError):
        named.name = "Changed"
    # A name edit does not change identity or mutate an already captured snapshot.
    assert settings.update({"RADARR_4K_NAME": "UHD"}) == []
    assert captured.arr_instance("radarr-4k").name == ""
    assert arr.source_name("radarr-4k") == "UHD"
    assert config.value("RADARR_4K_NAME", captured) == ""
    assert config.current().arr_instance("radarr-missing") is None
    assert config.value("RADARR_MISSING_URL") == ""
