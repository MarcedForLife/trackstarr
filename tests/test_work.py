"""Queue order is execution order, across sources and across processing phases."""

import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import claim, run_task, set_config, step
from trackstarr import events, jobs, lifecycle, notify, pauses, runs, sweep, sweep_cache, work

# Bound here, since conftest replaces the module's own attribute with a no-op
# to keep a settings save in an API test from bringing the pool up.
from trackstarr.lifecycle import wake
from trackstarr.policy import Policy
from trackstarr.processing import Job
from trackstarr.status import Status


@pytest.fixture
def queue(clean_registry):
    lifecycle.open_run("sweep", runs.SWEEP)
    lifecycle.open_run("import", runs.IMPORT, filling=True)
    yield work.scheduler


def add(queue, path, run="sweep", lane="work", call=lambda: None):
    return queue.submit(run, path, lane, call)


def paths(queue):
    return [item["path"] for item in queue.snapshot(limit=1000)["items"]]


def folder(queue, path):
    """The rows one title's page draws, off a capture of the whole queue."""
    return queue.capture().for_folder(path)


def test_reorder_crosses_runs_preserves_selection_order_and_undo(queue):
    for path, run in [("one", "sweep"), ("two", "import"), ("three", "sweep")]:
        add(queue, path, run)
    moved, token = queue.move_top({("sweep", "three"), ("import", "two")})
    assert moved == 2
    assert paths(queue) == ["two", "three", "one"]
    add(queue, "arrived", "import")
    assert paths(queue) == ["two", "three", "one", "arrived"]
    assert queue.restore(token)
    assert paths(queue) == ["one", "two", "three", "arrived"]
    assert not queue.restore(token)
    _, older = queue.move_top({("sweep", "three")})
    queue.move_top({("import", "two")})
    assert not queue.restore(older)
    assert queue.move_top({("sweep", "gone")}) == (0, "")


def test_search_and_pagination_cover_the_whole_queue(queue):
    for number in range(150):
        add(queue, f"/shows/Episode {number:03}.mkv")
    page = queue.snapshot("EPISODE 1", offset=10, limit=5)
    assert (page["total"], page["matched"]) == (150, 50)
    assert [item["path"] for item in page["items"]] == [
        f"/shows/Episode {number}.mkv" for number in range(110, 115)
    ]
    assert queue.snapshot("missing")["items"] == []
    with pytest.raises(ValueError, match="already queued"):
        add(queue, "/shows/Episode 000.mkv")


def test_workers_execute_promoted_work_first_without_displacing_active(queue):
    set_config(MAX_CONCURRENT_REWRITES=1)
    entered, release = threading.Event(), threading.Event()
    reached = []

    def first():
        entered.set()
        assert release.wait(3)
        reached.append("first")

    first_done = add(queue, "first", call=first)
    queue.start()
    queue.start()
    assert entered.wait(3)
    later = add(queue, "later", call=lambda: reached.append("later"))
    next_done = add(queue, "next", "import", call=lambda: reached.append("next"))
    moved, token = queue.move_top({("sweep", "first"), ("import", "next")})
    assert moved == 1
    assert not first_done.done()
    release.set()
    next_done.result(timeout=3)
    later.result(timeout=3)
    assert reached == ["first", "next", "later"]
    assert not queue.active
    assert not queue.tasks
    assert queue.restore(token)


def test_probe_capacity_is_independent_and_limits_follow_settings(queue):
    set_config(MAX_CONCURRENT_REWRITES=1, PROBE_WORKERS=2)
    add(queue, "rewrite")
    add(queue, "blocked")
    add(queue, "probe1", lane="probe")
    add(queue, "probe2", lane="probe")
    add(queue, "probe3", lane="probe")
    with queue.condition:
        assert claim(queue).path == "rewrite"
        assert claim(queue).path == "probe1"
        assert claim(queue).path == "probe2"
        assert claim(queue) is None
        set_config(MAX_CONCURRENT_REWRITES=2)
        assert claim(queue).path == "blocked"
        set_config(PROBE_WORKERS=1)
        run_task(queue, queue.active[("sweep", "probe1")])
        assert claim(queue) is None
        run_task(queue, queue.active[("sweep", "probe2")])
        assert claim(queue).path == "probe3"


def test_pause_stop_skip_and_exceptions_settle_futures(queue):
    lifecycle.pause()
    add(queue, "waiting")
    add(queue, "skipped")
    lifecycle.skip({("sweep", "skipped")})
    with queue.condition:
        task = claim(queue)
        assert task.path == "skipped"
        assert claim(queue) is None
    assert paths(queue) == ["waiting"]
    assert queue.move_top({("sweep", "skipped")}) == (0, "")
    run_task(queue, task)
    lifecycle.stop("sweep")
    assert paths(queue) == []
    assert queue.move_top({("sweep", "waiting")}) == (0, "")
    task = claim(queue)
    run_task(queue, task)
    lifecycle.resume()

    def failing():
        raise RuntimeError("bad file")

    future = add(queue, "bad", "import", call=failing)
    step(queue)
    with pytest.raises(RuntimeError, match="bad file"):
        future.result()
    assert not queue.active
    assert not queue.pending


def test_rank_survives_discovery_and_is_released_after_booking(queue):
    add(queue, "first", lane="probe")
    add(queue, "chosen", lane="probe")
    _, token = queue.move_top({("sweep", "chosen")})
    chosen = claim(queue)
    run_task(queue, chosen)
    queue.continue_file(chosen.future.handle, lambda: None)
    assert paths(queue) == ["chosen", "first"]
    assert queue.restore(token)
    assert paths(queue) == ["first", "chosen"]
    task = claim(queue)
    run_task(queue, task)
    queue.complete_file(task.future.handle)
    assert ("sweep", "first") not in queue.tasks


