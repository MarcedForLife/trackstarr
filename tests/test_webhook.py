"""The *arr intake: what a delivery parses into, and what the /webhook POST
queues. The listener itself is test_server.py's and the queue behind the
intake is test_jobs.py's."""

import http.client
import json
import os
import socket

import pytest

from conftest import keep_alive, read_events, request
from trackstarr import auth, jobs, webhook
from trackstarr.arr import AUTH_HEADER
from trackstarr.webhook import jobs_from_hook


def test_radarr_import_webhook():
    delivered = jobs_from_hook(
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
    assert [job.path for job in delivered] == ["/data/media/movies/Film (2024)/Film (2024).mkv"]
    assert delivered[0].lang == "kor"
    assert delivered[0].item_id == 12
    assert delivered[0].arr.name == "radarr"


def test_radarr_absolute_path_wins_over_relative():
    delivered = jobs_from_hook(
        {
            "eventType": "Download",
            "movie": {"id": 1, "folderPath": "/wrong"},
            "movieFile": {"path": "/data/media/movies/A/A.mkv", "relativePath": "A.mkv"},
        }
    )
    assert [job.path for job in delivered] == ["/data/media/movies/A/A.mkv"]


def test_sonarr_import_webhook():
    delivered = jobs_from_hook(
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
    assert [job.path for job in delivered] == ["/data/media/tv/Show/Season 01/S01E01.mkv"]
    assert delivered[0].lang == "eng"
    assert delivered[0].item_id == 7
    assert delivered[0].arr.name == "sonarr"


def test_sonarr_multi_file_webhook():
    delivered = jobs_from_hook(
        {
            "eventType": "Download",
            "series": {"id": 7, "path": "/data/media/tv/Show"},
            "episodeFiles": [{"relativePath": "a.mkv"}, {"relativePath": "b.mkv"}],
        }
    )
    assert [job.path for job in delivered] == [
        "/data/media/tv/Show/a.mkv",
        "/data/media/tv/Show/b.mkv",
    ]


def test_jobs_share_the_run_the_delivery_was_stamped_with():
    """A season import is one POST with many files; its rewrites group in the
    history the way one sweep's do."""
    delivered = jobs_from_hook(
        {
            "eventType": "Download",
            "series": {"id": 7, "path": "/data/media/tv/Show"},
            "episodeFiles": [{"relativePath": "a.mkv"}, {"relativePath": "b.mkv"}],
        },
        "2026-08-20T03:00:00+12:00#abcd",
    )
    assert {job.run for job in delivered} == {"2026-08-20T03:00:00+12:00#abcd"}


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
    delivered = jobs_from_hook(
        {
            "eventType": "Download",
            "movie": {"id": 1, "folderPath": "/data/media/movies/A"},
            "movieFile": {},
        }
    )
    assert delivered == []


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


@pytest.fixture
def queued(monkeypatch):
    """Every job the listener handed to the queue."""
    taken: list = []
    monkeypatch.setattr(jobs, "enqueue", lambda job: taken.append(job) is None)
    return taken


def test_post_queues_only_existing_paths(listener, media_root, queued):
    """A POST naming a file this container cannot see, usually a mount mismatch,
    answers 200 and queues nothing."""
    headers = {AUTH_HEADER: auth.mint("radarr")}

    body = movie_body(str(media_root / "missing.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 0")
    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 1")
    assert [job.path for job in queued] == [str(media_root / "f.mkv")]


def test_a_queued_post_records_the_delivery(listener, media_root, queued):
    """The run exists in the history before its rewrites do, so a runs view
    can show work still queued."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    post(listener, movie_body(str(media_root / "f.mkv"), str(media_root)), headers)
    (entry,) = read_events()
    assert entry["event"] == "webhook"
    assert entry["arr"] == "radarr"
    assert entry["files"] == 1
    assert entry["run"] == queued[0].run


def test_a_delivery_names_the_files_it_queued(listener, media_root):
    """A delivery whose files all conform leaves no other line, so the count
    alone would say a season arrived and never say which episodes."""
    headers = {AUTH_HEADER: auth.mint("sonarr")}
    (media_root / "S01E01.mkv").write_bytes(b"x")
    body = {
        "eventType": "Download",
        "series": {"id": 7, "path": str(media_root)},
        "episodeFiles": [
            {"relativePath": "S01E01.mkv"},
            # Queued by the *arr, absent here: usually a mount mismatch.
            {"relativePath": "S01E02.mkv"},
        ],
    }

    post(listener, body, headers)

    (entry,) = read_events()
    # Only what was really queued, so the names and the count cannot disagree.
    assert entry["paths"] == [str(media_root / "S01E01.mkv")]
    assert entry["files"] == len(entry["paths"])


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
    assert jobs.queued_count() == 0


def test_a_post_anywhere_but_the_webhook_path_is_a_404(listener, media_root):
    """POST is /webhook only, so the rest of the namespace stays free for the
    API instead of every path being the webhook forever."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    body = json.dumps(movie_body(str(media_root / "f.mkv"), str(media_root))).encode()
    assert request(listener, "POST", "/", body, headers)[0] == 404
    # Under /api the same POST is the API's business, and a machine secret is
    # no session there, so the webhook body never reaches the queue either way.
    api_headers = headers | {"Content-Type": "application/json"}
    assert request(listener, "POST", "/api/v1/queue", body, api_headers)[0] == 401
    assert jobs.queued_count() == 0


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
    assert jobs.queued_count() == 0


def test_a_post_for_a_file_already_in_flight_queues_nothing(listener, media_root):
    """A sweep and a webhook naming the same file is routine; the second must
    answer 200 having queued nothing, not stack a second rewrite behind it."""
    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, body, headers) == (200, "queued 1")
    # No workers run here, so the first is still in flight.
    assert post(listener, body, headers) == (200, "queued 0")


def test_a_delivery_is_one_run_on_the_activity_page(listener, media_root, clean_registry):
    """Not a run per file: a season import is one thing that happened, and the
    page has to show it as one line however many files it carried."""
    first = os.path.join(media_root, "s01e01.mkv")
    second = os.path.join(media_root, "s01e02.mkv")
    for path in (first, second):
        open(path, "w").close()
    body = {
        "eventType": "Download",
        "series": {"id": 7, "path": str(media_root)},
        "episodeFile": {"path": first},
    }
    post(listener, body, {AUTH_HEADER: auth.mint("sonarr")})

    (run,) = clean_registry.snapshot()["runs"]
    assert run["kind"] == "import"
    assert (run["label"], run["total"], run["done"]) == ("sonarr", 1, 0)


def test_a_delivery_that_names_no_files_registers_nothing(listener, clean_registry):
    """A Download whose file carries no path at all: there is no work, so
    there must be no run sitting on the activity page either."""
    body = {"eventType": "Download", "movie": {"id": 1, "folderPath": "/data"}, "movieFile": {}}
    assert post(listener, body, {AUTH_HEADER: auth.mint("radarr")}) == (200, "queued 0")
    assert clean_registry.snapshot()["runs"] == []


def test_an_unauthorized_webhook_closes_rather_than_desyncing(listener):
    conn = keep_alive(listener)
    try:
        conn.request("POST", webhook.WEBHOOK_PATH, b'{"eventType": "Test"}')
        response = conn.getresponse()
        assert response.status == 401
        assert response.will_close
    finally:
        conn.close()
