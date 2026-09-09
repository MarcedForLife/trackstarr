"""Webhook parsing and the listener: the API, the served UI and the sockets.
Most tests here bind a loopback socket; the credential store is test_auth.py's
and the queue behind the intake is test_jobs.py's."""

import gzip
import http.client
import json
import logging
import os
import socket
import struct
import threading
import time
import urllib.error

import pytest

from conftest import api, configured_arr, read_events, sign_in
from trackstarr import (
    assets,
    auth,
    config,
    connections,
    covers,
    events,
    holds,
    jobs,
    library,
    links,
    notify,
    ratings,
    runlog,
    runs,
    sessions,
    settings,
    state,
    sweep,
    users,
    webhook,
)
from trackstarr.api import _POST_ROUTES, Access
from trackstarr.arr import AUTH_HEADER
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict
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


def test_health_needs_no_secret(listener):
    assert request(listener, "GET", "/health")[0] == 200


def test_an_unknown_path_is_a_404(listener):
    """With no WEB_DIR nothing else is served, so a scanner finding this port
    learns nothing."""
    assert request(listener, "GET", "/admin")[0] == 404


def get_raw(server, path: str) -> tuple[int, dict, bytes]:
    """One GET, returning (status, headers, body) with the body unparsed."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


@pytest.fixture
def web_dir(tmp_path, monkeypatch):
    """A built UI: the shell plus one hashed asset, with WEB_DIR pointing at it."""
    root = tmp_path / "webui"
    (root / "_app" / "immutable").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>trackstarr</title>")
    (root / "_app" / "immutable" / "app.abc123.js").write_text("console.log('hi')")
    monkeypatch.setattr(config, "WEB_DIR", str(root))
    return root


def test_the_ui_shell_is_served_without_a_secret(listener, web_dir):
    """Any web app's shell is public; everything the pages show comes through
    /api/, which demands a secret."""
    status, headers, body = get_raw(listener, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"trackstarr" in body


def test_spa_routes_fall_back_to_the_shell_but_assets_do_not(listener, web_dir):
    """The SPA router owns /library; a missing asset served as index.html
    would be a module-load error blamed on the wrong file."""
    status, _, body = get_raw(listener, "/library")
    assert status == 200
    assert b"trackstarr" in body
    assert get_raw(listener, "/_app/immutable/gone.js")[0] == 404


def test_hashed_assets_are_served_immutable(listener, web_dir):
    status, headers, _ = get_raw(listener, "/_app/immutable/app.abc123.js")
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert "immutable" in headers["Cache-Control"]


def test_the_shell_is_revalidated(listener, web_dir):
    """index.html keeps its name across releases and names the hashed bundles,
    so a cached copy of the last one asks for files this image dropped. Both
    the shell itself and the SPA routes that fall back to it must say so."""
    for path in ("/", "/library"):
        headers = get_raw(listener, path)[1]
        assert headers["Cache-Control"] == "no-cache"
        assert "immutable" not in headers["Cache-Control"]


def test_the_ui_is_served_with_its_security_headers(listener, web_dir):
    """The shell, the SPA routes that fall back to it, and the bundles it
    names all carry them: a policy the shell alone declares says nothing
    about the script that runs the app."""
    for path in ("/", "/library", "/_app/immutable/app.abc123.js"):
        headers = get_raw(listener, path)[1]
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "same-origin"
        assert headers["X-Frame-Options"] == "DENY"
        policy = headers["Content-Security-Policy"]
        assert "default-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "base-uri 'self'" in policy
        assert "form-action 'self'" in policy
        # data: for the SVG placeholder posters and the inlined woff2 faces,
        # inline styles for Svelte's style: directives.
        assert "img-src 'self' data:" in policy
        assert "font-src 'self' data:" in policy
        assert "style-src 'self' 'unsafe-inline'" in policy
        # Named rather than left to default-src, which is not the same thing:
        # the shell's two inline scripts are the app, and a policy that
        # governs them without hashing them serves a blank page.
        assert "script-src 'self' 'unsafe-inline'" in policy


def test_a_path_cannot_reach_outside_the_web_dir(web_dir, tmp_path):
    """WEB_DIR is the boundary; a crafted URL must not read the volume
    around it."""
    (tmp_path / "secret.txt").write_text("nope")
    assert assets.static_file("../secret.txt") is None
    assert assets.static_file("/_app/../../secret.txt") is None
    # Still serving the real thing after all that suspicion.
    assert assets.static_file("/index.html") == str(web_dir / "index.html")


def test_an_extensionless_miss_without_a_shell_is_a_404(tmp_path, monkeypatch):
    """A SPA route only falls back to index.html when there is one; a WEB_DIR
    with no shell must 404 rather than point at a file that isn't there."""
    root = tmp_path / "webui"
    root.mkdir()
    monkeypatch.setattr(config, "WEB_DIR", str(root))
    assert assets.static_file("/library") is None


def test_a_file_that_cannot_be_read_is_a_404(listener, web_dir, monkeypatch):
    """_static_file vetted the path, but a race can still leave it unopenable;
    the shell must 404 rather than crash the handler thread."""
    monkeypatch.setattr(assets, "static_file", lambda url_path: str(web_dir / "_app"))
    assert get_raw(listener, "/")[0] == 404


def test_the_status_api_needs_a_secret(listener, web_dir, clean_registry):
    assert request(listener, "GET", "/api/status")[0] == 401

    headers = {AUTH_HEADER: auth.mint("browser")}
    status, payload = request(listener, "GET", "/api/status", headers=headers)
    assert status == 200
    assert payload["version"]
    # An idle service owes the library nothing. The registry is cleared for
    # this one because the queue is counted off it now, and a run another test
    # left behind is a service with work to do.
    assert (payload["queue"], payload["working"], payload["parked"]) == (0, 0, 0)
    assert request(listener, "GET", "/api/nothing", headers=headers)[0] == 404


def test_the_events_api_pages_the_history_newest_first(listener):
    assert request(listener, "GET", "/api/events")[0] == 401

    headers = {AUTH_HEADER: auth.mint("browser")}
    for index in range(3):
        events.record("modified", path=f"/{index}.mkv")

    status, page = request(listener, "GET", "/api/events", headers=headers)
    assert status == 200
    assert [entry["path"] for entry in page["events"]] == ["/2.mkv", "/1.mkv", "/0.mkv"]
    assert page["next"] is None

    status, page = request(listener, "GET", "/api/events?limit=2", headers=headers)
    assert [entry["path"] for entry in page["events"]] == ["/2.mkv", "/1.mkv"]

    path = f"/api/events?limit=2&before={page['next']}"
    status, page = request(listener, "GET", path, headers=headers)
    assert [entry["path"] for entry in page["events"]] == ["/0.mkv"]
    # Null rather than absent, so the feed knows it has reached the beginning.
    assert page["next"] is None


def test_the_events_api_bounds_a_page_in_time(listener):
    """What the feed's time control asks in. A preset works out its own
    `since` each time it fetches, so "the last hour" stays the last hour."""
    headers = {AUTH_HEADER: auth.mint("browser")}
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(events.path(), "a") as history:
        for day in range(1, 6):
            line = {
                "ts": f"2026-03-0{day}T12:00:00+00:00",
                "event": "modified",
                "path": f"/{day}.mkv",
            }
            history.write(json.dumps(line) + "\n")

    since = "2026-03-03T00:00:00%2B00:00"
    status, page = request(listener, "GET", f"/api/events?since={since}", headers=headers)
    assert status == 200
    assert [entry["path"] for entry in page["events"]] == ["/5.mkv", "/4.mkv", "/3.mkv"]
    assert page["next"] is None

    until = "2026-03-04T00:00:00%2B00:00"
    path = f"/api/events?since={since}&until={until}"
    status, page = request(listener, "GET", path, headers=headers)
    assert [entry["path"] for entry in page["events"]] == ["/3.mkv"]

    # A window pages on the byte offset like any other read, so the second
    # page of a custom range costs what the second page of the history costs.
    status, page = request(
        listener, "GET", f"/api/events?limit=1&since={since}", headers=headers
    )
    assert [entry["path"] for entry in page["events"]] == ["/5.mkv"]
    path = f"/api/events?limit=1&since={since}&before={page['next']}"
    status, page = request(listener, "GET", path, headers=headers)
    assert [entry["path"] for entry in page["events"]] == ["/4.mkv"]


def test_the_events_api_refuses_a_moment_it_cannot_read(listener):
    headers = {AUTH_HEADER: auth.mint("browser")}
    assert request(listener, "GET", "/api/events?since=yesterday", headers=headers)[0] == 400
    assert request(listener, "GET", "/api/events?until=17", headers=headers)[0] == 400


def test_a_window_with_its_ends_crossed_is_empty_rather_than_a_mistake(listener):
    """A from/to pair is two fields somebody types in either order, and half a
    second with the ends crossed should not put an error where the list goes."""
    headers = {AUTH_HEADER: auth.mint("browser")}
    events.record("modified", path="/a.mkv")

    path = "/api/events?since=2026-03-05T00:00:00%2B00:00&until=2026-03-01T00:00:00%2B00:00"
    status, page = request(listener, "GET", path, headers=headers)

    assert status == 200
    assert (page["events"], page["titles"], page["next"]) == ([], {}, None)