def test_imports_register_their_actual_paths_before_workers_start(queue, monkeypatch):
    assert jobs.enqueue(Job("/movie.mkv", run="import"))
    assert paths(queue) == ["/movie.mkv"]
    called = []
    monkeypatch.setattr(
        jobs, "_handle", lambda job, accounting, cancel=None, **kw: called.append(job.path)
    )
    step(queue)
    assert called == ["/movie.mkv"]
    assert jobs.enqueue(Job("/movie.mkv", run="import"))

    def failing(job, accounting, cancel=None, **kw):
        raise RuntimeError("a corrupt file")

    monkeypatch.setattr(jobs, "_handle", failing)
    step(queue)
    assert jobs.enqueue(Job("/movie.mkv", run="import"))


def test_unregistered_cli_work_does_not_leave_registry_entries(queue):
    future = add(queue, "cli", run=None)
    step(queue)
    assert future.result() is None
    assert not queue.tasks


def test_task_group_drains_before_releasing_its_cache_even_after_error(queue):
    queue.start()
    with lifecycle.group("sweep") as submit:
        future = submit("normal", "probe", lambda: "done")
        assert future.result(timeout=3) == "done"
        assert queue.complete_file(future.handle)
    lifecycle.pause()
    with (
        pytest.raises(RuntimeError, match="booking failed"),
        lifecycle.group("sweep") as submit,
    ):
        future = submit("stopped", "probe", lambda: lifecycle.hold("sweep"))
        raise RuntimeError("booking failed")
    assert future.result() is False
    assert not queue.active
    assert not queue.pending


def test_title_positions_are_global_and_exclude_sibling_folders(queue):
    add(queue, "/shows/Other/episode.mkv")
    add(queue, "/shows/Show/Season 01/E01.mkv")
    add(queue, "/shows/Show Extra/episode.mkv")
    add(queue, "/shows/Show/Season 01/E02.mkv", "import")
    assert [item["position"] for item in folder(queue, "/shows/Show")] == [2, 4]
    queue.move_top({("import", "/shows/Show/Season 01/E02.mkv")})
    assert [item["position"] for item in folder(queue, "/shows/Show")] == [1, 3]
    assert folder(queue, "/unknown") == []


def test_manual_discovery_is_immediate_but_rewrites_join_the_normal_queue(queue):
    add(queue, "background", lane="probe")
    lifecycle.open_run("manual", runs.RECHECK)
    queue.submit("manual", "chosen", "probe", lambda: None, priority=True)
    assert paths(queue) == ["chosen", "background"]
    task = claim(queue)
    assert (task.run, task.path) == ("manual", "chosen")
    run_task(queue, task)
    queue.continue_file(task.future.handle, lambda: None)
    assert paths(queue) == ["background", "chosen"]


def test_two_runs_cannot_work_on_the_same_file_at_once(queue):
    set_config(MAX_CONCURRENT_REWRITES=2, PROBE_WORKERS=2)
    add(queue, "same", lane="work")
    first = claim(queue)
    lifecycle.open_run("manual", runs.RECHECK)
    queue.submit("manual", "same", "probe", lambda: None, priority=True)
    add(queue, "different", lane="probe")
    with queue.condition:
        second = claim(queue)
        assert second.path == "different"
        assert claim(queue) is None
    run_task(queue, first)
    assert claim(queue).run == "manual"


def test_cache_publication_holds_the_path_but_not_the_scheduler_lock(
    queue, tmp_path, monkeypatch
):
    path = str(tmp_path / "film.mkv")
    (tmp_path / "film.mkv").write_bytes(b"file")
    cache = sweep_cache.SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    publishing, release, same_path = threading.Event(), threading.Event(), threading.Event()
    publish = sweep_cache.publish

    def delayed_publish(*args, **kwargs):
        publishing.set()
        assert release.wait(3)
        return publish(*args, **kwargs)

    monkeypatch.setattr(sweep_cache, "publish", delayed_publish)
    judged = sweep.Judged(
        Job(path), sweep_cache.cache_key(path, None), sweep_cache.Verdict(Status.CONFORM)
    )
    first = add(
        queue,
        path,
        lane="probe",
        call=lambda: sweep._observed(path, cache, lambda policy, observation: judged),
    )
    queue.start()
    try:
        assert publishing.wait(3)
        second = add(queue, path, run="import", call=same_path.set)
        unrelated = add(queue, "unrelated", lane="probe", call=lambda: "done")
        assert unrelated.result(timeout=3) == "done"
        assert queue.snapshot()["total"] == 1
        assert not first.done()
        assert not same_path.is_set()
    finally:
        release.set()
    first.result(timeout=3)
    second.result(timeout=3)
    assert cache._standing()[path]["status"] == "conform"
    assert not queue.active


@pytest.mark.parametrize("fail", [False, True])
def test_pause_write_does_not_hold_scheduler_and_failure_preserves_order(
    queue, monkeypatch, fail
):
    set_config(MAX_CONCURRENT_REWRITES=2)
    writing, release = threading.Event(), threading.Event()
    save = pauses._save

    def blocked_save(found):
        writing.set()
        assert release.wait(3)
        if fail:
            raise OSError("read-only")
        save(found)

    monkeypatch.setattr(pauses, "_save", blocked_save)
    first = add(queue, "/media/first.mkv")
    second = add(queue, "/media/second.mkv")
    keys = {("sweep", "/media/first.mkv"), ("sweep", "/media/second.mkv")}

    def pause():
        with queue.pause_selection(keys) as selected:
            pauses.place_many([(path, "", "") for _, path in selected])

    with ThreadPoolExecutor() as pool:
        command = pool.submit(pause)
        try:
            assert writing.wait(3)
            queue.start()
            assert paths(queue) == ["/media/first.mkv", "/media/second.mkv"]
            assert queue.count("work") == 2
            unrelated = add(queue, "/media/unrelated.mkv", call=lambda: "done")
            assert unrelated.result(timeout=3) == "done"
            assert not first.done() and not second.done()
            # Pause dispatch so rollback order can be inspected before draining.
            lifecycle.pause()
        finally:
            release.set()
        if fail:
            with pytest.raises(OSError, match="read-only"):
                command.result(timeout=3)
            assert paths(queue) == ["/media/first.mkv", "/media/second.mkv"]
            assert not pauses.current()
            assert not queue.skipped("sweep", "/media/first.mkv")
        else:
            command.result(timeout=3)
            # Committed: both leave the visible queue, and dispatch is free to
            # drain them past the pause, which is what a skip is for.
            assert paths(queue) == []
            assert {p.path for p in pauses.current()} == {
                "/media/first.mkv",
                "/media/second.mkv",
            }
        assert not queue.reserved
        lifecycle.resume()
        first.result(timeout=3)
        second.result(timeout=3)


