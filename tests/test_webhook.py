"""The *arr intake: what a delivery parses into, and what the /webhook POST
queues. The listener itself is test_server.py's and the queue behind the
intake is test_jobs.py's."""

import http.client
import json
import os
import socket

import pytest

from conftest import (
    cache,
    keep_alive,
    pending,
    read_events,
    request,
    rewrote,
    seed_verdict,
    set_config,
)
from trackstarr import auth, config, jobs, lifecycle, rewrites, sweep_cache, webhook
from trackstarr.arr import AUTH_HEADER
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import SweepCache, Verdict, cache_key
from trackstarr.webhook import Removed, jobs_from_hook, removed_from_hook, renamed_from_hook


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
    assert delivered[0].arr.instance_id == "radarr"


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
    assert delivered[0].arr.instance_id == "sonarr"


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
    """Rename included: it changes no track content, so nothing is queued."""
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


def test_an_upgrade_names_the_file_it_replaced():
    """Both *arrs list what an upgrade displaced under deletedFiles, beside the
    import. Unread, the old file's verdict stands next to the new file's as a
    second variant of the title until a full sweep."""
    removed = removed_from_hook(
        {
            "eventType": "Download",
            "isUpgrade": True,
            "movie": {"id": 1, "folderPath": "/data/media/movies/Film (2024)"},
            "movieFile": {"relativePath": "Film (2024) 2160p.mkv"},
            "deletedFiles": [{"relativePath": "Film (2024) 1080p.mkv"}],
        }
    )
    assert removed == Removed(("/data/media/movies/Film (2024)/Film (2024) 1080p.mkv",))


def test_a_fresh_import_removes_nothing():
    body = {
        "eventType": "Download",
        "movie": {"id": 1, "folderPath": "/data/media/movies/A"},
        "movieFile": {"relativePath": "A.mkv"},
    }
    assert removed_from_hook(body) is None
    assert removed_from_hook({"eventType": "Grab", "movie": {"id": 1}}) is None
    assert removed_from_hook({"eventType": "MovieFileDelete"}) is None, "no *arr key"


@pytest.mark.parametrize(
    ("event", "item", "folder_key", "file_key"),
    [
        ("MovieFileDelete", "movie", "folderPath", "movieFile"),
        ("EpisodeFileDelete", "series", "path", "episodeFile"),
    ],
)
def test_a_deleted_file_arrives_under_the_imports_key(event, item, folder_key, file_key):
    body = {
        "eventType": event,
        item: {"id": 1, folder_key: "/data/media/T"},
        file_key: {"relativePath": "T.mkv"},
        "deleteReason": "manual",
    }
    assert removed_from_hook(body) == Removed(("/data/media/T/T.mkv",))
    assert jobs_from_hook(body) == [], "nothing to judge: the file is gone"


def test_a_title_deleted_with_its_files_names_its_folder():
    body = {
        "eventType": "MovieDelete",
        "movie": {"id": 1, "folderPath": "/data/media/movies/Film (2024)"},
        "deletedFiles": True,
    }
    assert removed_from_hook(body) == Removed(folder="/data/media/movies/Film (2024)")
    # Dropped from the *arr with the files left on disk: they stand, as a
    # folder no *arr claims.
    assert removed_from_hook({**body, "deletedFiles": False}) is None


@pytest.mark.parametrize(
    ("item", "folder_key", "files_key"),
    [("movie", "folderPath", "renamedMovieFiles"), ("series", "path", "renamedEpisodeFiles")],
)
def test_a_rename_names_each_files_old_and_new_path(item, folder_key, files_key):
    body = {
        "eventType": "Rename",
        item: {"id": 1, folder_key: "/data/media/T"},
        files_key: [
            {"previousPath": "/data/media/T/old.mkv", "path": "/data/media/T/new.mkv"},
            {"previousRelativePath": "b.mkv", "relativePath": "Season 01/b.mkv"},
            # Unchanged, nameless and malformed entries say nothing.
            {"previousPath": "/data/media/T/same.mkv", "path": "/data/media/T/same.mkv"},
            {},
            "not a file",
        ],
    }
    assert renamed_from_hook(body) == [
        ("/data/media/T/old.mkv", "/data/media/T/new.mkv"),
        ("/data/media/T/b.mkv", "/data/media/T/Season 01/b.mkv"),
    ]
    assert renamed_from_hook({"eventType": "Rename"}) == [], "no *arr key"


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