def test_the_events_api_names_the_title_each_line_is_about(listener, one_title):
    """What puts a poster on the feed. Only the service knows which title a
    file belongs to."""
    headers = {AUTH_HEADER: auth.mint("browser")}
    inside = f"{one_title}/Dune.mkv"
    events.record("modified", path=inside)
    events.record("webhook", arr="radarr", files=1, paths=[inside])
    # About the library rather than about a title, and about a file under no
    # title at all: neither has a poster to stand under.
    events.record("sweep", files=1)
    events.record("modified", path="/elsewhere/film.mkv")

    status, page = request(listener, "GET", "/api/events", headers=headers)
    assert status == 200
    assert [(entry["event"], entry.get("title")) for entry in page["events"]] == [
        ("modified", None),
        ("sweep", None),
        ("webhook", "arr:radarr:7"),
        ("modified", "arr:radarr:7"),
    ]
    # One card for the two lines naming it, and it is the card the grid draws.
    assert list(page["titles"]) == ["arr:radarr:7"]
    card = page["titles"]["arr:radarr:7"]
    assert (card["name"], card["state"]) == ("Dune", "pending")


def test_a_history_with_no_titles_in_it_carries_no_cards(listener):
    """Sweeps, saves and pauses are about the library or the service. The
    feed drew them without posters before and still does."""
    headers = {AUTH_HEADER: auth.mint("browser")}
    events.record("paused", by="admin")
    _, page = request(listener, "GET", "/api/events", headers=headers)
    assert page["titles"] == {}
    assert "title" not in page["events"][0]


def test_a_delivery_spanning_two_titles_gets_no_poster(listener, monkeypatch):
    """One import is one title in every *arr there is, but the history
    outlives their habits. A row naming two is about the delivery rather than
    about either of them, and a poster of one half of it is worse than none."""
    monkeypatch.setattr(
        library,
        "cards_for_paths",
        lambda paths: (
            {"/a/one.mkv": "dir:/a", "/b/two.mkv": "dir:/b"},
            {"dir:/a": {"id": "dir:/a"}, "dir:/b": {"id": "dir:/b"}},
        ),
    )
    headers = {AUTH_HEADER: auth.mint("browser")}
    events.record("webhook", arr="sonarr", files=2, paths=["/a/one.mkv", "/b/two.mkv"])
    _, page = request(listener, "GET", "/api/events", headers=headers)
    assert "title" not in page["events"][0]
    # And no card either: the page carries what its rows actually name.
    assert page["titles"] == {}


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=501", "limit=lots", "before=-1", "before=soon"]
)
def test_the_events_api_refuses_a_page_it_cannot_serve(listener, query):
    """A limit of a million would answer one request by reading a year of
    history into memory, and a cursor that is not an offset reads nothing."""
    headers = {AUTH_HEADER: auth.mint("browser")}
    assert events.MAX_READ == 500
    assert request(listener, "GET", f"/api/events?{query}", headers=headers)[0] == 400


def open_stream(server, headers: dict) -> tuple[http.client.HTTPConnection, object]:
    """GET /api/stream and hand back the live response, still streaming."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("GET", "/api/stream", headers=headers)
    return conn, conn.getresponse()


def test_the_stream_needs_a_secret(listener):
    assert request(listener, "GET", "/api/stream")[0] == 401


def test_the_stream_says_what_changed(listener, monkeypatch):
    monkeypatch.setattr("trackstarr.api._STREAM_HEARTBEAT", 0.05)
    conn, response = open_stream(listener, {AUTH_HEADER: auth.mint("browser")})
    try:
        assert response.status == 200
        headers = dict(response.getheaders())
        assert headers["Content-Type"] == "text/event-stream"
        # No Content-Length, so the socket closing is what ends the body: the
        # connection must not be offered for reuse, and a buffering proxy must
        # be asked to pass each line straight on.
        assert headers["Connection"] == "close"
        assert "Content-Length" not in headers
        assert headers["X-Accel-Buffering"] == "no"
        notify.publish(notify.LIBRARY)
        # The heartbeat is short here, so a ping can land before the message.
        for _ in range(20):
            line = response.readline()
            if line.strip():
                break
        assert json.loads(line.removeprefix(b"data: ")) == {"kind": "library"}
    finally:
        conn.close()


def test_a_full_house_turns_the_stream_away(listener, monkeypatch):
    """The page is answered rather than hung, and falls back to polling."""
    monkeypatch.setattr(notify, "MAX_STREAMS", 0)
    headers = {AUTH_HEADER: auth.mint("browser")}
    assert request(listener, "GET", "/api/stream", headers=headers)[0] == 503


def test_an_idle_stream_heartbeats(listener, monkeypatch):
    """The ping is what a dead peer fails to take delivery of, and what tells
    the page a proxy is sitting on the stream rather than the service on its
    news."""
    monkeypatch.setattr("trackstarr.api._STREAM_HEARTBEAT", 0.05)
    conn, response = open_stream(listener, {AUTH_HEADER: auth.mint("browser")})
    try:
        assert response.readline() == b'data: {"kind": "ping"}\n'
        assert response.readline() == b"\n"
    finally:
        conn.close()


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


def test_login_sets_a_hardened_cookie_that_signs_requests(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    status, me, headers = api(
        listener, "POST", "/api/auth/login", {"username": "admin", "password": "right password"}
    )
    assert status == 200
    assert me == {"name": "admin", "role": "admin", "must_change": False}
    set_cookie = headers["Set-Cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Path=/" in set_cookie
    assert f"Max-Age={sessions.TTL}" in set_cookie
    # LAN deploys are plain http; the flag would make the browser drop the cookie.
    assert "Secure" not in set_cookie

    cookie = set_cookie.split(";")[0]
    # Found among other cookies; junk without an = authorises nothing.
    assert api(listener, "GET", "/api/status", cookie=f"other=1; {cookie}")[0] == 200
    assert api(listener, "GET", "/api/status", cookie="junk")[0] == 401


def test_the_cookie_is_secure_only_behind_an_https_proxy(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    _, _, headers = api(
        listener,
        "POST",
        "/api/auth/login",
        {"username": "admin", "password": "right password"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "; Secure" in headers["Set-Cookie"]


def test_login_says_nothing_about_which_accounts_exist(listener, fast_scrypt):
    """Wrong password, unknown name and an empty body all read identically."""
    users.add("admin", "right password", "admin")
    wrong = api(
        listener, "POST", "/api/auth/login", {"username": "admin", "password": "guessed"}
    )
    unknown = api(
        listener, "POST", "/api/auth/login", {"username": "ghost", "password": "guessed"}
    )
    empty = api(listener, "POST", "/api/auth/login", {})
    assert wrong[0] == unknown[0] == empty[0] == 401
    assert wrong[1] == unknown[1] == empty[1]


def test_repeated_failures_park_the_name_even_for_the_right_password(listener, fast_scrypt):
    """Answering the right password differently mid-lock would confirm the
    guess the lock exists to slow down."""
    users.add("admin", "right password", "admin")
    bad = {"username": "admin", "password": "guessed"}
    for _ in range(users._LOCK_AFTER):
        assert api(listener, "POST", "/api/auth/login", bad)[0] == 401
    good = {"username": "admin", "password": "right password"}
    assert api(listener, "POST", "/api/auth/login", good)[0] == 429


def test_me_names_the_session_and_nobody_else(listener, fast_scrypt):
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    status, me, _ = api(listener, "GET", "/api/auth/me", cookie=cookie)
    assert (status, me) == (200, {"name": "watcher", "role": "viewer", "must_change": False})
    assert api(listener, "GET", "/api/auth/me")[0] == 401
    # A machine secret is a caller, not a person; it has no "me".
    machine = {AUTH_HEADER: auth.mint("scanner")}
    assert api(listener, "GET", "/api/auth/me", headers=machine)[0] == 401


def test_a_forced_change_unlocks_only_after_the_change(listener, fast_scrypt):
    """The bootstrap password sits in the docker log, so until it is
    replaced it unlocks nothing but the change itself."""
    users.add("admin", "bootstrap pass", "admin", must_change=True)
    status, me, headers = api(
        listener, "POST", "/api/auth/login", {"username": "admin", "password": "bootstrap pass"}
    )
    assert (status, me["must_change"]) == (200, True)
    cookie = headers["Set-Cookie"].split(";")[0]

    assert api(listener, "GET", "/api/status", cookie=cookie)[0] == 403
    assert api(listener, "GET", "/api/auth/me", cookie=cookie)[0] == 200
    assert api(listener, "POST", "/api/config", {}, cookie=cookie)[0] == 403

    status, me, headers = api(
        listener,
        "POST",
        "/api/auth/password",
        {"current": "bootstrap pass", "new": "a proper password"},
        cookie=cookie,
    )
    assert (status, me["must_change"]) == (200, False)
    fresh = headers["Set-Cookie"].split(";")[0]
    # Every session from before the change is dead, including this browser's
    # old one; the answer carried its replacement.
    assert api(listener, "GET", "/api/status", cookie=cookie)[0] == 401
    assert api(listener, "GET", "/api/status", cookie=fresh)[0] == 200


def test_change_password_proves_the_current_one_first(listener, fast_scrypt):
    """A walked-away-from browser must not be enough to take the account."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    wrong = {"current": "guessed", "new": "a proper password"}
    assert api(listener, "POST", "/api/auth/password", wrong, cookie=cookie)[0] == 403
    short = {"current": "right password", "new": "short"}
    assert api(listener, "POST", "/api/auth/password", short, cookie=cookie)[0] == 400
    signed_out = {"current": "right password", "new": "a proper password"}
    assert api(listener, "POST", "/api/auth/password", signed_out)[0] == 401


