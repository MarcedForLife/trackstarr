"""Webhook parsing, the listener and hardlink parking. No media; the
listener tests bind a loopback socket. The credential store itself is
covered by test_auth.py."""

import http.client
import json
import os
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

from trackstarr import auth, config, events, processing, webhook
from trackstarr.arr import AUTH_HEADER, original_of, radarr
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


@pytest.fixture
def listener():
    """A live Handler on a loopback socket, cleaned up with the in-flight set."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), webhook.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()
    with webhook._inflight_lock:
        webhook._inflight.clear()


def request(server, method: str, path: str, body: bytes | None = None, headers=None):
    """One HTTP request against a listener, returning (status code, JSON body)."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def post(server, body: dict, headers: dict | None = None) -> tuple[int, str]:
    """POST a webhook body, returning (status code, answer)."""
    status, answer = request(server, "POST", "/", json.dumps(body).encode(), headers)
    return status, answer["status"]


def movie_body(path: str, folder: str) -> dict:
    return {
        "eventType": "Download",
        "movie": {"id": 1, "folderPath": folder},
        "movieFile": {"path": path},
    }


@pytest.fixture
def media_root(tmp_path):
    """A library directory holding one file, f.mkv."""
    root = tmp_path / "media"
    root.mkdir()
    (root / "f.mkv").write_bytes(b"x")
    return root


def test_post_queues_only_existing_paths(listener, media_root):
    """A POST naming a file this container cannot see (usually a mount
    mismatch) must answer 200 and queue nothing."""
    headers = {AUTH_HEADER: auth.mint("radarr")}

    body = movie_body(str(media_root / "missing.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 0")
    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 1")
    # No worker threads run here, so the accepted job is still queued.
    assert webhook._work_q.get_nowait().path == str(media_root / "f.mkv")


def test_unauthenticated_posts_never_reach_the_queue(listener, media_root):
    auth.mint("radarr")  # provisioned, but not what these requests send
    queued_before = webhook._work_q.qsize()

    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body)[0] == 401
    assert post(listener, body, {AUTH_HEADER: "guessed-wrong"})[0] == 401
    assert post(listener, {"eventType": "Test"})[0] == 401
    assert webhook._work_q.qsize() == queued_before


def test_health_needs_no_secret(listener):
    assert request(listener, "GET", "/health")[0] == 200


def test_ping_is_a_health_check_too(listener):
    """Docker's HEALTHCHECK uses /health; /ping is what the *arrs probe."""
    assert request(listener, "GET", "/ping")[0] == 200


def test_an_unknown_path_is_a_404(listener):
    """Nothing else is served, so a scanner finding this port learns nothing."""
    assert request(listener, "GET", "/admin")[0] == 404


def test_a_body_too_large_is_refused_unread(listener, media_root):
    """A batch import body is a few KB. Anything vastly bigger is a mistake
    or an attack, and must not be read into memory to find out."""
    headers = {AUTH_HEADER: auth.mint("radarr"), "Content-Length": str(webhook._MAX_BODY + 1)}
    conn = http.client.HTTPConnection("127.0.0.1", listener.server_address[1])
    try:
        conn.putrequest("POST", "/")
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        assert conn.getresponse().status == 413
    finally:
        conn.close()


def test_a_body_that_is_not_json_is_a_400(listener):
    headers = {AUTH_HEADER: auth.mint("radarr")}
    status, _ = request(listener, "POST", "/", b"{not json", headers)
    assert status == 400


def test_a_bad_content_length_is_a_400_not_a_crash(listener):
    """A bad Content-Length and unparseable JSON are both ValueErrors, and
    both have to answer rather than drop the connection."""
    headers = {AUTH_HEADER: auth.mint("radarr"), "Content-Length": "not-a-number"}
    conn = http.client.HTTPConnection("127.0.0.1", listener.server_address[1])
    try:
        conn.putrequest("POST", "/")
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        assert conn.getresponse().status == 400
    finally:
        conn.close()


def test_the_arrs_test_button_is_answered_without_queueing(listener):
    """Saving the connection fires this; a 200 is what makes the *arr accept
    the credential it just sent."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    queued_before = webhook._work_q.qsize()
    assert post(listener, {"eventType": "Test"}, headers) == (200, "test ok")
    assert webhook._work_q.qsize() == queued_before


def test_a_path_already_in_flight_is_not_queued_twice(media_root):
    """A sweep and a webhook can name the same file; the second must not
    queue a rewrite behind the first for a file that is already correct."""
    job = Job(str(media_root / "f.mkv"))
    try:
        assert webhook.enqueue(job) is True
        assert webhook.enqueue(job) is False
        assert webhook._work_q.qsize() == 1
    finally:
        with webhook._inflight_lock:
            webhook._inflight.clear()
        webhook._work_q.get_nowait()


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


def test_a_post_for_a_file_already_in_flight_queues_nothing(listener, media_root):
    """A sweep and a webhook naming the same file is routine; the second must
    answer 200 having queued nothing, not stack a second rewrite behind it."""
    path = str(media_root / "f.mkv")
    with webhook._inflight_lock:
        webhook._inflight.add(path)
    try:
        headers = {AUTH_HEADER: auth.mint("radarr")}
        body = movie_body(path, str(media_root))
        assert post(listener, body, headers) == (200, "queued 0")
    finally:
        with webhook._inflight_lock:
            webhook._inflight.discard(path)


def test_a_released_file_already_in_flight_is_not_queued_twice(parked, seeded_file, tmp_path):
    """The recheck loop and a fresh webhook can free the same file at once."""
    webhook._parked[seeded_file] = Job(seeded_file)
    os.remove(tmp_path / "seed.mkv")
    with webhook._inflight_lock:
        webhook._inflight.add(seeded_file)
    try:
        before = webhook._work_q.qsize()
        webhook._recheck_parked()
        assert webhook._work_q.qsize() == before
        assert seeded_file not in webhook._parked
    finally:
        with webhook._inflight_lock:
            webhook._inflight.discard(seeded_file)
