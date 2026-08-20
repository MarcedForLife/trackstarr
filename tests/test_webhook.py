"""Webhook parsing, the listener and hardlink parking. The listener tests
bind a loopback socket; the credential store is test_auth.py's."""

import http.client
import json
import os
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from conftest import configured_arr, needed_plan, read_events
from trackstarr import auth, config, processing, webhook
from trackstarr.arr import AUTH_HEADER
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.webhook import _resolve_lang, jobs_from_hook


@pytest.fixture(autouse=True)
def _drain_queue():
    """The queue and the in-flight set are module state, so a test that queues a
    job must not leave it for the next one."""
    yield
    with webhook._inflight_lock:
        webhook._inflight.clear()
    while not webhook._work_q.empty():
        webhook._work_q.get_nowait()


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


def test_jobs_share_the_run_the_delivery_was_stamped_with():
    """A season import is one POST with many files; its rewrites group in the
    history the way one sweep's do."""
    jobs = jobs_from_hook(
        {
            "eventType": "Download",
            "series": {"id": 7, "path": "/data/media/tv/Show"},
            "episodeFiles": [{"relativePath": "a.mkv"}, {"relativePath": "b.mkv"}],
        },
        "2026-08-20T03:00:00+12:00#abcd",
    )
    assert {job.run for job in jobs} == {"2026-08-20T03:00:00+12:00#abcd"}


@pytest.mark.parametrize(
    "event", ["Grab", "Test", "HealthIssue", "ApplicationUpdate", "Rename"]
)
def test_uninteresting_events_are_ignored(event):
    """Rename included: its body carries files only under renamed*Files keys
    nothing here reads, and a rename changes no track content."""
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
    arr = configured_arr()
    arr.item = lambda item_id: {"originalLanguage": {"name": "Korean"}}
    job = _resolve_lang(Job("/x.mkv", None, 5, arr))
    assert job.lang == "kor"


def test_worker_keeps_a_language_the_webhook_already_carried():
    arr = configured_arr()
    arr.item = lambda item_id: pytest.fail("the API must not be queried")
    job = _resolve_lang(Job("/x.mkv", "eng", 5, arr))
    assert job.lang == "eng"


def test_dry_run_never_rewrites_a_webhook_import(monkeypatch):
    """The DRY_RUN latch lives inside process(), so the handler's plain
    dry_run=False still has to end as a would-fix."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("DRY_RUN must not rewrite")
    )
    webhook._handle(Job("/x.mkv"))
    (entry,) = read_events()
    assert entry["event"] == "would-fix"


def test_the_worker_labels_history_with_the_delivery_run(monkeypatch):
    """Under DRY_RUN the would-fix entry is the webhook's only record, so it
    has to carry the run the delivery minted."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: needed_plan())
    webhook._handle(Job("/x.mkv", run="r#1"))
    (entry,) = read_events()
    assert entry["run"] == "r#1"


@pytest.fixture
def listener():
    """A live Handler on a loopback socket."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), webhook.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


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
    status, answer = request(
        server, "POST", webhook.WEBHOOK_PATH, json.dumps(body).encode(), headers
    )
    return status, answer["status"]


def post_headers_only(server, headers: dict) -> int:
    """Announce a POST and send no body, so the listener has to answer on the
    headers alone. Returns the status code."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        conn.putrequest("POST", webhook.WEBHOOK_PATH)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        return conn.getresponse().status
    finally:
        conn.close()


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
    """A POST naming a file this container cannot see, usually a mount mismatch,
    answers 200 and queues nothing."""
    headers = {AUTH_HEADER: auth.mint("radarr")}

    body = movie_body(str(media_root / "missing.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 0")
    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 1")
    # No worker threads run here, so the accepted job is still queued.
    assert webhook._work_q.get_nowait().path == str(media_root / "f.mkv")


def test_a_queued_post_records_the_delivery(listener, media_root):
    """The run exists in the history before its rewrites do, so a runs view
    can show work still queued."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    post(listener, movie_body(str(media_root / "f.mkv"), str(media_root)), headers)
    (entry,) = read_events()
    assert entry["event"] == "webhook"
    assert entry["arr"] == "radarr"
    assert entry["files"] == 1
    assert entry["run"] == webhook._work_q.get_nowait().run


def test_a_post_that_queues_nothing_records_nothing(listener, media_root):
    """Test buttons and mount mismatches are not runs; recording them would
    fill the history with entries no rewrite ever joins."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    post(listener, movie_body(str(media_root / "missing.mkv"), str(media_root)), headers)
    post(listener, {"eventType": "Test"}, headers)
    assert read_events() == []


def test_unauthenticated_posts_never_reach_the_queue(listener, media_root):
    auth.mint("radarr")  # provisioned, but not what these requests send

    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body)[0] == 401
    assert post(listener, body, {AUTH_HEADER: "guessed-wrong"})[0] == 401
    assert post(listener, {"eventType": "Test"})[0] == 401
    assert webhook._work_q.qsize() == 0


def test_health_needs_no_secret(listener):
    assert request(listener, "GET", "/health")[0] == 200


def test_an_unknown_path_is_a_404(listener):
    """Nothing else is served, so a scanner finding this port learns nothing."""
    assert request(listener, "GET", "/admin")[0] == 404