def test_logout_revokes_the_session_and_clears_the_cookie(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    status, _, headers = api(listener, "POST", "/api/auth/logout", {}, cookie=cookie)
    assert status == 200
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert api(listener, "GET", "/api/status", cookie=cookie)[0] == 401


def test_only_admin_sessions_may_write(listener, fast_scrypt):
    """A write is gated before it is looked up, so a viewer asking for a path
    that isn't there is told 403 where an admin is told 404."""
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    admin_cookie = sign_in(listener, "admin")
    viewer_cookie = sign_in(listener, "watcher")
    assert api(listener, "POST", "/api/config", {}, cookie=viewer_cookie)[0] == 403
    assert api(listener, "POST", "/api/config", {}, cookie=admin_cookie)[0] == 404
    assert api(listener, "GET", "/api/status", cookie=viewer_cookie)[0] == 200


def test_every_write_but_the_two_auth_ones_wants_an_admin():
    """Which endpoints a viewer may write is a field on each row rather than
    where it sits, so an endpoint arriving without one is a missing field."""
    for_any_session = {
        path for path, route in _POST_ROUTES.items() if route.access is not Access.ADMIN
    }
    assert for_any_session == {"/api/auth/logout", "/api/auth/password"}


def test_settings_read_for_any_session_written_by_admins(listener, fast_scrypt, settings_state):
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    admin_cookie = sign_in(listener, "admin")
    viewer_cookie = sign_in(listener, "watcher")

    status, shot, _ = api(listener, "GET", "/api/settings", cookie=viewer_cookie)
    assert status == 200
    assert shot["settings"]["AUDIO_LAYOUTS"]["value"] == ["2.0", "5.1"]

    change = {"RULE_COMMENTARY": "always"}
    assert api(listener, "POST", "/api/settings", change, cookie=viewer_cookie)[0] == 403
    status, shot, _ = api(listener, "POST", "/api/settings", change, cookie=admin_cookie)
    assert status == 200
    assert shot["settings"]["RULE_COMMENTARY"]["value"] == "always"
    assert config.RULE_MODES["commentary"] == "always"
    # The history credits the account that saved it, which is the endpoint's
    # to know: settings.update() is handed a body and nothing else.
    saved = next(entry for entry in read_events() if entry["event"] == "settings")
    assert saved["by"] == "admin"


def test_a_settings_write_with_an_unreadable_body_is_a_400(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    headers = {"Content-Type": "application/json", "Cookie": cookie}
    status, _ = request(listener, "POST", "/api/settings", b"{not json", headers)
    assert status == 400


def test_a_settings_write_the_disk_refuses_is_a_500(listener, fast_scrypt, monkeypatch):
    """An unwritable /config must answer rather than drop the connection, or
    the page sits on a spinner over a permissions mistake."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")

    def refuse(changes, by=None):
        raise OSError("read-only file system")

    monkeypatch.setattr(settings, "update", refuse)
    status, answer, _ = api(
        listener, "POST", "/api/settings", {"RULE_COMMENTARY": "always"}, cookie=cookie
    )
    assert (status, answer["status"]) == (500, "could not write the settings")


def test_a_connection_test_answers_the_check(listener, fast_scrypt, monkeypatch):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    asked = []

    def fake_check(name, url, key):
        asked.append((name, url, key))
        return connections.Result(True, "Films 5.14.0", webhook="connected")

    monkeypatch.setattr(connections, "check", fake_check)
    status, answer, _ = api(
        listener,
        "POST",
        "/api/connections/test",
        {"service": "radarr", "url": "http://radarr:7878", "key": "typed"},
        cookie=cookie,
    )
    assert status == 200
    assert answer == {"ok": True, "detail": "Films 5.14.0", "hint": "", "webhook": "connected"}
    # The page's own values, passed through rather than read back off disk.
    assert asked == [("radarr", "http://radarr:7878", "typed")]


def test_a_connection_test_is_an_admins(listener, fast_scrypt):
    """The body names an address this container will fetch, so the role gate
    matters more here than on a settings write."""
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    body = {"service": "radarr"}
    assert api(listener, "POST", "/api/connections/test", body, cookie="")[0] == 401
    viewer_cookie = sign_in(listener, "watcher")
    assert api(listener, "POST", "/api/connections/test", body, cookie=viewer_cookie)[0] == 403


def test_a_connection_test_with_an_unreadable_body_is_a_400(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    headers = {"Content-Type": "application/json", "Cookie": cookie}
    status, _ = request(listener, "POST", "/api/connections/test", b"{not json", headers)
    assert status == 400


def test_an_unknown_service_cannot_be_tested(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    status, answer, _ = api(
        listener, "POST", "/api/connections/test", {"service": "emby"}, cookie=cookie
    )
    assert (status, answer["status"]) == (404, "no such service")


def test_saving_an_arr_address_re_registers_the_webhook(
    listener, fast_scrypt, settings_state, monkeypatch
):
    """Nothing else revisits the *arrs' connections until a restart, so a new
    address would otherwise be saved and never called."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    fired = threading.Event()
    monkeypatch.setattr("trackstarr.api.reregister_webhooks", fired.set)

    unrelated = {"REWRITE_MODE": "report"}
    assert api(listener, "POST", "/api/settings", unrelated, cookie=cookie)[0] == 200
    assert not fired.is_set()

    change = {"RADARR_URL": "http://radarr:7878"}
    assert api(listener, "POST", "/api/settings", change, cookie=cookie)[0] == 200
    assert fired.wait(timeout=5)


def test_an_invalid_settings_change_answers_the_problems(listener, fast_scrypt, settings_state):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    status, answer, _ = api(
        listener, "POST", "/api/settings", {"RULE_NOPE": "always"}, cookie=cookie
    )
    assert status == 400
    assert any("names no rule" in problem for problem in answer["problems"])
    assert "nope" not in config.RULE_MODES


def test_api_posts_demand_a_json_content_type(listener):
    """A cross-site form can spell neither application/json nor a custom
    header, so SameSite plus this check is the whole CSRF story."""
    body = {"username": "admin", "password": "whatever"}
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert api(listener, "POST", "/api/auth/login", body, headers=form)[0] == 415


def test_a_body_that_is_not_a_json_object_is_a_400(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    assert api(listener, "POST", "/api/auth/login", [1, 2])[0] == 400
    assert api(listener, "POST", "/api/auth/password", [1, 2], cookie=cookie)[0] == 400
    webhook_headers = {AUTH_HEADER: auth.mint("radarr")}
    assert request(listener, "POST", webhook.WEBHOOK_PATH, b"[1, 2]", webhook_headers)[0] == 400


def test_storage_failures_answer_500_not_a_dead_connection(listener, fast_scrypt, monkeypatch):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    # The store refusal, not the session shuffle around it, so the cookie
    # survives each 500 for the next case.
    monkeypatch.setattr(users, "set_password", refuse)
    change = {"current": "right password", "new": "a proper password"}
    assert api(listener, "POST", "/api/auth/password", change, cookie=cookie)[0] == 500
    monkeypatch.setattr(sessions, "create", refuse)
    good = {"username": "admin", "password": "right password"}
    assert api(listener, "POST", "/api/auth/login", good)[0] == 500
    monkeypatch.setattr(sessions, "revoke", refuse)
    assert api(listener, "POST", "/api/auth/logout", {}, cookie=cookie)[0] == 500


def test_the_sweep_check_answers_the_page(listener, fast_scrypt, tmp_path):
    """The schedule read back in words, and whether each dir is mounted, while
    there is still something on the page to correct."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    (tmp_path / "movies").mkdir()

    status, answer, _ = api(
        listener,
        "POST",
        "/api/sweep/check",
        {
            "at": "0 4 * * *",
            "dirs": [str(tmp_path / "movies"), str(tmp_path / "gone")],
            "tz": "America/New_York",
        },
        cookie=cookie,
    )
    assert status == 200
    assert answer["ok"] and answer["error"] == ""
    assert all(run.endswith("T04:00:00") for run in answer["runs"])
    assert [entry["state"] for entry in answer["dirs"]] == ["ok", "missing"]
    # The zone the page is holding, not the one the service is running in:
    # both are still on the page, unsaved.
    assert answer["zone"] in ("EST", "EDT")


def test_the_sweep_check_is_an_admins(listener, fast_scrypt):
    """The body names paths this container stats, so it sits behind the same
    gate as the connection test rather than beside the reads."""
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    body = {"at": "0 4 * * *", "dirs": []}
    assert api(listener, "POST", "/api/sweep/check", body, cookie="")[0] == 401
    viewer_cookie = sign_in(listener, "watcher")
    assert api(listener, "POST", "/api/sweep/check", body, cookie=viewer_cookie)[0] == 403


def test_the_sweep_check_survives_a_body_that_names_no_dirs(listener, fast_scrypt):
    """A page mid-edit sends whatever it holds; a missing or wrong-shaped
    dirs key is an empty list, not a 500."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    for body in ({"at": "0 4 * * *"}, {"at": "0 4 * * *", "dirs": "/data"}):
        status, answer, _ = api(listener, "POST", "/api/sweep/check", body, cookie=cookie)
        assert (status, answer["dirs"]) == (200, [])


def test_the_runs_api_reports_what_is_happening_now(listener, fast_scrypt, clean_registry):
    """A read, so a viewer can watch a sweep; only the buttons are an admin's."""
    users.add("watcher", "right password", "viewer")
    assert api(listener, "GET", "/api/runs")[0] == 401

    clean_registry.open_run("r#1", runs.SWEEP, dry_run=True)
    clean_registry.set_total("r#1", 40)
    clean_registry.tally("r#1", "conform")
    clean_registry.begin("r#1", "/data/film.mkv")

    status, answer, _ = api(listener, "GET", "/api/runs", cookie=sign_in(listener, "watcher"))
    assert status == 200
    (run,) = answer["runs"]
    assert (run["id"], run["done"], run["total"]) == ("r#1", 1, 40)
    assert run["active"][0]["path"] == "/data/film.mkv"
    # The queue counts everything owed, so this sweep's 38 unwalked files are
    # in it; counting the import queue alone showed a sweep as nothing waiting.
    assert (answer["queue"], answer["working"]) == (38, 1)
    assert (answer["parked"], answer["paused"]) == (0, False)
    # And when the next sweep is, which is the one thing an idle service can
    # say about the future; None here, since the test listener has no schedule.
    assert answer["next_sweep"] is None


def test_a_files_verdict_and_its_log_are_both_readable_from_the_row(
    listener, fast_scrypt, clean_registry, caplog
):
    """What became of a file, and what its worker said while doing it. The
    verdict travels with the snapshot; the log is its own request, since a
    sweep's worth of lines in a two-second poll would be most of the wire."""
    caplog.set_level(logging.INFO)
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    runlog.capture()

    clean_registry.open_run("r#1", runs.SWEEP)
    clean_registry.begin("r#1", "/data/film.mkv")
    logging.getLogger("trackstarr.test").info("ffmpeg -i film.mkv")
    clean_registry.finish("r#1", "/data/film.mkv")
    clean_registry.tally("r#1", "modified", path="/data/film.mkv", detail="add 2.0 downmix")

    _, answer, _ = api(listener, "GET", "/api/runs", cookie=cookie)
    (row,) = answer["runs"][0]["recent"]
    assert (row["path"], row["status"], row["detail"]) == (
        "/data/film.mkv",
        "modified",
        "add 2.0 downmix",
    )

    status, answer, _ = api(
        listener, "GET", "/api/runs/log?run=r%231&path=/data/film.mkv", cookie=cookie
    )
    assert status == 200
    assert [line.split("INFO")[-1].strip() for line in answer["lines"]] == [
        "ffmpeg -i film.mkv"
    ]

    # A file nothing kept lines for is an empty log, not an error: one far
    # enough back has had its lines dropped for newer files'.
    _, answer, _ = api(
        listener, "GET", "/api/runs/log?run=r%231&path=/data/other.mkv", cookie=cookie
    )
    assert answer["lines"] == []
    assert api(listener, "GET", "/api/runs/log?run=r%231", cookie=cookie)[0] == 400
    assert api(listener, "GET", "/api/runs/log?run=r%231&path=/x.mkv")[0] == 401


def test_pausing_from_the_page_stops_the_service_and_says_who(
    listener, fast_scrypt, clean_registry
):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")

    status, answer, _ = api(listener, "POST", "/api/runs/pause", {}, cookie=cookie)
    assert status == 200
    # Answered with the snapshot the page would have polled for next, so the
    # button's state comes from the service rather than an optimistic guess.
    assert answer["paused"] is True
    assert answer["paused_by"] == "admin"
    assert clean_registry.paused()

    _, answer, _ = api(listener, "POST", "/api/runs/resume", {}, cookie=cookie)
    assert answer["paused"] is False
    assert not clean_registry.paused()


def test_only_admins_may_work_the_controls(listener, fast_scrypt, clean_registry):
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    viewer_cookie = sign_in(listener, "watcher")
    for path in ("start", "stop", "pause", "resume", "abort"):
        assert api(listener, "POST", f"/api/runs/{path}", {}, cookie="")[0] == 401
        assert api(listener, "POST", f"/api/runs/{path}", {}, cookie=viewer_cookie)[0] == 403
    assert not clean_registry.paused()


def test_a_sweep_started_from_the_page_answers_with_its_run(
    listener, fast_scrypt, clean_registry, monkeypatch
):
    """The run id comes back rather than being looked up afterwards, so the
    page follows the sweep it asked for even when a delivery lands beside it."""
    users.add("admin", "right password", "admin")
    swept = threading.Event()
    seen: list[tuple] = []

    def fake_sweep(dry_run, run=None):
        seen.append((dry_run, run))
        swept.set()
        return {}

    monkeypatch.setattr(sweep, "sweep", fake_sweep)
    status, answer, _ = api(
        listener,
        "POST",
        "/api/runs/start",
        {"mode": "apply"},
        cookie=sign_in(listener, "admin"),
    )
    assert status == 200
    assert answer["run"]
    assert swept.wait(5)
    assert seen == [(False, answer["run"])]


def test_a_page_sweep_defaults_to_reporting_and_refuses_anything_else(
    listener, fast_scrypt, clean_registry, monkeypatch
):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    swept = threading.Event()
    seen: list[bool] = []
    monkeypatch.setattr(
        sweep,
        "sweep",
        lambda dry_run, run=None: (seen.append(dry_run), swept.set(), {})[2],
    )

    assert api(listener, "POST", "/api/runs/start", {}, cookie=cookie)[0] == 200
    assert swept.wait(5)
    assert seen == [True], "report-only unless the page asks to apply"
    assert api(listener, "POST", "/api/runs/start", {"mode": "wipe"}, cookie=cookie)[0] == 400


def test_a_second_sweep_is_refused_while_one_is_going(listener, fast_scrypt, clean_registry):
    """Two walks would fight over the sweep cache and over pending.tsv, which
    is one file both would be writing."""
    users.add("admin", "right password", "admin")
    clean_registry.open_run("r#1", runs.SWEEP)
    status, answer, _ = api(
        listener, "POST", "/api/runs/start", {}, cookie=sign_in(listener, "admin")
    )
    assert status == 409
    assert answer["run"] == "r#1", "named, so the page can offer to stop it"


def test_clearing_the_verdicts_is_refused_while_a_sweep_is_going(
    listener, fast_scrypt, clean_registry
):
    """Not a courtesy. A running sweep holds the previous entries in memory
    and writes the whole file out at every checkpoint, so deleting it
    underneath is either undone seconds later or loses the walk's own work."""
    users.add("admin", "right password", "admin")
    clean_registry.open_run("r#1", runs.SWEEP)
    status, answer, _ = api(
        listener, "POST", "/api/library/clear", {}, cookie=sign_in(listener, "admin")
    )
    assert status == 409
    assert answer["run"] == "r#1", "named, so the page can offer to stop it"


def test_clearing_the_verdicts_is_an_admins(listener, fast_scrypt, clean_registry):
    """It throws away work. A viewer watching the library cannot empty it."""
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    assert (
        api(listener, "POST", "/api/library/clear", {}, cookie=sign_in(listener, "watcher"))[0]
        == 403
    )
    status, answer, _ = api(
        listener, "POST", "/api/library/clear", {}, cookie=sign_in(listener, "admin")
    )
    assert status == 200
    assert answer["dropped"] == 0


def test_the_scores_can_be_read_by_anyone_signed_in(listener, fast_scrypt):
    """One small file, so the settings page can say how many titles carry a
    score without loading the shelf to count them."""
    users.add("watcher", "right password", "viewer")
    status, answer, _ = api(
        listener, "GET", "/api/library/ratings", cookie=sign_in(listener, "watcher")
    )

    assert status == 200
    assert answer == {"scored": 0, "fetched": 0.0}


def test_fetching_the_scores_now_stores_them_and_says_what_it_found(
    listener, fast_scrypt, monkeypatch
):
    """The settings page's button: everything the daily loop does, minus the
    two intervals it obeys, since this one was asked for."""
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "IMDB_RATINGS", True)
    monkeypatch.setattr(library, "imdb_ids", lambda: {"tt15239678", "tt0000001"})
    monkeypatch.setattr(ratings, "refresh", lambda wanted: _store_scores({"tt15239678": 6.4}))

    status, answer, _ = api(
        listener, "POST", "/api/library/ratings", {}, cookie=sign_in(listener, "admin")
    )

    assert status == 200
    assert answer["scored"] == 1
    assert answer["titles"] == 2, "both numbers, since one alone says nothing"
    assert answer["fetched"] > 0


def test_fetching_the_scores_is_an_admins(listener, fast_scrypt):
    users.add("watcher", "right password", "viewer")

    watching = sign_in(listener, "watcher")

    assert api(listener, "POST", "/api/library/ratings", {}, cookie=watching)[0] == 403


def test_a_score_fetch_with_an_unreadable_body_is_a_400(listener, fast_scrypt):
    """The button sends an empty object, but the body still has to leave the
    socket: the connection is reused and the next request reads from where
    this one stopped."""
    users.add("admin", "right password", "admin")
    headers = {"Content-Type": "application/json", "Cookie": sign_in(listener, "admin")}

    status, _ = request(listener, "POST", "/api/library/ratings", b"{not json", headers)

    assert status == 400


def test_the_scores_are_not_fetched_with_the_setting_off(listener, fast_scrypt, monkeypatch):
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "IMDB_RATINGS", False)

    status, answer, _ = api(
        listener, "POST", "/api/library/ratings", {}, cookie=sign_in(listener, "admin")
    )

    assert status == 409
    assert "switched off" in answer["status"]


def test_the_scores_are_not_rebuilt_from_half_a_library(listener, fast_scrypt, monkeypatch):
    """An *arr that could not be listed is every one of its titles losing its
    score until tomorrow, which is worse than the button doing nothing."""
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "IMDB_RATINGS", True)
    monkeypatch.setattr(library, "imdb_ids", lambda: None)

    status, answer, _ = api(
        listener, "POST", "/api/library/ratings", {}, cookie=sign_in(listener, "admin")
    )

    assert status == 409
    assert "could not be listed" in answer["status"]