@pytest.fixture
def enabled_arrs():
    set_config(
        RADARR_URL="http://radarr",
        RADARR_API_KEY="radarr-key",
        SONARR_URL="http://sonarr",
        SONARR_API_KEY="sonarr-key",
    )


def test_post_queues_only_existing_paths(listener, media_root, queued, enabled_arrs):
    """A POST naming a file this container cannot see, usually a mount mismatch,
    answers 200 and queues nothing."""
    headers = {AUTH_HEADER: auth.mint("radarr")}

    body = movie_body(str(media_root / "missing.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 0")
    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body, headers) == (200, "queued 1")
    assert [job.path for job in queued] == [str(media_root / "f.mkv")]


def test_a_queued_post_records_the_delivery(listener, media_root, queued, enabled_arrs):
    """The run exists in the history before its rewrites do, so a runs view
    can show work still queued."""
    headers = {AUTH_HEADER: auth.mint("radarr")}
    post(listener, movie_body(str(media_root / "f.mkv"), str(media_root)), headers)
    (entry,) = read_events()
    assert entry["event"] == "webhook"
    assert entry["arr"] == "radarr"
    assert entry["files"] == 1
    assert entry["run"] == queued[0].run


def test_a_delivery_names_the_files_it_queued(listener, media_root, enabled_arrs):
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


def stored_paths() -> set[str]:
    """Every path the sweep cache holds a verdict for."""
    fingerprint = Policy.from_config().fingerprint()
    return set(sweep_cache.read(sweep_cache.cache_path(), fingerprint).files)


def test_a_removal_drops_the_verdict_of_a_file_that_has_gone(
    listener, media_root, enabled_arrs
):
    """Not a run: nothing was done to a file, so the history gets no line."""
    kept, gone = str(media_root / "f.mkv"), str(media_root / "old.mkv")
    cache((kept, Verdict(Status.CONFORM)), (gone, Verdict(Status.PENDING)))
    body = {
        "eventType": "MovieFileDelete",
        "movie": {"id": 1, "folderPath": str(media_root)},
        "movieFile": {"path": gone},
        "deleteReason": "upgrade",
    }
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, body, headers) == (200, "queued 0, dropped 1")
    assert stored_paths() == {kept}
    assert read_events() == []


def test_a_removal_of_a_file_still_here_leaves_its_verdict(listener, media_root, enabled_arrs):
    """The *arr spoke of its own copy. A path this container can still see
    keeps what was judged about it."""
    kept = str(media_root / "f.mkv")
    cache((kept, Verdict(Status.CONFORM)))
    body = {
        "eventType": "MovieFileDelete",
        "movie": {"id": 1, "folderPath": str(media_root)},
        "movieFile": {"path": kept},
    }
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, body, headers) == (200, "queued 0, dropped 0")
    assert stored_paths() == {kept}


def test_an_upgrade_queues_the_new_file_and_drops_the_old(
    listener, media_root, queued, enabled_arrs
):
    new, old = str(media_root / "f.mkv"), str(media_root / "old.mkv")
    cache((old, Verdict(Status.PENDING)))
    body = {
        **movie_body(new, str(media_root)),
        "isUpgrade": True,
        "deletedFiles": [{"path": old}],
    }
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, body, headers) == (200, "queued 1, dropped 1")
    assert [job.path for job in queued] == [new]
    assert stored_paths() == set()


def test_a_title_deleted_with_its_files_drops_everything_under_it(
    listener, media_root, enabled_arrs
):
    """MovieDelete names no files, only whether the folder went with it. Its
    entries would otherwise come back as a folder title no *arr claims."""
    folder = media_root / "Film (2024)"
    under = (str(folder / "Film.mkv"), str(folder / "Film.1080p.mkv"))
    elsewhere = str(media_root / "f.mkv")
    cache(*((path, Verdict(Status.CONFORM)) for path in (*under, elsewhere)))
    body = {
        "eventType": "MovieDelete",
        "movie": {"id": 1, "folderPath": str(folder)},
        "deletedFiles": True,
    }
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, body, headers) == (200, "queued 0, dropped 2")
    assert stored_paths() == {elsewhere}