def test_a_post_anywhere_but_the_webhook_path_is_a_404(listener, media_root):
    """POST is /webhook only, so the rest of the namespace stays free for a
    future API instead of every path being the webhook forever."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    body = json.dumps(movie_body(str(media_root / "f.mkv"), str(media_root))).encode()
    for path in ("/", "/api/v1/queue"):
        assert request(listener, "POST", path, body, headers)[0] == 404
    assert webhook._work_q.qsize() == 0


def test_registration_points_the_arrs_at_the_webhook_path(monkeypatch):
    """The base URL is the setting; the path is this listener's own contract,
    appended here so both ends always agree."""
    monkeypatch.setattr(config, "WEBHOOK_URL", "http://trackstarr:5120")
    assert webhook.webhook_url() == "http://trackstarr:5120/webhook"


def test_a_silent_connection_is_dropped_rather_than_held(listener, monkeypatch):
    """Python leaves BaseHTTPRequestHandler.timeout unset, so a peer that
    connects and says nothing holds a server thread indefinitely, without
    authenticating, since the secret is checked long after the request line."""
    assert webhook.Handler.timeout, "a finite timeout has to ship, not just be testable"
    monkeypatch.setattr(webhook.Handler, "timeout", 0.2)

    sock = socket.create_connection(("127.0.0.1", listener.server_address[1]))
    try:
        # Long enough that a hung server fails the test rather than the client.
        sock.settimeout(10)
        # No request line, ever. The server has to give up on its own.
        assert sock.recv(64) == b""
    finally:
        sock.close()


def test_a_body_too_large_is_refused_unread(listener):
    """A batch import body is a few KB. Anything vastly bigger is a mistake
    or an attack, and must not be read into memory to find out."""
    headers = {AUTH_HEADER: auth.mint("radarr"), "Content-Length": str(webhook._MAX_BODY + 1)}
    assert post_headers_only(listener, headers) == 413


def test_a_body_that_is_not_json_is_a_400(listener):
    headers = {AUTH_HEADER: auth.mint("radarr")}
    status, _ = request(listener, "POST", webhook.WEBHOOK_PATH, b"{not json", headers)
    assert status == 400


def test_a_bad_content_length_is_a_400_not_a_crash(listener):
    """A bad Content-Length and unparseable JSON are both ValueErrors, and both
    have to answer rather than drop the connection."""
    headers = {AUTH_HEADER: auth.mint("radarr"), "Content-Length": "not-a-number"}
    assert post_headers_only(listener, headers) == 400


def test_the_arrs_test_button_is_answered_without_queueing(listener):
    """Saving the connection fires this, and the 200 is what makes the *arr accept
    the credential it just sent."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, {"eventType": "Test"}, headers) == (200, "test ok")
    assert webhook._work_q.qsize() == 0


def test_a_path_already_in_flight_is_not_queued_twice(media_root):
    """A sweep and a webhook can name the same file; the second must not
    queue a rewrite behind the first for a file that is already correct."""
    job = Job(str(media_root / "f.mkv"))
    assert webhook.enqueue(job) is True
    assert webhook.enqueue(job) is False
    assert webhook._work_q.qsize() == 1


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
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, movie_body(path, str(media_root)), headers) == (200, "queued 0")


def test_parking_survives_a_restart(parked, seeded_file):
    """Nothing re-fires an import and SWEEP_AT is unset by default, so a set
    lost to a restart is a file nothing comes back to."""
    webhook._park(Job(seeded_file, "kor", 12, configured_arr("sonarr"), "r#1"))

    parked.clear()  # stand in for the process going away
    webhook.load_parked()

    (job,) = parked.values()
    assert job.path == seeded_file
    assert job.lang == "kor"
    assert job.item_id == 12
    # Stored by name and rebuilt from current config, so a job restored after
    # its *arr was reconfigured carries the new settings, not the old ones.
    assert job.arr.name == "sonarr"
    # The delivery's run rides along, so a rewrite finished days after its
    # import still groups with it.
    assert job.run == "r#1"


def test_a_parked_job_with_no_arr_round_trips(parked, seeded_file):
    """`fix` and a hand-rolled client both queue jobs matched to nothing."""
    webhook._park(Job(seeded_file))

    parked.clear()
    webhook.load_parked()

    (job,) = parked.values()
    assert (job.lang, job.item_id, job.arr) == (None, None, None)


def test_releasing_the_last_parked_file_clears_the_stored_set(
    parked, seeded_file, tmp_path, monkeypatch
):
    """Otherwise the next restart restores a file that was long since done."""
    monkeypatch.setattr(webhook, "enqueue", lambda job: True)
    webhook._park(Job(seeded_file))
    os.unlink(tmp_path / "seed.mkv")

    webhook._recheck_parked()

    parked.clear()
    webhook.load_parked()
    assert parked == {}


def test_no_stored_set_restores_nothing(parked):
    webhook.load_parked()
    assert parked == {}


@pytest.mark.parametrize(
    "content",
    ["not json", '{"path": "/x.mkv"}', '[["/x.mkv"], {}, {"lang": "eng"}]'],
    ids=["unparseable", "not a list", "entries with no path"],
)
def test_a_damaged_stored_set_restores_nothing(parked, content):
    """Advisory state: a mangled file costs the parked entries, not the
    listener that was about to start."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(webhook._parked_path(), "w") as parked_file:
        parked_file.write(content)

    webhook.load_parked()
    assert parked == {}


def test_an_unwritable_state_dir_does_not_fail_an_import(
    parked, seeded_file, tmp_path, monkeypatch, caplog
):
    """Persisting is a convenience; the in-memory set still works without it."""
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker / "under-a-file"))

    webhook._park(Job(seeded_file))

    assert seeded_file in parked
    assert "could not persist the parked set" in caplog.text


def test_a_released_file_already_in_flight_is_not_queued_twice(parked, seeded_file, tmp_path):
    """The recheck loop and a fresh webhook can free the same file at once."""
    webhook._parked[seeded_file] = Job(seeded_file)
    os.remove(tmp_path / "seed.mkv")
    with webhook._inflight_lock:
        webhook._inflight.add(seeded_file)

    webhook._recheck_parked()
    assert webhook._work_q.qsize() == 0
    assert seeded_file not in webhook._parked