def test_a_library_with_no_imdb_ids_is_not_worth_eight_megabytes(
    listener, fast_scrypt, monkeypatch
):
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "IMDB_RATINGS", True)
    monkeypatch.setattr(library, "imdb_ids", set)

    status, answer, _ = api(
        listener, "POST", "/api/library/ratings", {}, cookie=sign_in(listener, "admin")
    )

    assert status == 409
    assert "IMDb id" in answer["status"]


def test_imdb_being_unreachable_leaves_the_stored_scores_alone(
    listener, fast_scrypt, monkeypatch
):
    """Yesterday's scores are the right answer for today; none at all is not."""
    users.add("admin", "right password", "admin")
    _store_scores({"tt15239678": 6.4})
    monkeypatch.setattr(config, "IMDB_RATINGS", True)
    monkeypatch.setattr(library, "imdb_ids", lambda: {"tt15239678"})

    def refuse(wanted):
        raise urllib.error.URLError("imdb is down")

    monkeypatch.setattr(ratings, "refresh", refuse)
    status, answer, _ = api(
        listener, "POST", "/api/library/ratings", {}, cookie=sign_in(listener, "admin")
    )

    assert status == 502
    assert "IMDb" in answer["status"]
    assert ratings.scores() == {"tt15239678": 6.4}


