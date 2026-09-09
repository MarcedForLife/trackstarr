"""The listener under the API: the served UI, the sockets it keeps and the
compression and revalidation on the way out. What the routes answer is
test_api.py's."""

import gzip
import json
import os
import socket
import struct
import time

import pytest

from conftest import api, get_raw, keep_alive, request, set_config, sign_in
from trackstarr import assets, config, covers, events, library, users
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict


def test_health_needs_no_secret(listener):
    assert request(listener, "GET", "/health")[0] == 200


def test_an_unknown_path_is_a_404(listener):
    """With no WEB_DIR nothing else is served, so a scanner finding this port
    learns nothing."""
    assert request(listener, "GET", "/admin")[0] == 404


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


def test_an_extensionless_miss_without_a_shell_is_a_404(tmp_path):
    """A SPA route only falls back to index.html when there is one; a WEB_DIR
    with no shell must 404 rather than point at a file that isn't there."""
    root = tmp_path / "webui"
    root.mkdir()
    set_config(WEB_DIR=str(root))
    assert assets.static_file("/library") is None


def test_a_file_that_cannot_be_read_is_a_404(listener, web_dir, monkeypatch):
    """_static_file vetted the path, but a race can still leave it unopenable;
    the shell must 404 rather than crash the handler thread."""
    monkeypatch.setattr(assets, "static_file", lambda url_path: str(web_dir / "_app"))
    assert get_raw(listener, "/")[0] == 404


# The transport: one connection, compressed, and revalidated


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
        os.path.join(config.current().MEDIA_DIRS[0], "Dune (2024)", "Extra.mkv"),
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