@pytest.mark.parametrize("finish_run", [lifecycle.stop, lifecycle.close_run])
def test_reserved_pause_survives_stop_or_close_and_conflicts_are_atomic(queue, finish_run):
    one, two = "/media/one.mkv", "/media/two.mkv"
    add(queue, one)
    add(queue, two)
    _, token = queue.move_top({("sweep", two)})
    with queue.pause_selection({("sweep", one)}) as selected:
        finish_run("sweep")
        for command in [queue.skip, queue.move_top]:
            with pytest.raises(work.ConflictError):
                command({("sweep", one), ("sweep", two)})
        with pytest.raises(work.ConflictError), queue.pause_selection({("sweep", one)}):
            pytest.fail("overlapping reservation succeeded")
        with pytest.raises(work.ConflictError):
            queue.restore(token)
        with pytest.raises(work.ConflictError):
            queue.skip_file("sweep", one)
        assert not queue.skipped("sweep", two)
        pauses.place_many([(path, "", "") for _, path in selected])
    assert pauses.paused(one) is not None
    assert not queue.reserved
    assert queue.restore(token)


def test_queue_notifications_and_audits_run_after_unlock(queue, monkeypatch):
    publish, record = notify.publish, events.record
    audited = []

    def check_publish(kind):
        assert not queue.condition._is_owned()
        publish(kind)

    def check_record(kind, **fields):
        assert not queue.condition._is_owned()
        audited.append(kind)
        record(kind, **fields)

    monkeypatch.setattr(notify, "publish", check_publish)
    monkeypatch.setattr(events, "record", check_record)
    set_config(MEDIA_DIRS=["/media"])
    add(queue, "/media/one")
    add(queue, "/media/two")
    add(queue, "/media/three")
    assert lifecycle.skip({("sweep", "/media/one")}, "admin") == lifecycle.Outcome(1)
    assert lifecycle.pause_selection(
        {("sweep", "/media/two")}, 0, "admin"
    ) == lifecycle.Outcome(1)
    assert audited == ["skipped", "item_paused", "skipped"]
    assert lifecycle.skip_file("sweep", "/media/three", "admin") == ("waiting", 0)
    # Nothing live to take off: no audit, and the API answers 404 off this.
    assert lifecycle.skip_file("missing", "file") == ("", 0)
    assert lifecycle.skip_file("sweep", "/media/gone") == ("", 0)
    assert lifecycle.skip({("missing", "file")}) == lifecycle.Outcome(0)
    with queue.pause_selection({("missing", "file")}) as selected:
        assert selected == ()
    assert audited == ["skipped", "item_paused", "skipped", "skipped"]


@pytest.mark.parametrize("state", ["pending", "active", "awaiting_decision"])
def test_a_skip_names_the_phase_and_dies_with_the_admission(queue, state):
    """A skip is "not in this pass", and the pass is the file's admission: the
    next delivery of the same path must not inherit it."""
    lane = "probe" if state == "awaiting_decision" else "work"
    phase = add(queue, "same", lane=lane)
    task = None
    if state != "pending":
        task = claim(queue)
        if state == "awaiting_decision":
            run_task(queue, task)

    expected = "active" if state == "active" else "waiting"
    assert lifecycle.skip_file("sweep", "same") == (expected, 0)
    assert queue.skipped("sweep", "same")
    assert queue.controls["sweep"].skipped == {"same"}
    assert paths(queue) == []
    # Two taps on the row is the page's to do, and the queue still counts one file.
    assert lifecycle.skip_file("sweep", "same") == (expected, 0)
    assert queue.snapshot()["total"] == 0

    if state == "awaiting_decision":
        assert queue.complete_file(phase.handle)
    else:
        if state == "pending":
            task = claim(queue)
        run_task(queue, task)
    assert not queue.skipped("sweep", "same")
    assert not queue.controls["sweep"].skipped
    add(queue, "same")
    assert paths(queue) == ["same"]


def test_anonymous_cli_work_is_skipped_by_the_row_the_page_draws(queue):
    """Work with no run joins the same queue and shows an empty run on its row,
    which is the key a skip comes back with."""
    phase = add(queue, "cli", run=None)
    assert queue.snapshot()["items"][0]["run"] == ""
    assert lifecycle.skip({("", "cli")}) == lifecycle.Outcome(1)
    assert queue.skipped("", "cli")
    assert paths(queue) == []
    step(queue)
    assert phase.result() is None