def _store_scores(scores: dict) -> int:
    """The table a fetch would have written. Returns what refresh() does."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    state.write_json(ratings.path(), {"scores": scores})
    ratings.forget()
    return len(scores)


def test_a_paused_service_will_not_be_talked_into_a_sweep(
    listener, fast_scrypt, clean_registry
):
    """It would only walk the library and park every thread on the gate."""
    users.add("admin", "right password", "admin")
    clean_registry.pause("marc")
    status, answer, _ = api(
        listener, "POST", "/api/runs/start", {}, cookie=sign_in(listener, "admin")
    )
    assert status == 409
    assert "paused" in answer["status"]


def test_stopping_names_the_run_and_404s_on_one_that_has_finished(
    listener, fast_scrypt, clean_registry
):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    clean_registry.open_run("r#1", runs.SWEEP)

    assert api(listener, "POST", "/api/runs/stop", {}, cookie=cookie)[0] == 400
    assert api(listener, "POST", "/api/runs/stop", {"run": "r#9"}, cookie=cookie)[0] == 404
    assert api(listener, "POST", "/api/runs/stop", {"run": "r#1"}, cookie=cookie)[0] == 200
    # It stops between files rather than mid-file, so it is still registered
    # and the page can say why it is emptying out.
    assert clean_registry.snapshot()["runs"][0]["stopping"] is True


def test_stopping_everything_ends_every_run_and_kills_the_rewrites(
    listener, fast_scrypt, clean_registry, monkeypatch
):
    """One button for "give me the machine back", so it does not ask what is
    running: a sweep, a delivery and a re-check are all what this machine is
    doing. Each still stops between files, so each writes its own summary."""
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(runs, "terminate_running", lambda path="": 2)
    clean_registry.open_run("s#1", runs.SWEEP)
    clean_registry.open_run("i#1", runs.IMPORT)

    status, answer, _ = api(
        listener, "POST", "/api/runs/abort", {}, cookie=sign_in(listener, "admin")
    )
    assert (status, answer["stopped"], answer["rewrites"]) == (200, 2, 2)
    # Not "runs": pause and resume answer with the snapshot, and the page tells
    # the two apart by whether the answer carries one.
    assert "runs" not in answer
    assert [run["stopping"] for run in clean_registry.snapshot()["runs"]] == [True, True]

    # Pressed again with everything already winding down: nothing left to ask,
    # and the rewrites are asked again because a SIGTERM can be ignored.
    _, again, _ = api(
        listener, "POST", "/api/runs/abort", {}, cookie=sign_in(listener, "admin")
    )
    assert (again["stopped"], again["rewrites"]) == (0, 2)


def test_skipping_an_active_file_kills_that_rewrite_and_no_other(
    listener, fast_scrypt, clean_registry, monkeypatch
):
    """Stop all is the blunt instrument. This one is for the film somebody has
    just sat down to watch while the sweep is halfway through it."""
    users.add("admin", "right password", "admin")
    signalled: list[str] = []
    monkeypatch.setattr(runs, "terminate_running", lambda path="": signalled.append(path))
    clean_registry.open_run("r#1", runs.SWEEP)
    clean_registry.begin("r#1", "/data/f.mkv")

    body = {"run": "r#1", "path": "/data/f.mkv"}
    status, answer, _ = api(
        listener, "POST", "/api/runs/skip", body, cookie=sign_in(listener, "admin")
    )
    assert (status, answer["where"]) == (200, "active")
    assert signalled == ["/data/f.mkv"], "by name, not every encode on the machine"
    assert clean_registry.skipped("r#1", "/data/f.mkv")


def test_skipping_a_file_the_run_will_not_reach_is_refused(
    listener, fast_scrypt, clean_registry
):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    clean_registry.open_run("r#1", runs.SWEEP)

    assert api(listener, "POST", "/api/runs/skip", {"run": "r#1"}, cookie=cookie)[0] == 400
    body = {"run": "r#9", "path": "/data/f.mkv"}
    assert api(listener, "POST", "/api/runs/skip", body, cookie=cookie)[0] == 404


def test_a_hold_is_placed_by_path_and_listed_back(listener, fast_scrypt, monkeypatch):
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/data/media/movies"])
    cookie = sign_in(listener, "admin")
    body = {
        "paths": ["/data/media/movies/Dune (2024)"],
        "seconds": 7200,
        "reason": "watching it",
    }

    status, answer, _ = api(listener, "POST", "/api/holds", body, cookie=cookie)
    assert status == 200
    (placed,) = answer["holds"]
    assert (placed["by"], placed["reason"], placed["seconds"]) == ("admin", "watching it", 7200)
    # The countdown, not a stamp: a browser in another zone reads it the same
    # way this one does.
    assert placed["until"] is not None

    _, listed, _ = api(listener, "GET", "/api/holds", cookie=cookie)
    assert listed["holds"] == answer["holds"]


def test_a_hold_outside_the_library_is_refused(listener, fast_scrypt, monkeypatch):
    """A hold is matched by prefix, so one on / would quietly stop everything
    being rewritten."""
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/data/media/movies"])
    cookie = sign_in(listener, "admin")

    assert api(listener, "POST", "/api/holds", {"paths": ["/"]}, cookie=cookie)[0] == 400
    assert api(listener, "POST", "/api/holds", {}, cookie=cookie)[0] == 400
    assert (
        api(listener, "POST", "/api/holds", {"ids": ["arr:radarr:9"]}, cookie=cookie)[0] == 404
    )
    body = {"paths": ["/data/media/movies/f.mkv"], "seconds": -1}
    assert api(listener, "POST", "/api/holds", body, cookie=cookie)[0] == 400


def test_lifting_a_hold_says_how_many_went(listener, fast_scrypt, monkeypatch):
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/data/media/movies"])
    cookie = sign_in(listener, "admin")
    body = {"paths": ["/data/media/movies/Dune (2024)"]}
    api(listener, "POST", "/api/holds", body, cookie=cookie)

    status, answer, _ = api(listener, "POST", "/api/holds/lift", body, cookie=cookie)
    assert (status, answer["lifted"], answer["holds"]) == (200, 1, [])
    # Pressed twice, or lifted from another tab first.
    _, again, _ = api(listener, "POST", "/api/holds/lift", body, cookie=cookie)
    assert again["lifted"] == 0


@pytest.mark.parametrize("path", ["/api/runs/skip", "/api/holds", "/api/holds/lift"])
def test_an_unreadable_body_is_a_400(listener, fast_scrypt, path):
    """The body still has to leave the socket: the connection is reused and the
    next request reads from where this one stopped."""
    users.add("admin", "right password", "admin")
    headers = {"Content-Type": "application/json", "Cookie": sign_in(listener, "admin")}

    status, _ = request(listener, "POST", path, b"{not json", headers)
    assert status == 400


def test_a_hold_that_cannot_be_stored_is_refused_rather_than_believed(
    listener, fast_scrypt, monkeypatch
):
    """A hold the page shows and nothing enforces is worse than a refusal: the
    file it was meant to protect would be rewritten anyway."""
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/data/media/movies"])
    cookie = sign_in(listener, "admin")

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(holds, "_save", refuse)
    body = {"paths": ["/data/media/movies/Dune (2024)"]}
    assert api(listener, "POST", "/api/holds", body, cookie=cookie)[0] == 500

    # And the same on the way back out, where a lift nothing wrote would read
    # as the title being free again.
    monkeypatch.setattr(holds, "lift", refuse)
    assert api(listener, "POST", "/api/holds/lift", body, cookie=cookie)[0] == 500


def test_a_lift_still_has_to_name_something(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    assert api(listener, "POST", "/api/holds/lift", {}, cookie=cookie)[0] == 400


def test_a_full_store_refuses_another_hold(listener, fast_scrypt, monkeypatch):
    """A hold is placed by hand, so the ceiling bounds a mistake: a script
    holding the whole library would stop every rewrite silently."""
    users.add("admin", "right password", "admin")
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/data/media/movies"])
    monkeypatch.setattr(holds, "full", lambda: True)
    body = {"paths": ["/data/media/movies/Dune (2024)"]}

    status, answer, _ = api(
        listener, "POST", "/api/holds", body, cookie=sign_in(listener, "admin")
    )
    assert (status, "already held" in answer["status"]) == (409, True)


def test_holding_and_skipping_are_an_admins(listener, fast_scrypt, clean_registry):
    """A viewer watches; both of these change what the machine does."""
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")

    assert api(listener, "POST", "/api/holds", {"paths": ["/x"]}, cookie=cookie)[0] == 403
    assert api(listener, "POST", "/api/holds/lift", {"paths": ["/x"]}, cookie=cookie)[0] == 403
    skip = {"run": "r#1", "path": "/x"}
    assert api(listener, "POST", "/api/runs/skip", skip, cookie=cookie)[0] == 403
    # Reading what is held is not.
    assert api(listener, "GET", "/api/holds", cookie=cookie)[0] == 200


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


@pytest.mark.parametrize("path", ["start", "stop", "pause", "resume", "abort"])
def test_every_control_refuses_a_body_it_cannot_read(
    listener, fast_scrypt, clean_registry, path
):
    """A page mid-reconnect can send anything; none of it may be a 500, and
    none of it may pause the service on the way past."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    assert api(listener, "POST", f"/api/runs/{path}", [1, 2], cookie=cookie)[0] == 400
    assert not clean_registry.paused()


