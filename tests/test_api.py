"""The JSON API under /api: what each route answers, who may call it and what
it refuses. The sockets under it are test_server.py's and the credential store
is test_auth.py's."""

import http.client
import json
import logging
import os
import threading
import urllib.error

import pytest

from conftest import api, keep_alive, read_events, request, set_config, sign_in
from trackstarr import (
    auth,
    config,
    connections,
    covers,
    events,
    holds,
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
    assert config.current().RULE_MODES["commentary"] == "always"
    # The history credits the account that saved it, which is the endpoint's
    # to know: settings.update() is handed a body and nothing else.
    saved = next(entry for entry in read_events() if entry["event"] == "settings")
    assert saved["by"] == "admin"


@pytest.mark.parametrize("route", ["/api/settings", "/api/connections/test"])
def test_a_write_with_an_unreadable_body_is_a_400(listener, fast_scrypt, route):
    users.add("admin", "right password", "admin")
    headers = {"Content-Type": "application/json", "Cookie": sign_in(listener, "admin")}
    status, _ = request(listener, "POST", route, b"{not json", headers)
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
    assert "nope" not in config.current().RULE_MODES


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


@pytest.mark.parametrize(
    "route", ["/api/runs/start", "/api/library/clear"], ids=["a second sweep", "a clear"]
)
def test_a_write_that_fights_a_running_sweep_is_refused(
    listener, fast_scrypt, clean_registry, route
):
    """Two walks would fight over the sweep cache and over pending.tsv. A clear
    is no better: a running sweep holds the entries in memory and writes the
    whole file at every checkpoint, so deleting it is undone or loses the walk."""
    users.add("admin", "right password", "admin")
    clean_registry.open_run("r#1", runs.SWEEP)
    status, answer, _ = api(listener, "POST", route, {}, cookie=sign_in(listener, "admin"))
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
    set_config(IMDB_RATINGS=True)
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


def test_the_scores_are_not_fetched_with_the_setting_off(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    set_config(IMDB_RATINGS=False)

    status, answer, _ = api(
        listener, "POST", "/api/library/ratings", {}, cookie=sign_in(listener, "admin")
    )

    assert status == 409
    assert "switched off" in answer["status"]


def test_the_scores_are_not_rebuilt_from_half_a_library(listener, fast_scrypt, monkeypatch):
    """An *arr that could not be listed is every one of its titles losing its
    score until tomorrow, which is worse than the button doing nothing."""
    users.add("admin", "right password", "admin")
    set_config(IMDB_RATINGS=True)
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
    set_config(IMDB_RATINGS=True)
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
    set_config(IMDB_RATINGS=True)
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


def test_a_hold_is_placed_by_path_and_listed_back(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    set_config(MEDIA_DIRS=["/data/media/movies"])
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


def test_a_hold_outside_the_library_is_refused(listener, fast_scrypt):
    """A hold is matched by prefix, so one on / would quietly stop everything
    being rewritten."""
    users.add("admin", "right password", "admin")
    set_config(MEDIA_DIRS=["/data/media/movies"])
    cookie = sign_in(listener, "admin")

    assert api(listener, "POST", "/api/holds", {"paths": ["/"]}, cookie=cookie)[0] == 400
    assert api(listener, "POST", "/api/holds", {}, cookie=cookie)[0] == 400
    assert (
        api(listener, "POST", "/api/holds", {"ids": ["arr:radarr:9"]}, cookie=cookie)[0] == 404
    )
    body = {"paths": ["/data/media/movies/f.mkv"], "seconds": -1}
    assert api(listener, "POST", "/api/holds", body, cookie=cookie)[0] == 400


def test_lifting_a_hold_says_how_many_went(listener, fast_scrypt):
    users.add("admin", "right password", "admin")
    set_config(MEDIA_DIRS=["/data/media/movies"])
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
    set_config(MEDIA_DIRS=["/data/media/movies"])
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
    set_config(MEDIA_DIRS=["/data/media/movies"])
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


def test_a_title_names_the_servers_before_they_are_asked(listener, fast_scrypt, one_title):
    """Naming a media server is a read of the settings; finding the title
    inside it is a call, and the sheet stands its buttons up on the first.
    Radarr's link is neither: it is the slug the title arrived with."""
    set_config(PLEX_URL="http://plex:32400")
    set_config(PLEX_TOKEN="token")
    set_config(RADARR_URL="http://radarr:7878")
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

    monkeypatch.setattr(covers, "fetch", answer)
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

    monkeypatch.setattr(covers, "fetch", refuse)
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