def rename_body(folder: str, *moves: tuple[str, str]) -> dict:
    return {
        "eventType": "Rename",
        "movie": {"id": 1, "folderPath": folder},
        "renamedMovieFiles": [
            {"previousPath": previous, "path": path} for previous, path in moves
        ],
    }


def test_a_rename_takes_the_verdict_and_the_rewrite_record_with_it(
    listener, media_root, enabled_arrs
):
    """The file is the same file under a new name, so what was judged and
    what we did to it both follow it. Neither waits for a walk."""
    (media_root / "g.mkv").write_bytes(b"y")
    moves = [
        (str(media_root / "old-f.mkv"), str(media_root / "f.mkv")),
        (str(media_root / "old-g.mkv"), str(media_root / "g.mkv")),
    ]
    fingerprint = Policy.from_config().fingerprint()
    os.makedirs(config.STATE_DIR, exist_ok=True)
    store = SweepCache(sweep_cache.cache_path(), fingerprint)
    for previous, new in moves:
        # The key a rename leaves alone: the entry holds the file's own.
        seed_verdict(store, previous, cache_key(new, "eng"), pending())
    store.save()
    # Only one of the two was ours to rewrite.
    rewrote(moves[0][0])

    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, rename_body(str(media_root), *moves), headers) == (
        200,
        "queued 0, moved 2",
    )
    stored = sweep_cache.read(sweep_cache.cache_path(), fingerprint).files
    assert set(stored) == {new for _, new in moves}
    assert stored[moves[0][1]]["status"] == "pending"
    assert set(rewrites.records()) == {moves[0][1]}


def test_a_rename_onto_another_file_drops_the_old_verdict(listener, media_root, enabled_arrs):
    """A file at the new path under another key is not the file that was
    judged. The old entry goes and the new file waits for a walk."""
    old, new = str(media_root / "old.mkv"), str(media_root / "f.mkv")
    cache((old, Verdict(Status.PENDING)))

    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, rename_body(str(media_root), (old, new)), headers) == (
        200,
        "queued 0, moved 0",
    )
    assert stored_paths() == set()


def test_a_rename_of_a_file_still_at_its_old_path_moves_nothing(
    listener, media_root, enabled_arrs
):
    """Usually a mount mismatch: the *arr and this container spell the
    library differently, so its rename describes files we cannot see."""
    old, new = str(media_root / "f.mkv"), str(media_root / "new.mkv")
    cache((old, Verdict(Status.CONFORM)))

    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, rename_body(str(media_root), (old, new)), headers) == (
        200,
        "queued 0, moved 0",
    )
    assert stored_paths() == {old}


def test_a_delivery_arriving_during_shutdown_is_refused_not_dropped(
    listener, media_root, queued
):
    """503, so the *arr retries into the process that comes back, rather than
    a 200 for files this one is no longer going to look at."""
    lifecycle.shutdown(0)
    headers = {AUTH_HEADER: auth.mint("radarr")}

    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    assert post(listener, body, headers) == (503, "the service is stopping")
    assert queued == []
    assert read_events() == []


def test_a_post_that_queues_nothing_records_nothing(listener, media_root, enabled_arrs):
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


def test_a_post_for_a_file_already_in_flight_queues_nothing(listener, media_root, enabled_arrs):
    """A sweep and a webhook naming the same file is routine; the second must
    answer 200 having queued nothing, not stack a second rewrite behind it."""
    body = movie_body(str(media_root / "f.mkv"), str(media_root))
    headers = {AUTH_HEADER: auth.mint("radarr")}
    assert post(listener, body, headers) == (200, "queued 1")
    # No workers run here, so the first is still in flight.
    assert post(listener, body, headers) == (200, "queued 0")


def test_a_delivery_is_one_run_on_the_activity_page(
    listener, media_root, clean_registry, enabled_arrs
):
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
    assert run["type"] == "import"
    assert (run["label"], run["total"], run["done"]) == ("Sonarr", 1, 0)


def test_a_delivery_that_names_no_files_registers_nothing(
    listener, clean_registry, enabled_arrs
):
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