@pytest.fixture
def one_title(monkeypatch, tmp_path):
    """One film, swept once: the *arr answering for the title and a cached
    verdict under it. Yields the title's folder."""
    root = tmp_path / "media" / "movies"
    folder = root / "Dune (2024)"
    folder.mkdir(parents=True)
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    arr = configured_arr()
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])
    monkeypatch.setattr(
        type(arr),
        "all_items",
        lambda self: [
            {
                "id": 7,
                "title": "Dune",
                "year": 2024,
                "path": str(folder),
                # What Radarr's own pages route on, which is its TMDB id.
                "titleSlug": "693134",
            }
        ],
    )
    library.forget()
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), Policy.from_config().fingerprint()
    )
    cache.record(
        str(folder / "Dune.mkv"),
        FileKey(10, 1, 1, "eng"),
        Verdict(Status.PENDING, "add 2.0 downmix", tracks=[{"index": 0, "kind": "video"}]),
    )
    cache.save()
    yield str(folder)
    library.forget()
    covers.forget()


def test_the_collection_is_a_read_any_session_may_make(listener, fast_scrypt, one_title):
    """A viewer browses the library; only the buttons elsewhere are an
    admin's."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    status, shelf, _ = api(listener, "GET", "/api/library", cookie=cookie)
    assert status == 200
    assert [(card["name"], card["state"]) for card in shelf["titles"]] == [("Dune", "pending")]


def test_the_summary_is_a_tally_and_the_newest_few_posters(listener, fast_scrypt, one_title):
    """What the overview reads instead of the whole shelf: the same cards the
    grid draws, newest first and cut to a strip's worth."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    status, found, _ = api(listener, "GET", "/api/library/summary", cookie=cookie)
    assert status == 200
    assert found["titles"] == 1
    assert found["counts"] == {"pending": 1}
    assert [(card["name"], card["state"]) for card in found["head"]] == [("Dune", "pending")]
    # A count, not the list itself: the landing page has no use for a few
    # hundred kilobytes of cards it will not draw.
    assert isinstance(found["titles"], int)


def test_the_summary_takes_the_order_the_strip_is_kept_in(listener, fast_scrypt, one_title):
    """Which dozen posters the strip gets is the service's cut, so the browser
    asks for the order rather than sorting what comes back."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    status, found, _ = api(
        listener, "GET", "/api/library/summary?sort=processed", cookie=cookie
    )
    assert status == 200
    assert [card["name"] for card in found["head"]] == ["Dune"]


def test_the_collection_is_closed_to_anyone_signed_out(listener, one_title):
    assert api(listener, "GET", "/api/library")[0] == 401
    assert api(listener, "GET", "/api/library/summary")[0] == 401
    assert api(listener, "GET", "/api/library/title?id=arr:radarr:7")[0] == 401


def test_a_title_answers_with_its_files(listener, fast_scrypt, one_title):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    status, detail, _ = api(
        listener, "GET", "/api/library/title?id=arr%3Aradarr%3A7", cookie=cookie
    )
    assert status == 200
    assert [file["name"] for file in detail["files"]] == ["Dune.mkv"]


def test_a_title_nobody_has_is_a_404(listener, fast_scrypt, one_title):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    assert api(listener, "GET", "/api/library/title?id=arr:radarr:99", cookie=cookie)[0] == 404
    # An id-less request is the same answer, not a 500 on a missing param.
    assert api(listener, "GET", "/api/library/title", cookie=cookie)[0] == 404


def test_a_title_names_the_servers_before_they_are_asked(
    listener, fast_scrypt, one_title, monkeypatch
):
    """Naming a media server is a read of the settings; finding the title
    inside it is a call, and the sheet stands its buttons up on the first.
    Radarr's link is neither: it is the slug the title arrived with."""
    monkeypatch.setattr(config, "PLEX_URL", "http://plex:32400")
    monkeypatch.setattr(config, "PLEX_TOKEN", "token")
    monkeypatch.setattr(config, "RADARR_URL", "http://radarr:7878")
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    _, detail, _ = api(listener, "GET", "/api/library/title?id=arr:radarr:7", cookie=cookie)

    assert detail["servers"] == [
        {"server": "plex", "label": "Plex"},
        {"server": "radarr", "label": "Radarr", "url": "http://radarr:7878/movie/693134"},
    ]


def test_the_links_endpoint_hands_over_the_whole_title(
    listener, fast_scrypt, one_title, monkeypatch
):
    asked: list[links.Subject] = []

    def found(subject):
        asked.append(subject)
        return [{"server": "plex", "label": "Plex", "url": "http://plex/web/index.html#!/x"}]

    monkeypatch.setattr(links, "for_title", found)
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    status, answer, _ = api(
        listener, "GET", "/api/library/links?id=arr:radarr:7", cookie=cookie
    )

    assert status == 200
    assert answer["links"][0]["server"] == "plex"
    # The folder as this container spells it; each server maps it on its way.
    assert asked[0].folder.endswith("Dune (2024)")
    assert (asked[0].name, asked[0].year) == ("Dune", 2024)
    # And which *arr claims it, with what that *arr's own pages route on.
    assert (asked[0].arr, asked[0].slug) == ("radarr", "693134")
    # An id nothing goes by is the same 404 the title itself gives.
    assert api(listener, "GET", "/api/library/links?id=arr:radarr:99", cookie=cookie)[0] == 404


def test_a_cover_is_proxied_with_the_arr_key_left_behind(
    listener, fast_scrypt, one_title, monkeypatch
):
    """The browser gets the poster and never the credential that fetched it,
    and caches it hard: a grid asks for hundreds at once."""
    sent: list[dict] = []

    def answer(url, headers=None, timeout=30):
        sent.append(headers or {})
        return b"\x89PNGdata", "image/png"

    monkeypatch.setattr(covers, "fetch",answer)
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    conn = http.client.HTTPConnection("127.0.0.1", listener.server_address[1])
    try:
        conn.request("GET", "/api/library/cover?id=arr:radarr:7", headers={"Cookie": cookie})
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
    finally:
        conn.close()
    assert (response.status, body) == (200, b"\x89PNGdata")
    assert headers["Content-Type"] == "image/png"
    # A week, and immutable: the url carries the title id, and that title's
    # poster is the same picture every time, so a reload should not even ask.
    assert "max-age=604800" in headers["Cache-Control"]
    assert "immutable" in headers["Cache-Control"]
    assert headers["ETag"]
    assert sent == [{"X-Api-Key": "key"}]


def test_a_cover_the_browser_already_holds_is_not_sent_again(
    listener, fast_scrypt, one_title, monkeypatch
):
    """The week runs out eventually and a cache in between revalidates before
    then; either way the answer is the tag, not the picture."""
    monkeypatch.setattr(
        covers, "fetch", lambda url, headers=None, timeout=30: (b"\x89PNGdata", "image/png")
    )
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    conn = keep_alive(listener)
    try:
        conn.request("GET", "/api/library/cover?id=arr:radarr:7", headers={"Cookie": cookie})
        first = conn.getresponse()
        tag = first.getheader("ETag")
        first.read()

        conn.request(
            "GET",
            "/api/library/cover?id=arr:radarr:7",
            headers={"Cookie": cookie, "If-None-Match": tag},
        )
        again = conn.getresponse()
        assert (again.status, again.read()) == (304, b"")
        assert again.getheader("ETag") == tag
    finally:
        conn.close()


