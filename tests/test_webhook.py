"""Webhook parsing and hardlink parking. No media, no network."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from trackstarr import config, events, processing, webhook
from trackstarr.arr import original_of, radarr
from trackstarr.planner import Plan
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.webhook import _resolve_lang, jobs_from_hook


def test_radarr_import_webhook():
    jobs = jobs_from_hook(
        {
            "eventType": "Download",
            "movie": {
                "id": 12,
                "folderPath": "/data/media/movies/Film (2024)",
                "originalLanguage": {"name": "Korean"},
            },
            "movieFile": {"relativePath": "Film (2024).mkv"},
        }
    )
    assert [job.path for job in jobs] == ["/data/media/movies/Film (2024)/Film (2024).mkv"]
    assert jobs[0].lang == "kor"
    assert jobs[0].item_id == 12
    assert jobs[0].arr.name == "radarr"


def test_radarr_absolute_path_wins_over_relative():
    jobs = jobs_from_hook(
        {
            "eventType": "Download",
            "movie": {"id": 1, "folderPath": "/wrong"},
            "movieFile": {"path": "/data/media/movies/A/A.mkv", "relativePath": "A.mkv"},
        }
    )
    assert [job.path for job in jobs] == ["/data/media/movies/A/A.mkv"]


def test_sonarr_import_webhook():
    jobs = jobs_from_hook(
        {
            "eventType": "Download",
            "series": {
                "id": 7,
                "path": "/data/media/tv/Show",
                "originalLanguage": {"name": "English"},
            },
            "episodeFile": {"relativePath": "Season 01/S01E01.mkv"},
        }
    )
    assert [job.path for job in jobs] == ["/data/media/tv/Show/Season 01/S01E01.mkv"]
    assert jobs[0].lang == "eng"
    assert jobs[0].item_id == 7
    assert jobs[0].arr.name == "sonarr"


def test_sonarr_multi_file_webhook():
    jobs = jobs_from_hook(
        {
            "eventType": "Download",
            "series": {"id": 7, "path": "/data/media/tv/Show"},
            "episodeFiles": [{"relativePath": "a.mkv"}, {"relativePath": "b.mkv"}],
        }
    )
    assert [job.path for job in jobs] == [
        "/data/media/tv/Show/a.mkv",
        "/data/media/tv/Show/b.mkv",
    ]


@pytest.mark.parametrize("event", ["Grab", "Test", "HealthIssue", "ApplicationUpdate"])
def test_uninteresting_events_are_ignored(event):
    assert jobs_from_hook({"eventType": event, "movie": {}}) == []


def test_unknown_payload_shape_is_ignored():
    assert jobs_from_hook({"eventType": "Download"}) == []


def test_missing_relative_path_yields_no_path():
    jobs = jobs_from_hook(
        {
            "eventType": "Download",
            "movie": {"id": 1, "folderPath": "/data/media/movies/A"},
            "movieFile": {},
        }
    )
    assert jobs == []


def test_worker_resolves_missing_language_from_the_arr():
    arr = replace(radarr(), url="http://radarr:7878", key="key")
    arr.item = lambda item_id: {"originalLanguage": {"name": "Korean"}}
    job = _resolve_lang(Job("/x.mkv", None, 5, arr))
    assert job.lang == "kor"


def test_worker_keeps_a_language_the_webhook_already_carried():
    arr = replace(radarr(), url="http://radarr:7878", key="key")
    arr.item = lambda item_id: pytest.fail("the API must not be queried")
    job = _resolve_lang(Job("/x.mkv", "eng", 5, arr))
    assert job.lang == "eng"


def test_dry_run_never_rewrites_a_webhook_import(monkeypatch):
    """The DRY_RUN latch lives inside process(), so the handler's plain
    dry_run=False must still end as a would-fix, never a rewrite."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    plan = Plan(path="/x.mkv", reasons=["reorder streams"])
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("DRY_RUN must not rewrite")
    )
    webhook._handle(Job("/x.mkv"))
    (entry,) = list(events.read())
    assert entry["event"] == "would-fix"


def test_original_of_handles_missing_fields():
    assert original_of(None) is None
    assert original_of({}) is None
    assert original_of({"originalLanguage": {}}) is None
    assert original_of({"originalLanguage": {"name": "Unknown"}}) is None


@pytest.fixture
def parked(monkeypatch):
    """SKIP_HARDLINKS on, with a clean parked set before and after."""
    monkeypatch.setattr(config, "SKIP_HARDLINKS", True)
    webhook._parked.clear()
    yield webhook._parked
    webhook._parked.clear()


@pytest.fixture
def seeded_file(tmp_path) -> str:
    """A library file the download client still hard-links."""
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    os.link(path, tmp_path / "seed.mkv")
    return str(path)


def test_seeded_import_is_parked_not_processed(parked, seeded_file, monkeypatch):
    processed = []
    monkeypatch.setattr(webhook, "process", lambda job, dry_run: processed.append(job))
    webhook._handle(Job(seeded_file))
    assert seeded_file in parked
    assert processed == []


def test_parking_requires_the_option(parked, seeded_file, monkeypatch):
    monkeypatch.setattr(config, "SKIP_HARDLINKS", False)
    processed = []
    monkeypatch.setattr(
        webhook,
        "process",
        lambda job, dry_run: processed.append(job) or ProcessResult(Status.CONFORM),
    )
    webhook._handle(Job(seeded_file))
    assert parked == {}
    assert [job.path for job in processed] == [seeded_file]


def test_release_queues_the_parked_job(parked, seeded_file, tmp_path, monkeypatch):
    queued = []
    monkeypatch.setattr(webhook, "enqueue", lambda job: queued.append(job) is None)
    webhook._park(Job(seeded_file))

    webhook._recheck_parked()
    assert seeded_file in parked
    assert queued == []

    os.unlink(tmp_path / "seed.mkv")
    webhook._recheck_parked()
    assert parked == {}
    assert [job.path for job in queued] == [seeded_file]


def test_vanished_parked_file_is_dropped(parked, monkeypatch):
    queued = []
    monkeypatch.setattr(webhook, "enqueue", lambda job: queued.append(job) is None)
    webhook._park(Job("/nowhere/f.mkv"))

    webhook._recheck_parked()
    assert parked == {}
    assert queued == []