def test_a_stopped_run_finishes_its_phase_and_abandons_what_follows(queue):
    """Stop is between files: the phase in hand still lands, and anything the
    producer hands over afterwards goes straight to the abandoned lane."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    running = add(queue, "in hand")
    task = claim(queue)
    assert lifecycle.stop("sweep")
    assert not lifecycle.stop("gone")
    lifecycle.pause()
    late = add(queue, "late")
    run_task(queue, task)
    assert running.result() is None
    assert paths(queue) == []
    # Paused, and still dispatched: a stopped file's callable has its own early
    # exit to reach rather than waiting out the pause.
    abandoned = claim(queue)
    assert abandoned.path == "late"
    run_task(queue, abandoned)
    assert late.result() is None


def test_stopping_everything_asks_each_open_run_once(queue):
    add(queue, "waiting")
    add(queue, "delivered", "import")
    assert lifecycle.stop_all() == 2
    assert all(queue.stopping(run) for run in ("sweep", "import"))
    assert paths(queue) == []
    # Nothing left to ask the second time, which is what the page reports.
    assert lifecycle.stop_all() == 0


class NoTraversal(dict):
    def values(self):
        pytest.fail("the backlog was traversed")

    def __iter__(self):
        pytest.fail("the backlog was traversed")


def test_saturated_and_ordinary_claims_never_project_or_traverse_backlog(queue, monkeypatch):
    set_config(MAX_CONCURRENT_REWRITES=1, PROBE_WORKERS=1)
    for number in range(1000):
        add(queue, str(number))
    add(queue, "probe", lane="probe")
    queue.pending = NoTraversal(queue.pending)
    queue.active = NoTraversal(queue.active)
    monkeypatch.setattr(queue, "_relist", lambda: pytest.fail("dispatch sorted the queue"))
    first = claim(queue)
    probe = claim(queue)
    for _ in range(10):
        assert claim(queue) is None
    run_task(queue, first)
    next_task = claim(queue)
    assert next_task.path == "1"
    run_task(queue, next_task)
    run_task(queue, probe)
    assert queue.occupied == {"probe": 0, "work": 0}
    assert not queue.active_paths


def stock(queue, count=30):
    """A queue deep enough to page through."""
    for number in range(count):
        add(queue, f"/media/File {number:02}.mkv")


def undone(queue):
    """A promotion and the undo that puts the rows back."""
    queue.restore(queue.move_top({("sweep", "/media/File 25.mkv")})[1])


#: Each queue operation and whether a page drawn before it describes another
#: queue. Reads and commands that name nothing leave the revision where it is.
CHANGES = [
    ("admission", lambda queue: add(queue, "/media/Arrived.mkv"), True),
    ("a claim", claim, True),
    ("a skip", lambda queue: queue.skip({("sweep", "/media/File 20.mkv")}), True),
    ("one file skipped", lambda queue: queue.skip_file("sweep", "/media/File 21.mkv"), True),
    ("a stop", lambda queue: queue.stop("sweep"), True),
    ("every run stopped", lambda queue: queue.stop_all(), True),
    ("a promotion", lambda queue: queue.move_top({("sweep", "/media/File 25.mkv")}), True),
    ("an undo", undone, True),
    ("a page", lambda queue: queue.snapshot(), False),
    ("a title's rows", lambda queue: folder(queue, "/media"), False),
    ("nothing to promote", lambda queue: queue.move_top({("sweep", "/media/Gone.mkv")}), False),
    ("a stale undo", lambda queue: queue.restore("no such token"), False),
    ("an unknown file", lambda queue: queue.skip_file("sweep", "/media/Gone.mkv"), False),
]


def join_pages(queue, change, limit=10):
    """Read the whole queue a page at a time, as the dialog joins them.

    ``change`` runs after the first page. The joined paths, or None where a
    later page described another queue and the reader has to start again.
    """
    first = queue.snapshot(limit=limit)
    rows = list(first["items"])
    for offset in range(limit, first["total"], limit):
        if offset == limit:
            change()
        page = queue.snapshot(offset=offset, limit=limit)
        if (page["epoch"], page["revision"]) != (first["epoch"], first["revision"]):
            return None
        rows.extend(page["items"])
    return [row["path"] for row in rows]


@pytest.mark.parametrize(
    ("change", "seen"), [pytest.param(what, seen, id=name) for name, what, seen in CHANGES]
)
def test_the_revision_moves_with_what_a_page_would_draw(queue, change, seen):
    stock(queue)
    revision = queue.revision
    change(queue)
    assert (queue.revision > revision) is seen


@pytest.mark.parametrize("change", [pytest.param(what, id=name) for name, what, _ in CHANGES])
def test_pages_join_only_where_they_describe_one_queue(queue, change):
    """An accepted answer is the queue as the first page found it, or nothing."""
    stock(queue)
    wanted = paths(queue)
    joined = join_pages(queue, lambda: change(queue))
    assert joined is None or joined == wanted


def test_a_claim_between_pages_would_lose_the_row_the_second_page_starts_at(queue):
    """101 files read 50 at a time, which is where the two pages were found."""
    stock(queue, 101)
    first = queue.snapshot(limit=50)
    claim(queue)
    second = queue.snapshot(offset=50, limit=50)
    joined = [row["path"] for row in first["items"] + second["items"]]
    assert "/media/File 50.mkv" not in joined
    assert (first["epoch"], first["revision"]) != (second["epoch"], second["revision"])


def test_a_probed_file_returning_for_its_rewrite_rejects_a_joined_read(queue):
    """It arrives before the block's tail, so the pages behind it have moved."""
    stock(queue)
    probe = queue.submit("sweep", "/media/Probed.mkv", "probe", lambda: None, priority=True)
    run_task(queue, claim(queue))
    assert join_pages(queue, lambda: queue.continue_file(probe.handle, lambda: None)) is None


def test_a_restart_keeps_the_numbers_but_not_the_epoch(queue):
    """The count starts again, so the number alone would join across a restart."""
    add(queue, "one")
    before = queue.capture()
    work.reset()
    lifecycle.open_run("sweep", runs.SWEEP)
    add(work.scheduler, "two")
    after = work.scheduler.capture()
    assert (after.revision, after.epoch != before.epoch) == (before.revision, True)