def test_a_cover_nobody_has_is_a_404(listener, fast_scrypt, one_title, monkeypatch):
    """A tile the grid draws itself, not an error worth a message. Cached
    briefly all the same: a title with no artwork is on every reload."""

    def refuse(url, headers=None, timeout=30):
        raise OSError("gone")

    monkeypatch.setattr(covers, "fetch",refuse)
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    conn = http.client.HTTPConnection("127.0.0.1", listener.server_address[1])
    try:
        conn.request("GET", "/api/library/cover?id=arr:radarr:7", headers={"Cookie": cookie})
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
    finally:
        conn.close()
    assert (response.status, body) == (404, b"")
    assert "max-age=600" in headers["Cache-Control"]


# /api/library/run: re-checking chosen titles


def test_a_recheck_started_from_the_collection_answers_with_its_run(
    listener, fast_scrypt, clean_registry, one_title, monkeypatch
):
    """The run id comes back rather than being looked up afterwards, so the
    page follows the run it asked for and can show its progress in place."""
    users.add("admin", "right password", "admin")
    ran = threading.Event()
    seen: list[tuple] = []

    def fake_recheck(folders, dry_run, run=None, label=""):
        seen.append((folders, dry_run, run, label))
        ran.set()
        return {}

    monkeypatch.setattr(sweep, "recheck", fake_recheck)
    status, answer, _ = api(
        listener,
        "POST",
        "/api/library/run",
        {"ids": ["arr:radarr:7"], "mode": "apply"},
        cookie=sign_in(listener, "admin"),
    )
    assert status == 200
    assert (answer["run"], answer["titles"]) == (answer["run"], 1)
    assert ran.wait(5)
    folders, dry_run, run, label = seen[0]
    assert [os.path.basename(folder) for folder in folders] == ["Dune (2024)"]
    assert (dry_run, run, label) == (False, answer["run"], "Dune")


def test_a_recheck_defaults_to_planning_and_refuses_anything_else(
    listener, fast_scrypt, clean_registry, one_title, monkeypatch
):
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    ran = threading.Event()
    seen: list[bool] = []
    monkeypatch.setattr(
        sweep,
        "recheck",
        lambda folders, dry_run, run=None, label="": (seen.append(dry_run), ran.set(), {})[2],
    )

    assert (
        api(listener, "POST", "/api/library/run", {"ids": ["arr:radarr:7"]}, cookie=cookie)[0]
        == 200
    )
    assert ran.wait(5)
    assert seen == [True], "nothing is rewritten unless the page asks for it"
    assert (
        api(
            listener,
            "POST",
            "/api/library/run",
            {"ids": ["arr:radarr:7"], "mode": "wipe"},
            cookie=cookie,
        )[0]
        == 400
    )


def test_a_recheck_has_to_name_the_titles_it_wants(
    listener, fast_scrypt, clean_registry, one_title, monkeypatch
):
    """The ids are the whole request. How many of them there are is not: a
    selection is walked the way a sweep is, so none is turned down for size."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    ran = threading.Event()
    counted: list[int] = []

    def fake_recheck(folders, dry_run, run=None, label=""):
        counted.append(len(folders))
        ran.set()
        return {}

    monkeypatch.setattr(sweep, "recheck", fake_recheck)
    # A body that is not an object at all, which no page sends and anything
    # else might.
    assert api(listener, "POST", "/api/library/run", [1, 2], cookie=cookie)[0] == 400
    assert api(listener, "POST", "/api/library/run", {}, cookie=cookie)[0] == 400
    assert api(listener, "POST", "/api/library/run", {"ids": []}, cookie=cookie)[0] == 400
    assert api(listener, "POST", "/api/library/run", {"ids": "all"}, cookie=cookie)[0] == 400

    # A library's worth of ids, of which only the seventh is a title. Answered
    # rather than turned down for its size, and the rest dropped by selected().
    ids = [f"arr:radarr:{n}" for n in range(5_000)]
    assert api(listener, "POST", "/api/library/run", {"ids": ids}, cookie=cookie)[0] == 200
    assert ran.wait(5)
    assert counted == [1]


def test_a_recheck_of_titles_nothing_goes_by_is_a_404(
    listener, fast_scrypt, clean_registry, one_title
):
    """Resolved against the library's own index, so an id that names
    nothing is a miss rather than a folder walked on a request's say-so."""
    users.add("admin", "right password", "admin")
    status, _, _ = api(
        listener,
        "POST",
        "/api/library/run",
        {"ids": ["dir:/etc"]},
        cookie=sign_in(listener, "admin"),
    )
    assert status == 404


def test_a_recheck_is_refused_while_a_sweep_is_going(
    listener, fast_scrypt, clean_registry, one_title
):
    """Both write the sweep cache whole, so the second would publish a copy
    that never saw the first one's verdicts."""
    users.add("admin", "right password", "admin")
    clean_registry.open_run("r#1", runs.SWEEP)
    status, answer, _ = api(
        listener,
        "POST",
        "/api/library/run",
        {"ids": ["arr:radarr:7"]},
        cookie=sign_in(listener, "admin"),
    )
    assert status == 409
    assert answer["run"] == "r#1", "named, so the page can offer to stop it"


def test_a_sweep_is_refused_while_a_recheck_is_going(listener, fast_scrypt, clean_registry):
    """The same guard from the other side. A re-check walks a few folders
    rather than a library, but it writes the same file."""
    users.add("admin", "right password", "admin")
    clean_registry.open_run("r#1", runs.RECHECK)
    status, answer, _ = api(
        listener, "POST", "/api/runs/start", {}, cookie=sign_in(listener, "admin")
    )
    assert status == 409
    assert answer["run"] == "r#1"


def test_a_paused_service_will_not_be_talked_into_a_recheck(
    listener, fast_scrypt, clean_registry, one_title
):
    users.add("admin", "right password", "admin")
    clean_registry.pause("marc")
    status, answer, _ = api(
        listener,
        "POST",
        "/api/library/run",
        {"ids": ["arr:radarr:7"]},
        cookie=sign_in(listener, "admin"),
    )
    assert status == 409
    assert "paused" in answer["status"]


def test_running_titles_is_an_admins(listener, fast_scrypt, clean_registry, one_title):
    """It rewrites files. A viewer browsing the library cannot start one."""
    users.add("admin", "right password", "admin")
    users.add("watcher", "right password", "viewer")
    body = {"ids": ["arr:radarr:7"]}
    assert api(listener, "POST", "/api/library/run", body, cookie="")[0] == 401
    assert (
        api(listener, "POST", "/api/library/run", body, cookie=sign_in(listener, "watcher"))[0]
        == 403
    )


# The transport: one connection, compressed, and revalidated


def keep_alive(server):
    """A connection the caller drives request by request, so a test can see
    whether the listener leaves it usable."""
    return http.client.HTTPConnection("127.0.0.1", server.server_address[1])


def test_answers_share_one_connection(listener):
    """HTTP/1.1, so the grid's hundreds of posters are not hundreds of
    handshakes. Python's default is 1.0, which closes after every answer."""
    conn = keep_alive(listener)
    try:
        for _ in range(3):
            conn.request("GET", "/health")
            response = conn.getresponse()
            assert response.status == 200
            assert response.version == 11
            response.read()
            # The client only keeps the socket if the server let it: this is
            # False exactly when the answer said, or implied, close.
            assert not response.will_close
    finally:
        conn.close()


def test_a_post_body_nothing_wants_still_leaves_the_socket(listener, fast_scrypt):
    """Sign-out takes no arguments and the browser posts `{}` anyway. Left in
    the socket, that body would be read as the next request's opening line."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    conn = keep_alive(listener)
    try:
        conn.request(
            "POST",
            "/api/auth/logout",
            b'{"unused": true}',
            {"Content-Type": "application/json", "Cookie": cookie},
        )
        assert json.loads(conn.getresponse().read())["status"] == "signed out"
        # The desync shows up here or not at all: a body left behind makes
        # this answer either nonsense or nothing.
        conn.request("GET", "/health")
        second = conn.getresponse()
        assert second.status == 200
        assert json.loads(second.read())["status"] == "healthy"
    finally:
        conn.close()


def test_a_body_that_is_not_an_object_is_refused_wherever_it_lands(
    listener, fast_scrypt, clean_registry
):
    """Every endpoint reads its body through the same door, and one that says
    no has already answered. An endpoint that carried on would send a second
    reply down a connection the client is reading the first from."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")

    for path in ("/api/sweep/check", "/api/library/clear", "/api/auth/logout"):
        assert api(listener, "POST", path, ["not an object"], cookie=cookie)[0] == 400, path
    # And the session the last of them did not sign out of is still good.
    assert api(listener, "GET", "/api/auth/me", cookie=cookie)[0] == 200


