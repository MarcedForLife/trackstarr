"""Full queue reads, admin-only reordering and bulk actions on waiting work."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from conftest import (
    api,
    cache,
    claim,
    configured_arr,
    movie,
    pending,
    read_events,
    run_task,
    set_config,
    sign_in,
    step,
)
from trackstarr import api as api_module
from trackstarr import library, lifecycle, pauses, runs, sweep_cache, users, work
from trackstarr import sweep as sweep_module
from trackstarr.policy import Policy
from trackstarr.status import Status


@pytest.fixture
def queued(listener, fast_scrypt, clean_registry):
    set_config(MEDIA_DIRS=["/media"])
    users.add("admin", "right password", "admin")
    users.add("viewer", "right password", "viewer")
    lifecycle.open_run("sweep", runs.SWEEP)
    lifecycle.open_run("import", runs.IMPORT, filling=True)
    for i in range(125):
        run = "import" if i % 2 else "sweep"
        work.scheduler.submit(run, f"/media/File {i:03}.mkv", "work", lambda: None)
    return listener, sign_in(listener, "admin"), sign_in(listener, "viewer")


def test_queue_access_search_paging_and_overview_preview(queued):
    listener, admin, viewer = queued
    assert api(listener, "GET", "/api/queue")[0] == 401
    code, page, _ = api(listener, "GET", "/api/queue?q=FILE%201&offset=10", cookie=viewer)
    assert code == 200
    assert (page["total"], page["matched"], len(page["items"])) == (125, 25, 15)
    assert page["items"][0]["path"] == "/media/File 110.mkv"
    assert api(listener, "GET", "/api/queue?offset=no", cookie=viewer)[0] == 400
    _, limited, _ = api(listener, "GET", "/api/queue?limit=1000&offset=-1", cookie=viewer)
    assert len(limited["items"]) == 100
    assert api(listener, "POST", "/api/queue", {"action": "top"}, cookie=viewer)[0] == 403
    _, preview, _ = api(listener, "GET", "/api/runs", cookie=admin)
    assert len(preview["queue_preview"]) == 3


def test_a_searched_row_keeps_its_place_in_the_whole_queue(queued):
    """A row says where a worker will reach it, not where it landed among the
    matches, so a search does not renumber the queue."""
    listener, _, viewer = queued
    _, whole, _ = api(listener, "GET", "/api/queue", cookie=viewer)
    assert [item["position"] for item in whole["items"]] == list(range(1, 51))
    ranks = {item["path"]: item["position"] for item in whole["items"]}
    _, found, _ = api(listener, "GET", "/api/queue?q=FILE%2001", cookie=viewer)
    seen = [item for item in found["items"] if item["path"] in ranks]
    assert seen and all(item["position"] == ranks[item["path"]] for item in seen)
    assert any(item["position"] != at for at, item in enumerate(found["items"], 1))


def test_a_page_says_which_queue_it_describes(queued):
    """The pair a reader joins pages on: the numbering and whose it is."""
    listener, _, viewer = queued
    _, first, _ = api(listener, "GET", "/api/queue", cookie=viewer)
    _, beside, _ = api(listener, "GET", "/api/queue?offset=50", cookie=viewer)
    assert (beside["epoch"], beside["revision"]) == (first["epoch"], first["revision"])
    step(work.scheduler)
    _, later, _ = api(listener, "GET", "/api/queue?offset=50", cookie=viewer)
    assert later["epoch"] == first["epoch"]
    assert later["revision"] > first["revision"]


def test_a_page_carries_the_plan_behind_each_waiting_file(queued):
    """So a row can say what the rewrite does without opening it."""
    listener, _, viewer = queued
    cache(("/media/File 000.mkv", pending()))
    _, page, _ = api(listener, "GET", "/api/queue", cookie=viewer)
    assert page["plans"]["/media/File 000.mkv"]["adds"] == ["2.0"]
    assert "/media/File 001.mkv" not in page["plans"], "no verdict, so nothing to say"
    assert page["plans_current"] is True


def test_a_page_says_when_the_rules_have_moved_past_its_plans(queued):
    """The tallies are the last assessment, not what the queued rewrite will
    do: the work phase replans under the rules in force."""
    listener, _, viewer = queued
    cache(("/media/File 000.mkv", pending()))
    set_config(AUDIO_LAYOUTS=("2.0",))
    library.forget()
    _, page, _ = api(listener, "GET", "/api/queue", cookie=viewer)
    assert page["plans_current"] is False
    assert page["plans"]["/media/File 000.mkv"]["adds"] == ["2.0"], "still worth showing"


def test_two_runs_waiting_on_one_file_share_the_one_assessment(queued):
    """A path can wait in two runs, and the row is told apart by which. The
    stored assessment belongs to the path, not to either admission."""
    listener, _, viewer = queued
    work.scheduler.submit("sweep", "/media/File 001.mkv", "work", lambda: None)
    cache(("/media/File 001.mkv", pending()))
    _, page, _ = api(listener, "GET", "/api/queue?q=File%20001", cookie=viewer)
    assert {item["run"] for item in page["items"]} == {"import", "sweep"}
    assert list(page["plans"]) == ["/media/File 001.mkv"]


def test_queue_reordering_undo_and_stale_files(queued):
    listener, admin, _ = queued
    item = {"run": "import", "path": "/media/File 123.mkv"}
    code, answer, _ = api(
        listener, "POST", "/api/queue", {"action": "top", "items": [item]}, cookie=admin
    )
    assert code == 200 and answer["moved"] == 1
    _, page, _ = api(listener, "GET", "/api/queue", cookie=admin)
    assert page["items"][0]["path"] == item["path"]
    token = answer["undo"]
    code, _, _ = api(
        listener, "POST", "/api/queue", {"action": "undo", "token": token}, cookie=admin
    )
    assert code == 200
    assert (
        api(listener, "POST", "/api/queue", {"action": "undo", "token": token}, cookie=admin)[0]
        == 409
    )
    step(work.scheduler)
    _, stale, _ = api(
        listener,
        "POST",
        "/api/queue",
        {"action": "skip", "items": [{"run": "sweep", "path": "/media/File 000.mkv"}]},
        cookie=admin,
    )
    assert stale["changed"] == 0


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"action": "no"},
        {"action": "skip"},
        {"action": "top", "items": []},
        {"action": "top", "items": [{"run": "sweep", "path": "x"}] * 1001},
        {"action": "top", "items": [None]},
        {"action": "top", "items": [{"run": 1, "path": "x"}]},
        {"action": "top", "items": [{"run": "sweep", "path": 1}]},
        *[
            {
                "action": "pause",
                "items": [{"run": "sweep", "path": "/media/File 000.mkv"}],
                "seconds": seconds,
            }
            for seconds in [True, -1, "bad"]
        ],
    ],
)
def test_queue_rejects_malformed_actions(queued, body):
    listener, admin, _ = queued
    assert api(listener, "POST", "/api/queue", body, cookie=admin)[0] == 400


def test_bulk_pause_and_skip_only_affect_still_waiting_files(queued):
    listener, admin, _ = queued
    first = claim(work.scheduler)
    items = [
        {"run": "sweep", "path": "/media/File 000.mkv"},
        {"run": "import", "path": "/media/File 001.mkv"},
    ]
    _, answer, _ = api(
        listener,
        "POST",
        "/api/queue",
        {"action": "pause", "items": items, "seconds": 3600},
        cookie=admin,
    )
    assert answer["changed"] == 1
    assert pauses.paused(items[1]["path"]) is not None
    assert pauses.paused(first.path) is None
    assert not work.scheduler.skipped(first.run, first.path)
    _, answer, _ = api(
        listener,
        "POST",
        "/api/queue",
        {"action": "skip", "items": [{"run": "sweep", "path": "/media/File 002.mkv"}]},
        cookie=admin,
    )
    assert answer["changed"] == 1
    run_task(work.scheduler, first)


def test_pause_refusals_preserve_the_queue(queued, monkeypatch):
    listener, admin, _ = queued
    work.scheduler.submit("sweep", "/elsewhere/file.mkv", "work", lambda: None)
    outside = {"action": "pause", "items": [{"run": "sweep", "path": "/elsewhere/file.mkv"}]}
    assert api(listener, "POST", "/api/queue", outside, cookie=admin)[0] == 400
    body = {"action": "pause", "items": [{"run": "sweep", "path": "/media/File 000.mkv"}]}
    monkeypatch.setattr(pauses, "MAX_PAUSES", 0)
    assert api(listener, "POST", "/api/queue", body, cookie=admin)[0] == 409
    monkeypatch.setattr(pauses, "MAX_PAUSES", 200)

    def failed(*args):
        raise OSError("read-only")

    monkeypatch.setattr(pauses, "place_many", failed)
    assert api(listener, "POST", "/api/queue", body, cookie=admin)[0] == 500
    assert not work.scheduler.skipped("sweep", "/media/File 000.mkv")


def test_queue_and_activity_include_only_visible_cover_identities(queued, monkeypatch):
    listener, admin, _ = queued
    looked_up = []

    def covers(paths):
        paths = list(paths)
        looked_up.append(paths)
        return {path: {"id": "arr:radarr:1", "name": "Film"} for path in paths}

    monkeypatch.setattr(library, "covers_for_paths", covers)
    _, page, _ = api(listener, "GET", "/api/queue?offset=50", cookie=admin)
    assert looked_up[-1] == [item["path"] for item in page["items"]]
    assert len(page["covers"]) == 50
    _, activity, _ = api(listener, "GET", "/api/runs", cookie=admin)
    assert set(activity["covers"]) == {item["path"] for item in activity["queue_preview"]}
    assert len(looked_up[-1]) == 3


def test_file_details_are_viewer_readable_and_require_a_path(queued, monkeypatch):
    listener, _, viewer = queued
    monkeypatch.setattr(
        library, "file_detail", lambda path, card=False: {"current": False, "file": None}
    )
    assert api(listener, "GET", "/api/library/file?path=/media/file.mkv")[0] == 401
    assert api(listener, "GET", "/api/library/file", cookie=viewer)[0] == 400
    code, answer, _ = api(
        listener, "GET", "/api/library/file?path=/media/file.mkv", cookie=viewer
    )
    assert code == 200
    assert answer == {"current": False, "file": None}


def test_title_work_reports_global_queue_positions_for_viewers(queued, monkeypatch):
    listener, _, viewer = queued
    monkeypatch.setattr(
        library,
        "selected",
        lambda ids: [SimpleNamespace(folders=["/media"])] if ids == ["film"] else [],
    )
    assert api(listener, "GET", "/api/library/work?id=film")[0] == 401
    assert api(listener, "GET", "/api/library/work?id=unknown", cookie=viewer)[0] == 404
    _, answer, _ = api(listener, "GET", "/api/library/work?id=film", cookie=viewer)
    assert len(answer["queued"]) == 125
    assert answer["queued"][-1]["position"] == 125
    assert answer["active"] == []


def test_activity_endpoints_reach_the_scheduler_through_lifecycle_only():
    """Queue reads, promotion and undo are lifecycle operations, so no handler
    can compose a consistency boundary of its own."""
    assert not hasattr(api_module, "work")


def test_queue_pause_handles_transaction_capacity_race(queued, monkeypatch):
    listener, admin, _ = queued
    body = {"action": "pause", "items": [{"run": "sweep", "path": "/media/File 000.mkv"}]}
    # The transaction owns capacity; queue commands have no separate precheck.
    monkeypatch.setattr(pauses, "MAX_PAUSES", 0)
    assert api(listener, "POST", "/api/queue", body, cookie=admin)[0] == 409
    assert not work.scheduler.skipped("sweep", "/media/File 000.mkv")
    monkeypatch.setattr(pauses, "MAX_PAUSES", 200)
    body["seconds"] = float("inf")
    assert api(listener, "POST", "/api/queue", body, cookie=admin)[0] == 400
    assert not work.scheduler.skipped("sweep", "/media/File 000.mkv")


@pytest.mark.parametrize("action", ["top", "skip", "pause", "undo", "run-skip"])
def test_reserved_queue_commands_return_conflict_without_partial_changes(queued, action):
    listener, admin, _ = queued
    one = {"run": "sweep", "path": "/media/File 000.mkv"}
    two = {"run": "import", "path": "/media/File 001.mkv"}
    _, token = work.scheduler.move_top({(two["run"], two["path"])})
    with work.scheduler.pause_selection({(one["run"], one["path"])}) as selected:
        body = {"action": action, "items": [one, two], "token": token}
        endpoint = "/api/queue"
        if action == "run-skip":
            endpoint, body = "/api/runs/skip", one
        code, _, _ = api(listener, "POST", endpoint, body, cookie=admin)
        assert code == 409
        assert not work.scheduler.skipped(two["run"], two["path"])
        pauses.place_many([(path, "", "") for _, path in selected])


def test_queue_pause_batch_capacity_deduplication_and_extension(queued, monkeypatch):
    listener, admin, _ = queued
    one = {"run": "sweep", "path": "/media/File 000.mkv"}
    two = {"run": "import", "path": "/media/File 001.mkv"}
    body = {"action": "pause", "items": [one, two]}
    monkeypatch.setattr(pauses, "MAX_PAUSES", 1)
    assert api(listener, "POST", "/api/queue", body, cookie=admin)[0] == 409
    assert not pauses.current()
    assert work.scheduler.snapshot()["total"] == 125
    pauses.place(one["path"])
    work.scheduler.submit("import", one["path"], "work", lambda: None)
    body["items"] = [one, one, {"run": "import", "path": one["path"]}]
    saved = []
    save = pauses._save

    def counted_save(found):
        saved.append(len(found))
        save(found)

    monkeypatch.setattr(pauses, "_save", counted_save)
    code, answer, _ = api(listener, "POST", "/api/queue", body, cookie=admin)
    assert code == 200 and answer == {"changed": 2}
    assert saved == [1]
    assert len(pauses.current()) == 1


def test_committed_queue_pause_is_not_reported_as_failed_when_response_disconnects(queued):
    def disconnected(answer):
        assert answer == {"changed": 1}
        raise OSError("client disconnected")

    handler = SimpleNamespace(
        read_json=lambda: {
            "action": "pause",
            "items": [{"run": "sweep", "path": "/media/File 000.mkv"}],
        },
        send_json=disconnected,
        reply=lambda *args: pytest.fail("a committed pause was reported as failed"),
    )
    with pytest.raises(OSError, match="client disconnected"):
        api_module._queue_action(handler, SimpleNamespace(name="admin"))
    assert pauses.paused("/media/File 000.mkv") is not None
    assert work.scheduler.skipped("sweep", "/media/File 000.mkv")


def test_activity_http_responses_do_not_wait_for_catalogue(queued, monkeypatch):

    listener, admin, _ = queued
    entered, release = Event(), Event()
    arr = configured_arr()
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])

    def blocked(self):
        entered.set()
        assert release.wait(10)
        return [movie(1, "Media", "/media")]

    monkeypatch.setattr(type(arr), "all_items", blocked)
    try:
        library.start_refresh()
        assert entered.wait(5)
        for route in ("/api/runs", "/api/queue"):
            code, answer, _ = api(listener, "GET", route, cookie=admin)
            assert code == 200
            assert answer["covers"] == {}
        path = "/media/File 000.mkv"
        for route, body in (
            ("/api/pauses", {"paths": [path], "seconds": 300}),
            ("/api/pauses/resume", {"paths": [path]}),
        ):
            assert api(listener, "POST", route, body, cookie=admin)[0] == 200
        assert not release.is_set()
        release.set()
        library._catalogue.future.result(timeout=5)
        for route in ("/api/runs", "/api/queue"):
            code, answer, _ = api(listener, "GET", route, cookie=admin)
            assert code == 200
            assert answer["covers"][path] == {"id": "arr:radarr:1", "name": "Media"}
        library.forget()
        entered.clear()
        release.clear()
        library.start_refresh()
        assert entered.wait(5)
        for route in ("/api/pauses", "/api/pauses/resume"):
            code, _, _ = api(listener, "POST", route, {"ids": ["arr:radarr:1"]}, cookie=admin)
            assert code == 200
        assert not release.is_set()
    finally:
        release.set()


@pytest.mark.parametrize("action", ["skip", "pause"])
@pytest.mark.parametrize("cached", [False, True])
def test_queue_discovery_controls_preserve_verdicts(
    listener, fast_scrypt, clean_registry, monkeypatch, tmp_path, action, cached
):
    root = tmp_path / "library"
    root.mkdir()
    path = str(root / "film.mkv")
    set_config(MEDIA_DIRS=[str(root)])
    fingerprint = Policy.from_config().fingerprint()
    if cached:
        cache((path, pending()), (str(root / "departed.mkv"), pending()))
    expected = sweep_cache.read(sweep_cache.cache_path(), fingerprint).files.get(path)
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")
    queued, release = Event(), Event()
    submit = work.scheduler.submit
    observed = sweep_module._observed

    def observe(path, store, call, stopped=None):
        judged = observed(path, store, call, stopped=stopped)
        store.publish_view()
        return judged

    monkeypatch.setattr(sweep_module, "_observed", observe)

    def enqueue(*args, **kwargs):
        future = submit(*args, **kwargs)
        queued.set()
        assert release.wait(5)
        return future

    monkeypatch.setattr(work.scheduler, "start", lambda: None)
    monkeypatch.setattr(work.scheduler, "submit", enqueue)
    monkeypatch.setattr(sweep_module, "walk_library", lambda policy: [path])
    monkeypatch.setattr(
        sweep_module, "process", lambda *args, **kwargs: pytest.fail("skipped file opened")
    )
    with ThreadPoolExecutor() as pool:
        walking = pool.submit(sweep_module.sweep, True, "controlled")
        try:
            assert queued.wait(5)
            code, answer, _ = api(
                listener,
                "POST",
                "/api/queue",
                {"action": action, "items": [{"run": "controlled", "path": path}]},
                cookie=cookie,
            )
            assert code == 200 and answer["changed"] == 1
            step(work.scheduler)
            # Publish before reading, as the walk does between completions.
            # A changed live view takes precedence over the file on disk.
            view = sweep_cache.live_view(fingerprint)
            visible = (
                view[1]
                if view
                else sweep_cache.read(sweep_cache.cache_path(), fingerprint).files
            )
            assert visible.get(path) == expected
        finally:
            release.set()
        counts = walking.result(timeout=5)
    assert counts[Status.DEFERRED] == 1
    assert sum(counts.values()) == 1
    assert sweep_cache.read(sweep_cache.cache_path(), fingerprint).files == (
        {path: expected} if cached else {}
    )
    pauses.forget()
    assert (pauses.paused(path) is not None) == (action == "pause")
    event = next(event for event in read_events() if event["event"] == "sweep")
    assert event["files"] == 1 and not event.get("stopped")


def test_cold_partial_library_titles_can_be_paused(queued, monkeypatch):
    listener, admin, _ = queued
    arrs = [configured_arr(), configured_arr("sonarr")]
    monkeypatch.setattr(library, "all_arrs", lambda: arrs)

    def fetch(self):
        if self.name == "sonarr":
            raise OSError("offline")
        return [movie(1, "Healthy", "/media/Healthy")]

    monkeypatch.setattr(type(arrs[0]), "all_items", fetch)
    code, shelf, _ = api(listener, "GET", "/api/library", cookie=admin)
    assert code == 200
    assert not shelf["complete"]
    assert shelf["titles"][0]["id"] == "arr:radarr:1"
    assert library.covers_for_paths(["/media/Healthy/file.mkv"]) == {
        "/media/Healthy/file.mkv": {"id": "arr:radarr:1", "name": "Healthy"}
    }
    assert (
        api(listener, "POST", "/api/pauses", {"ids": ["arr:radarr:1"]}, cookie=admin)[0] == 200
    )
    pauses.forget()
    assert pauses.paused("/media/Healthy/file.mkv") is not None