def test_asking_twice_does_not_invalidate_a_read(queue):
    """A repeat hides nothing, and a page in flight is still worth joining."""
    add(queue, "/media/File 00.mkv")
    add(queue, "/media/File 01.mkv", run="import")
    queue.skip({("sweep", "/media/File 00.mkv")})
    queue.stop("import")
    revision = queue.revision
    queue.skip({("sweep", "/media/File 00.mkv")})
    queue.skip_file("sweep", "/media/File 00.mkv")
    queue.stop("import")
    queue.stop_all()
    assert queue.revision == revision


def test_dispatch_does_not_spoil_an_undo(queue):
    """The token and the identities it captured decide it, not the revision."""
    for path in ("one", "two", "three"):
        add(queue, path)
    _, token = queue.move_top({("sweep", "two"), ("sweep", "three")})
    revision = queue.revision
    step(queue)
    add(queue, "four")
    assert paths(queue) == ["three", "one", "four"]
    assert queue.revision > revision
    assert queue.restore(token)
    assert paths(queue) == ["one", "three", "four"]


def test_an_undo_cannot_reach_a_later_admission_of_the_same_file(queue):
    """The rank belongs to the admission the promotion moved, not to the path."""
    add(queue, "one")
    add(queue, "two")
    _, token = queue.move_top({("sweep", "two")})
    step(queue)
    add(queue, "three")
    add(queue, "two")
    assert queue.restore(token)
    assert paths(queue) == ["one", "three", "two"]


def test_counts_and_the_preview_answer_without_walking_the_backlog(queue):
    """Every open tab polls these, and a first-night sweep is the backlog."""
    for number in range(200):
        add(queue, f"/shows/Episode {number:03}.mkv")
    add(queue, "/shows/probe.mkv", lane="probe")
    queue.pending = NoTraversal(queue.pending)
    assert (queue.count("work"), queue.count("probe")) == (200, 1)
    page = queue.snapshot(limit=3)
    assert page["total"] == 201
    assert [item["path"] for item in page["items"]] == [
        f"/shows/Episode {number:03}.mkv" for number in range(3)
    ]
    with queue.condition:
        view = queue.control_view()["sweep"]
    assert (view.queued, len(view.upcoming)) == (201, runs.UPCOMING)


def test_a_skipped_head_is_walked_past_once(queue):
    """A skip of the whole front leaves rows every later preview would step over."""
    for number in range(100):
        add(queue, f"/shows/Episode {number:03}.mkv")
    queue.skip({("sweep", f"/shows/Episode {number:03}.mkv") for number in range(90)})
    page = queue.snapshot(limit=2)
    assert page["total"] == 10
    assert [item["path"] for item in page["items"]] == [
        "/shows/Episode 090.mkv",
        "/shows/Episode 091.mkv",
    ]
    assert queue.views["sweep"].shown_from == 90, "the cursor is past them, not before them"
    # The run still counts and names them, since a worker has yet to release them.
    with queue.condition:
        view = queue.control_view()["sweep"]
    assert view.queued == 100
    assert [row.path for row in view.upcoming][:1] == ["/shows/Episode 000.mkv"]


def free(condition):
    """Whether a thread other than this one could take the condition now."""
    taken = []

    def attempt():
        if condition.acquire(blocking=False):
            taken.append(True)
            condition.release()

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()
    return bool(taken)


def test_search_and_title_positions_walk_the_rows_off_the_dispatch_lock(queue, monkeypatch):
    """Reading the whole queue is the one read that cannot be a count."""
    for number in range(50):
        add(queue, f"/shows/Episode {number:02}.mkv")
    watched: list[bool] = []
    stream = work.queue_view.stream

    def watch(captures):
        for row in stream(captures):
            watched.append(free(queue.condition))
            yield row

    monkeypatch.setattr(work.queue_view, "stream", watch)
    page = queue.snapshot("episode 1", offset=0, limit=5)
    assert (page["total"], page["matched"]) == (50, 10)
    assert [item["path"] for item in page["items"]] == [
        f"/shows/Episode 1{number}.mkv" for number in range(5)
    ]
    assert folder(queue, "/shows")[0]["position"] == 1
    assert watched and all(watched)


def test_a_claim_during_a_read_leaves_the_page_whole(queue, monkeypatch):
    """Nothing holds the rows still while they are read, so a worker taking
    one of them mid-walk must not cost the page a row or a position."""
    set_config(MAX_CONCURRENT_REWRITES=1)
    for number in range(6):
        add(queue, f"/shows/{number}.mkv")
    stream = work.queue_view.stream

    def watch(captures):
        for index, row in enumerate(stream(captures)):
            if index == 2:
                claim(queue)
            yield row

    monkeypatch.setattr(work.queue_view, "stream", watch)
    assert [row["position"] for row in folder(queue, "/shows")] == [1, 2, 3, 4, 5, 6]


def test_blocked_paths_and_reservations_are_parked_until_release(queue, monkeypatch):
    set_config(MAX_CONCURRENT_REWRITES=3, PROBE_WORKERS=3)
    add(queue, "shared")
    active = claim(queue)
    add(queue, "shared", "import", lane="probe")
    add(queue, "reserved")
    with pytest.raises(RuntimeError), queue.pause_selection({("sweep", "reserved")}):
        assert claim(queue) is None
        assert queue.parked == {
            "shared": {("import", "shared")},
            "reserved": {("sweep", "reserved")},
        }
        with monkeypatch.context() as patch:
            patch.setattr(queue, "pending", NoTraversal())
            for _ in range(10):
                assert claim(queue) is None
        raise RuntimeError("rollback")
    reserved = claim(queue)
    assert reserved.path == "reserved"
    run_task(queue, reserved)
    run_task(queue, active)
    shared = claim(queue)
    assert shared.run == "import"
    run_task(queue, shared)
    assert not queue.parked