def test_clearing_the_library_reads_the_body_it_is_sent(
    listener, fast_scrypt, clean_registry, one_title
):
    """The same, for the other button that posts an empty object and wants
    nothing out of it."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    conn = keep_alive(listener)
    try:
        conn.request(
            "POST",
            "/api/library/clear",
            b"{}",
            {"Content-Type": "application/json", "Cookie": cookie},
        )
        assert json.loads(conn.getresponse().read())["status"] == "cleared"
        conn.request("GET", "/health")
        assert conn.getresponse().status == 200
    finally:
        conn.close()


def test_a_post_refused_on_its_headers_closes_the_connection(listener):
    """Nothing read the body, so there is no way to know where it ended.
    Closing is what resynchronises the socket."""
    conn = keep_alive(listener)
    try:
        conn.request("POST", "/api/settings", b'{"a": 1}', {"Content-Type": "text/plain"})
        response = conn.getresponse()
        assert response.status == 415
        assert response.getheader("Connection") == "close"
        assert response.will_close
    finally:
        conn.close()


def test_an_unauthorized_webhook_closes_rather_than_desyncing(listener):
    conn = keep_alive(listener)
    try:
        conn.request("POST", webhook.WEBHOOK_PATH, b'{"eventType": "Test"}')
        response = conn.getresponse()
        assert response.status == 401
        assert response.will_close
    finally:
        conn.close()


@pytest.fixture
def big_shelf(monkeypatch):
    """A library large enough to be worth compressing, which one film is
    not: the threshold is a kilobyte and a card is a few dozen bytes."""
    cards = [
        {"id": f"arr:radarr:{n}", "name": f"Film {n}", "kind": "movie", "state": "conform"}
        for n in range(200)
    ]
    monkeypatch.setattr(
        library,
        "shelf",
        lambda: {"titles": cards, "complete": True, "current": True, "swept": 200},
    )
    monkeypatch.setattr(covers, "warm", lambda: None)


def test_the_shelf_is_compressed_for_a_browser_that_asks(listener, fast_scrypt, big_shelf):
    """The one that matters: a real shelf is a few hundred kilobytes of very
    repetitive JSON, and the phone reading it is on wifi."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    conn = keep_alive(listener)
    try:
        conn.request(
            "GET", "/api/library", headers={"Cookie": cookie, "Accept-Encoding": "gzip"}
        )
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Encoding") == "gzip"
        # Caches in between must not hand this to a client that cannot read it.
        assert response.getheader("Vary") == "Accept-Encoding"
        sent = response.read()
        shelf = json.loads(gzip.decompress(sent))
        assert len(shelf["titles"]) == 200
        assert len(sent) < len(json.dumps(shelf).encode()) / 2
    finally:
        conn.close()


def test_a_client_that_cannot_take_gzip_gets_the_plain_answer(listener, fast_scrypt, big_shelf):
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    status, shelf, headers = api(listener, "GET", "/api/library", cookie=cookie)
    assert status == 200
    assert "Content-Encoding" not in headers
    assert len(shelf["titles"]) == 200


def test_gzip_offered_at_q_zero_is_not_offered(listener, fast_scrypt, big_shelf):
    """`gzip;q=0` is a refusal, so the check cannot be a substring match."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    _, _, headers = api(
        listener,
        "GET",
        "/api/library",
        cookie=cookie,
        headers={"Accept-Encoding": "identity, gzip;q=0"},
    )
    assert "Content-Encoding" not in headers


def test_a_quality_nothing_could_read_is_taken_as_a_yes(listener, fast_scrypt, big_shelf):
    """The client did name gzip. Reading a quality we do not understand as a
    refusal would cost the saving on every answer to that client; taking it at
    its word costs a compression it may not have wanted."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    conn = keep_alive(listener)
    try:
        conn.request(
            "GET", "/api/library", headers={"Cookie": cookie, "Accept-Encoding": "gzip;q=high"}
        )
        response = conn.getresponse()
        assert response.getheader("Content-Encoding") == "gzip"
        assert len(json.loads(gzip.decompress(response.read()))["titles"]) == 200
    finally:
        conn.close()


def test_a_small_answer_is_not_worth_compressing(listener, fast_scrypt):
    """The overview polls this every couple of seconds; a gzip header and
    trailer would cost about what the compression saved."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    _, _, headers = api(
        listener, "GET", "/api/status", cookie=cookie, headers={"Accept-Encoding": "gzip"}
    )
    assert "Content-Encoding" not in headers


def test_an_unchanged_library_comes_back_as_a_header(listener, fast_scrypt, one_title):
    """The library page's loader re-runs on every visit. Nothing has swept in
    between, so the second visit should not carry the shelf again."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    status, _, headers = api(listener, "GET", "/api/library", cookie=cookie)
    assert status == 200
    tag = headers["ETag"]
    # Weak, because the gzipped answer and the plain one are the same shelf.
    assert tag.startswith('W/"')
    assert headers["Cache-Control"] == "private, no-cache"

    conn = keep_alive(listener)
    try:
        conn.request("GET", "/api/library", headers={"Cookie": cookie, "If-None-Match": tag})
        again = conn.getresponse()
        assert again.status == 304
        assert again.read() == b""
        assert again.getheader("ETag") == tag
    finally:
        conn.close()


def test_a_swept_library_is_a_new_tag(listener, fast_scrypt, one_title):
    """The tag is the answer hashed, so it moves when a sweep moves it and
    the browser is never left holding last week's verdicts."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    before = api(listener, "GET", "/api/library", cookie=cookie)[2]["ETag"]

    cache = SweepCache.load(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), Policy.from_config().fingerprint()
    )
    cache.record(
        os.path.join(config.MEDIA_DIRS[0], "Dune (2024)", "Extra.mkv"),
        FileKey(11, 2, 2, "eng"),
        Verdict(Status.CONFORM, "nothing to do"),
    )
    cache.save()
    library.forget()

    # The browser comes back holding the old tag and is handed the shelf
    # itself, not a 304: what it has is out of date.
    status, shelf, headers = api(
        listener, "GET", "/api/library", cookie=cookie, headers={"If-None-Match": before}
    )
    assert status == 200
    assert headers["ETag"] != before
    assert shelf["titles"]


def test_the_summary_is_revalidated_too(listener, fast_scrypt, one_title):
    """The overview asks for it on every visit to the dashboard."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    tag = api(listener, "GET", "/api/library/summary", cookie=cookie)[2]["ETag"]
    conn = keep_alive(listener)
    try:
        conn.request(
            "GET", "/api/library/summary", headers={"Cookie": cookie, "If-None-Match": tag}
        )
        assert conn.getresponse().status == 304
    finally:
        conn.close()


def test_a_kept_connection_does_not_stall_between_answers(listener):
    """Nagle's algorithm against the peer's delayed ACK: an answer is two
    writes, and on a kept-alive connection the second waited about 40ms."""
    conn = keep_alive(listener)
    try:
        started = time.monotonic()
        for _ in range(50):
            conn.request("GET", "/health")
            conn.getresponse().read()
        assert time.monotonic() - started < 1.0
    finally:
        conn.close()


def test_the_bundles_are_compressed(listener, web_dir):
    """330KB of JS and CSS gzips to 124KB, and it is the first thing a cold
    visit waits on."""
    script = web_dir / "_app" / "immutable" / "big.abc123.js"
    script.write_text("export const listOfThings = [1, 2, 3];\n" * 400)
    conn = keep_alive(listener)
    try:
        conn.request(
            "GET", "/_app/immutable/big.abc123.js", headers={"Accept-Encoding": "gzip"}
        )
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Encoding") == "gzip"
        assert response.getheader("Vary") == "Accept-Encoding"
        # Still the hashed-name cache, which the compression does not touch.
        assert "immutable" in response.getheader("Cache-Control")
        assert gzip.decompress(response.read()).decode() == script.read_text()
    finally:
        conn.close()


def test_a_font_is_left_alone(listener, web_dir):
    """Already compressed. Gzipping one spends CPU to add bytes."""
    font = web_dir / "_app" / "immutable" / "face.abc123.woff2"
    font.write_bytes(bytes(range(256)) * 40)
    conn = keep_alive(listener)
    try:
        conn.request(
            "GET", "/_app/immutable/face.abc123.woff2", headers={"Accept-Encoding": "gzip"}
        )
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Encoding") is None
        assert response.read() == font.read_bytes()
    finally:
        conn.close()


def test_an_unchanged_history_comes_back_as_a_header(listener, fast_scrypt, tmp_path):
    """The events page refetches a hundred lines on every visit, and between
    two visits with nothing happening they are the same hundred lines."""
    users.add("watcher", "right password", "viewer")
    cookie = sign_in(listener, "watcher")
    for n in range(20):
        events.record("swept", run=f"r{n}", files=n)

    status, first, headers = api(listener, "GET", "/api/events?limit=100", cookie=cookie)
    assert status == 200
    tag = headers["ETag"]
    assert len(first["events"]) == 20

    conn = keep_alive(listener)
    try:
        conn.request(
            "GET", "/api/events?limit=100", headers={"Cookie": cookie, "If-None-Match": tag}
        )
        assert conn.getresponse().status == 304
    finally:
        conn.close()

    # One more line and the browser is handed the history rather than a header.
    events.record("swept", run="r99", files=99)
    status, _, again = api(
        listener, "GET", "/api/events?limit=100", cookie=cookie, headers={"If-None-Match": tag}
    )
    assert status == 200
    assert again["ETag"] != tag


def test_a_client_dropping_a_kept_connection_is_not_an_error(listener, capfd):
    """Ordinary, and now the common case: the socket waits for a request that
    never comes, and the library aborts its own covers on every navigation
    away from a loading grid. A stack trace each would drown the log."""
    conn = keep_alive(listener)
    conn.request("GET", "/health")
    conn.getresponse().read()
    # Reset rather than shut down politely, which is what a browser closing a
    # tab does: SO_LINGER 0 sends RST instead of FIN.
    conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    conn.close()
    time.sleep(0.4)
    assert "Traceback" not in capfd.readouterr().err

    # And the listener is still serving everyone else.
    assert request(listener, "GET", "/health")[0] == 200