@pytest.mark.parametrize(
    "action", ["submit", "resume", "stop", "stop_all", "skip", "skip_file", "settings"]
)
def test_dispatch_wakes_from_controls_without_polling(queue, monkeypatch, action):
    sleeping = threading.Event()
    original_wait = queue.condition.wait

    def waiting(timeout=None):
        assert timeout is None
        sleeping.set()
        return original_wait(timeout)

    monkeypatch.setattr(queue.condition, "wait", waiting)
    if action in {"resume", "stop", "stop_all", "skip", "skip_file"}:
        lifecycle.pause()
    if action == "settings":
        set_config(MAX_CONCURRENT_REWRITES=1)
        add(queue, "occupied")
        occupied = claim(queue)
    future = None if action == "submit" else add(queue, "waiting")
    queue.start()
    assert sleeping.wait(3)
    if action == "submit":
        future = add(queue, "waiting")
    elif action == "resume":
        lifecycle.resume()
    elif action == "stop":
        lifecycle.stop("sweep")
    elif action == "stop_all":
        lifecycle.stop_all()
    elif action == "skip":
        lifecycle.skip({("sweep", "waiting")})
    elif action == "skip_file":
        lifecycle.skip_file("sweep", "waiting")
    else:
        set_config(MAX_CONCURRENT_REWRITES=2)
        wake()
    future.result(timeout=3)
    if action == "settings":
        run_task(queue, occupied)


def test_indexes_stay_bounded_after_repeated_moves_and_draining(queue):
    for number in range(100):
        add(queue, str(number), lane="probe")
    for _ in range(100):
        _, token = queue.move_top({("sweep", "99")})
        assert sum(map(len, queue.candidates.values())) == 100
        assert queue.restore(token)
        assert sum(map(len, queue.candidates.values())) == 100
        assert len(queue.views["sweep"].block) == 100, "republished, not piled up"
    while task := claim(queue):
        run_task(queue, task)
        queue.complete_file(task.future.handle)
    assert not queue.pending and not queue.active and not queue.tasks
    assert not queue.parked and not queue.active_paths
    assert sum(map(len, queue.candidates.values())) == 0
    assert not queue.views and queue.queued == {"probe": 0, "work": 0}


def test_repeated_eligibility_changes_compact_without_waking_parked_paths(queue):
    set_config(MAX_CONCURRENT_REWRITES=2)
    add(queue, "shared")
    active = claim(queue)
    add(queue, "shared", "import")
    assert claim(queue) is None
    add(queue, "waiting")
    for _ in range(200):
        queue.skip_file("sweep", "waiting")
        assert sum(map(len, queue.candidates.values())) <= 2 * len(queue.entries) + 64
        assert queue.parked == {"shared": {("import", "shared")}}
    lifecycle.pause()
    step(queue)
    assert claim(queue) is None
    # A command for a run with no pending candidates is harmless.
    lifecycle.stop("sweep")
    lifecycle.resume()
    assert claim(queue) is None
    run_task(queue, active)
    step(queue)
    assert not queue.by_run and not queue.entries and not queue.parked


def test_draining_skipped_tasks_compacts_obsolete_normal_lane_entries(queue):
    for number in range(200):
        add(queue, str(number))
    lifecycle.stop("sweep")
    lifecycle.pause()
    while task := claim(queue):
        run_task(queue, task)
        assert sum(map(len, queue.candidates.values())) <= 2 * len(queue.entries) + 64
    assert not queue.by_run and not queue.entries
    lifecycle.resume()
    assert claim(queue) is None
    assert sum(map(len, queue.candidates.values())) == 0


@pytest.mark.parametrize("state", ["pending", "active", "awaiting_decision"])
def test_live_admission_rejects_duplicates_and_invalid_handoffs(queue, state):
    phase = add(queue, "same", lane="probe")
    if state != "pending":
        task = claim(queue)
        if state == "awaiting_decision":
            run_task(queue, task)
    with pytest.raises(ValueError, match="already queued"):
        add(queue, "same")
    assert len(queue.tasks) == 1
    if state != "awaiting_decision":
        with pytest.raises(ValueError, match="not awaiting"):
            queue.continue_file(phase.handle, lambda: None)
        assert not queue.complete_file(phase.handle)
        run_task(queue, claim(queue) if state == "pending" else task)
    assert queue.snapshot()["total"] == 0
    assert not queue.active_paths and not any(queue.occupied.values())
    assert queue.complete_file(phase.handle)
    assert not queue.complete_file(phase.handle)
    with pytest.raises(ValueError, match="not awaiting"):
        queue.continue_file(phase.handle, lambda: None)
    assert not queue.tasks


def test_stale_handles_and_undo_cannot_mutate_readmitted_file(queue):
    old = add(queue, "same", lane="probe")
    add(queue, "other")
    _, token = queue.move_top({("sweep", "other")})
    step(queue)
    step(queue)
    assert queue.complete_file(old.handle)
    new = add(queue, "same", lane="probe")
    assert new.handle.identity > old.handle.identity
    step(queue)
    rank = queue.tasks[("sweep", "same")].rank
    assert not queue.complete_file(old.handle)
    with pytest.raises(ValueError, match="not awaiting"):
        queue.continue_file(old.handle, lambda: None)
    assert queue.restore(token)
    assert queue.tasks[("sweep", "same")].rank == rank
    assert queue.complete_file(new.handle)


@pytest.mark.parametrize("lane", ["probe", "work"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_phase_failure_always_releases_admission_even_if_future_cancelled(
    queue, lane, cancelled
):
    def fail():
        raise RuntimeError("phase failed")

    phase = add(queue, "broken", lane=lane, call=fail)
    if cancelled:
        assert phase.cancel()
    step(queue)
    if cancelled:
        assert phase.cancelled()
    else:
        with pytest.raises(RuntimeError, match="phase failed"):
            phase.result()
    assert phase.settled.is_set()
    assert not queue.tasks and not queue.active_paths
    assert not any(queue.occupied.values())
    # Released once, so a drain neither hangs on it nor counts it twice.
    assert queue.drain(0)


def test_probe_callback_can_handoff_after_claim_release(queue):
    probe = add(queue, "same", lane="probe")
    continued = []

    def handoff(phase):
        assert not queue.condition._is_owned()
        assert not queue.active_paths
        continued.append(queue.continue_file(phase.handle, lambda: "rewritten", 12.5))

    probe.add_done_callback(handoff)
    step(queue)
    assert continued[0].handle == probe.handle
    assert queue.snapshot()["items"][0]["expected"] == 12.5
    step(queue)
    assert continued[0].result() == "rewritten"
    assert not queue.tasks


def test_group_rejects_missing_decisions_and_late_admission(queue):
    queue.start()
    with (
        pytest.raises(ValueError, match="awaiting a decision"),
        lifecycle.group("sweep") as submit,
    ):
        phase = submit("forgotten", "probe", lambda: None)
        phase.result(timeout=3)
    assert not queue.tasks
    with pytest.raises(ValueError, match="group is closed"):
        submit("late", "work", lambda: None)
    assert not queue.tasks


def test_group_failure_drains_prior_admissions_after_later_submission_fails(queue):
    queue.start()
    lifecycle.pause()
    with pytest.raises(ValueError, match="already queued"), lifecycle.group("sweep") as submit:
        first = submit("same", "probe", lambda: lifecycle.hold("sweep"))
        submit("same", "probe", lambda: None)
    assert first.result() is False
    assert first.settled.is_set()
    assert not queue.tasks and not queue.active_paths


def test_group_drains_cancelled_work_and_callbacks_before_exit(queue):
    callback_entered, release_callback = threading.Event(), threading.Event()
    draining, exited = threading.Event(), threading.Event()

    def callback(phase):
        callback_entered.set()
        assert release_callback.wait(3)

    def walking():
        with lifecycle.group("sweep") as submit:
            probe = submit("same", "probe", lambda: None)
            step(queue)
            rewrite = queue.continue_file(probe.handle, lambda: None)
            rewrite.add_done_callback(callback)
            cancelled = submit("cancelled", "work", lambda: None)
            assert cancelled.cancel()
            queue.start()
            draining.set()
        exited.set()
        assert cancelled.settled.is_set()

    with ThreadPoolExecutor() as pool:
        walking_done = pool.submit(walking)
        try:
            assert draining.wait(3)
            assert callback_entered.wait(3)
            assert not exited.is_set()
        finally:
            release_callback.set()
        walking_done.result(timeout=3)
    assert not queue.tasks


def test_a_drain_waits_for_the_callbacks_not_just_the_claim(queue):
    """The claim is released before the completion callback runs, so a drain
    that counts only queued work lets the process stop between a rewrite and
    the booking of its verdict."""
    booking, release = threading.Event(), threading.Event()

    def book():
        booking.set()
        assert release.wait(3)

    phase = queue.submit("sweep", "/one.mkv", "work", lambda: None, on_terminal=book)
    queue.start()
    assert booking.wait(3)

    assert not queue.tasks, "the admission is already gone"
    assert not queue.drain(0), "and the drain still has something to wait for"
    assert not phase.settled.is_set()

    release.set()
    assert queue.drain(3)
    assert phase.settled.is_set()


def test_a_drain_waits_for_the_callers_waiting_on_the_result(queue):
    """The other half of settlement: the sweep books its verdict in a done
    callback, which runs as the result is published."""
    booking, release = threading.Event(), threading.Event()

    def book(phase):
        booking.set()
        assert release.wait(3)

    phase = queue.submit("sweep", "/one.mkv", "work", lambda: None)
    phase.add_done_callback(book)
    queue.start()
    assert booking.wait(3)

    assert not queue.drain(0)
    release.set()
    assert queue.drain(3)


def test_a_drain_waits_for_a_decision_the_group_completes(queue):
    """Same boundary on the other route out: a probe the walk finished with is
    retired by hand, and its callback is the file's last accounting."""
    booking, release = threading.Event(), threading.Event()

    def book():
        booking.set()
        assert release.wait(3)

    probe = queue.submit("sweep", "same", "probe", lambda: None, on_terminal=book)
    step(queue)
    with ThreadPoolExecutor() as pool:
        completing = pool.submit(queue.complete_file, probe.handle)
        assert booking.wait(3)
        assert not queue.drain(0)
        release.set()
        assert completing.result(timeout=3)
    assert queue.drain(3)


def test_closed_group_rejects_continuation_without_changing_probe(queue):
    with pytest.raises(RuntimeError, match="booking"), lifecycle.group("sweep") as submit:
        probe = submit("same", "probe", lambda: None)
        step(queue)
        submit.closed = True
        with pytest.raises(ValueError, match="group is closed"):
            queue.continue_file(probe.handle, lambda: None)
        assert queue.tasks[probe.handle.key].state == "awaiting_decision"
        raise RuntimeError("booking")
    assert not queue.tasks


def test_cancelled_probe_still_runs_but_needs_no_decision(queue):
    reached = []
    phase = add(queue, "cancelled", lane="probe", call=lambda: reached.append(True))
    assert phase.cancel()
    step(queue)
    assert reached == [True]
    assert phase.settled.is_set()
    assert not queue.tasks and not queue.active_paths


@pytest.mark.parametrize("ending", ["work", "decision", "handoff", "probe_failure"])
def test_terminal_cleanup_runs_once_after_claim_release_and_outside_lock(queue, ending):
    cleaned = []

    def cleanup():
        with ThreadPoolExecutor() as readers:
            assert readers.submit(queue.snapshot).result(timeout=3)["total"] == 0
        assert not queue.tasks
        assert not queue.active_paths
        cleaned.append(True)

    def call():
        if ending == "probe_failure":
            raise RuntimeError("probe failed")
        return 42

    phase = queue.submit(
        "sweep",
        "file",
        "work" if ending == "work" else "probe",
        call,
        on_terminal=cleanup,
    )
    step(queue)
    if ending in {"decision", "handoff"}:
        assert cleaned == []
        if ending == "decision":
            assert queue.complete_file(phase.handle)
        else:
            phase = queue.continue_file(phase.handle, lambda: 42)
            step(queue)
    if ending == "probe_failure":
        with pytest.raises(RuntimeError, match="probe failed"):
            phase.result(timeout=3)
    else:
        assert phase.result(timeout=3) == 42
    assert not queue.complete_file(phase.handle)
    assert cleaned == [True]


@pytest.mark.parametrize("call_fails", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_terminal_cleanup_failure_still_settles_phase(queue, call_fails, cancelled):
    def call():
        if call_fails:
            raise RuntimeError("processing failed")

    def cleanup():
        raise RuntimeError("cleanup failed")

    phase = queue.submit("sweep", "file", "work", call, on_terminal=cleanup)
    if cancelled:
        assert phase.cancel()
    step(queue)
    assert not queue.tasks
    assert not queue.active_paths
    assert phase.settled.is_set()
    if cancelled:
        assert phase.cancelled()
    else:
        with pytest.raises(
            RuntimeError, match="processing failed" if call_fails else "cleanup failed"
        ):
            phase.result(timeout=3)


@pytest.mark.parametrize("transition", ["claim", "skip", "stop", "append", "reorder", "undo"])
def test_saved_queue_snapshot_survives_every_transition(queue, transition):
    for number in range(5):
        add(queue, f"/shows/{number}.mkv")
    if transition == "undo":
        _, undo = queue.move_top({("sweep", "/shows/4.mkv")})
    capture = queue.capture()
    before = capture.page("shows")
    positions = capture.for_folder("/shows")
    if transition == "claim":
        claim(queue)
    elif transition == "skip":
        queue.skip({("sweep", "/shows/3.mkv")})
    elif transition == "stop":
        queue.stop("sweep")
    elif transition == "append":
        add(queue, "/shows/5.mkv")
    elif transition == "reorder":
        queue.move_top({("sweep", "/shows/4.mkv")})
    else:
        assert queue.restore(undo)
    assert capture.page("shows") == before
    assert capture.for_folder("/shows") == positions
    assert queue.snapshot("shows") != before


@pytest.mark.parametrize("search", [False, True])
def test_page_counts_and_rows_agree_when_dispatch_changes_unread_rows(
    queue, monkeypatch, search
):
    for number in range(6):
        add(queue, f"/shows/{number}.mkv")
    reached, release = threading.Event(), threading.Event()
    stream = work.queue_view.stream

    def blocked(captures):
        for index, row in enumerate(stream(captures)):
            if index == 1:
                reached.set()
                assert release.wait(3)
            yield row

    monkeypatch.setattr(work.queue_view, "stream", blocked)
    with ThreadPoolExecutor() as pool:
        reading = pool.submit(queue.snapshot, "shows" if search else "")
        try:
            assert reached.wait(3)
            assert free(queue.condition)
            queue.skip({("sweep", "/shows/4.mkv")})
            queue.stop("sweep")
            claim(queue)
        finally:
            release.set()
        page = reading.result(timeout=3)
    assert page["total"] == page["matched"] == len(page["items"]) == 6
    assert [row["path"] for row in page["items"]] == [f"/shows/{i}.mkv" for i in range(6)]


def test_old_generations_are_collectable_after_reorder_skip_and_drain(queue):
    references = []
    for cycle in range(5):
        for number in range(100):
            add(queue, f"/shows/{cycle}/{number}.mkv")
        capture = queue.capture()
        references.extend(weakref.ref(entry) for entry in capture.captures[0].block)
        queue.move_top({("sweep", f"/shows/{cycle}/99.mkv")})
        queue.skip({("sweep", f"/shows/{cycle}/{number}.mkv") for number in range(100)})
        while queue.pending:
            step(queue)
        assert len(capture.page()["items"]) == 50
        del capture
        gc.collect()
        assert all(reference() is None for reference in references)


def test_activity_projection_does_not_copy_the_skipped_backlog(queue):
    class Unwalkable(set):
        def __iter__(self):
            pytest.fail("activity traversed the skipped backlog")

    for number in range(100):
        add(queue, f"/shows/{number}.mkv")
    queue.skip({("sweep", f"/shows/{number}.mkv") for number in range(100)})
    queue.controls["sweep"].skipped = Unwalkable(queue.controls["sweep"].skipped)
    with queue.condition:
        control = queue.control_view()["sweep"]
    assert len(control.upcoming) == runs.UPCOMING
    assert all(row.skipped for row in control.upcoming)
    assert control.skipped == frozenset()


def test_hurry_preserves_undo_and_only_promotes_eligible_rewrites(queue):
    for path in ["one", "two", "three", "reserved", "skipped"]:
        add(queue, path)
    add(queue, "probe", lane="probe")
    _, token = queue.move_top({("sweep", "two")})
    undo = queue.undo
    queue.skip({("sweep", "skipped")})
    with queue.pause_selection({("sweep", "reserved")}):
        assert queue.waiting_work("sweep") == {"one", "two", "three"}
        assert (
            queue.hurry(
                {("sweep", p) for p in ["three", "reserved", "skipped", "probe", "gone"]}
            )
            == 1
        )
        assert paths(queue)[:3] == ["three", "two", "one"]
        assert queue.undo is undo
    assert queue.restore(token)
    assert paths(queue)[:3] == ["one", "two", "three"]
    queue.set_paused(True)
    assert queue.waiting_work("sweep") == set()
    assert queue.hurry({("sweep", "three")}) == 0
    queue.set_paused(False)
    assert queue.hurry({("sweep", "three")}) == 1
    active = claim(queue)
    assert active.path == "three"
    assert queue.hurry({("sweep", "three")}) == 0
    queue.stop("sweep")
    assert queue.waiting_work("sweep") == set()
    assert queue.hurry({("sweep", "one")}) == 0
