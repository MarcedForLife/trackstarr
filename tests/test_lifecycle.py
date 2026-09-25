"""Durable pause coordination and scheduler dispatch state."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from conftest import (
    cache,
    claim,
    pending,
    publish_verdict,
    read_events,
    registered,
    run_task,
    set_config,
    step,
)
from trackstarr import config, lifecycle, notify, runs, sweep_cache, work

# Bound here, since conftest replaces the module's own attribute with a no-op
# to keep a settings save in an API test from bringing the pool up.
from trackstarr.executor import Cancel
from trackstarr.lifecycle import wake
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import Verdict, cache_key


@pytest.fixture(autouse=True)
def registry(clean_registry):
    yield


def flag_path() -> str:
    return os.path.join(config.STATE_DIR, lifecycle.PAUSED_FILE)


def test_pausing_holds_every_thread_that_asks_to_start():
    started = threading.Event()
    allowed = threading.Event()

    def work():
        started.set()
        if lifecycle.hold():
            allowed.set()

    lifecycle.pause("operator")
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert started.wait(2)
    # The gate is the whole mechanism: one flag, and every worker and every
    # sweep thread waits on it.
    assert not allowed.wait(0.2)

    lifecycle.resume("operator")
    assert allowed.wait(2)
    thread.join(timeout=2)


def test_a_pause_survives_a_restart():
    lifecycle.pause("operator")
    with open(flag_path()) as flag:
        assert json.load(flag)["paused"] is True

    # A fresh process: the flag is all it has.
    lifecycle.reset_paused()
    lifecycle.load_paused()
    assert lifecycle.paused()
    assert lifecycle.snapshot()["paused_by"] == "operator"


def test_resuming_clears_the_flag_a_restart_would_read():
    lifecycle.pause("operator")
    lifecycle.resume("operator")
    lifecycle.reset_paused()
    lifecycle.load_paused()
    assert not lifecycle.paused()


@pytest.mark.parametrize("content", ["not json at all", '["a list"]', '{"paused": false}'])
def test_a_flag_that_says_nothing_useful_reads_as_running(content):
    """Better to come up sweeping than stuck paused with nothing in the UI
    able to say why; a pause is cheap to make again."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(flag_path(), "w") as flag:
        flag.write(content)
    lifecycle.load_paused()
    assert not lifecycle.paused()
    assert not lifecycle.paused_on_disk()


def test_pausing_twice_changes_nothing_and_says_so():
    assert lifecycle.pause("operator") is True
    assert lifecycle.pause("operator") is False
    assert lifecycle.resume("operator") is True
    assert lifecycle.resume("operator") is False


def test_the_history_records_who_paused_and_resumed():
    lifecycle.pause("operator")
    lifecycle.resume("operator")
    lines = read_events()
    kinds = [(entry["event"], entry.get("by")) for entry in lines]
    assert kinds == [("paused", "operator"), ("resumed", "operator")]
    # Whole seconds, stamped just before the line itself.
    began = datetime.fromisoformat(lines[1]["paused_at"])
    assert 0 <= (datetime.fromisoformat(lines[0]["ts"]) - began).total_seconds() <= 1


def test_a_stopped_run_tells_its_threads_to_give_up():
    lifecycle.open_run("r#1", runs.SWEEP)
    assert lifecycle.hold("r#1") is True
    lifecycle.stop("r#1")
    assert lifecycle.hold("r#1") is False
    # A run nobody registered cannot be stopped, which is how the API answers
    # a page acting on a sweep that has since finished.
    assert lifecycle.stop("r#2") is False


def test_a_stop_wakes_a_held_thread_rather_than_being_polled_for(monkeypatch):
    """The stop arrives while the thread is already asleep on the pause: it must
    not go on to process a file once resumed. Both are the scheduler's condition,
    so the stop wakes the thread instead of being noticed a tick later."""
    lifecycle.open_run("r#1", runs.SWEEP)
    lifecycle.pause()
    sleeping = threading.Event()
    original_wait = work.scheduler.condition.wait

    def waiting(timeout=None):
        assert timeout is None, "the hold polled instead of waiting"
        sleeping.set()
        return original_wait(timeout)

    monkeypatch.setattr(work.scheduler.condition, "wait", waiting)
    verdict: list[bool] = []
    thread = threading.Thread(target=lambda: verdict.append(lifecycle.hold("r#1")), daemon=True)
    thread.start()
    assert sleeping.wait(3)
    lifecycle.stop("r#1")
    thread.join(timeout=5)
    assert verdict == [False]


def test_a_pause_that_cannot_be_written_still_pauses(monkeypatch, caplog):
    """The flag is how a pause survives a restart; losing it must not lose the
    pause itself, which is the thing keeping the machine quiet right now."""

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(lifecycle, "write_json", refuse)
    assert lifecycle.pause("operator") is True
    assert lifecycle.paused()
    assert "could not persist" in caplog.text


def test_a_run_read_carries_the_scheduler_stop_and_skip_state():
    """One owner: the Stop and Skip a page draws come from the queue that
    decides them, not a second copy kept beside it."""
    lifecycle.open_run("walk", runs.SWEEP)
    work.scheduler.submit("walk", "/media/one.mkv", "work", lambda: None, 90.0)
    work.scheduler.submit("walk", "/media/two.mkv", "work", lambda: None, 30.0)
    assert lifecycle.skip_file("walk", "/media/two.mkv") == ("waiting", 0)

    (run,) = lifecycle.snapshot()["runs"]
    assert run["stopping"] is False
    assert [(file["path"], file["skipped"]) for file in run["upcoming"]] == [
        ("/media/one.mkv", False),
        ("/media/two.mkv", True),
    ]
    assert lifecycle.stop("walk")
    assert lifecycle.snapshot()["runs"][0]["stopping"] is True


def test_a_skip_racing_the_last_phase_never_marks_a_released_file():
    """The answer comes off the live admission, so a file the worker has just
    let go is nothing to skip rather than a skip nothing will ever read."""
    lifecycle.open_run("walk", runs.SWEEP)
    working, release = threading.Event(), threading.Event()

    def call():
        working.set()
        assert release.wait(3)

    work.scheduler.submit("walk", "/media/one.mkv", "work", call)
    task = claim(work.scheduler)
    with ThreadPoolExecutor() as workers:
        executing = workers.submit(run_task, work.scheduler, task)
        try:
            assert working.wait(3)
            assert lifecycle.skip_file("walk", "/media/one.mkv") == ("active", 0)
        finally:
            release.set()
        executing.result(timeout=3)
    assert lifecycle.skip_file("walk", "/media/one.mkv") == ("", 0)
    assert not work.scheduler.controls["walk"].skipped


@pytest.mark.parametrize(
    ("path", "inside"),
    [
        ("/data/media/movies", True),
        ("/data/media/movies/Dune (2024)/./Dune (2024).mkv", True),
        ("/data/media/movies/Arrival (2016)/../Dune (2024)", True),
        ("//data/media/movies/Dune (2024)", True),
        ("/data/media/movies2/Dune (2024)", False),
        ("/data/media/movies/../secrets.mkv", False),
        ("movies/Dune (2024)", False),
    ],
)
def test_a_media_root_is_matched_by_component_on_the_canonical_path(path, inside):
    """The path authorized has to be the path stored, or a pause passes the
    root check and then matches nothing the sweep walked."""
    set_config(MEDIA_DIRS=["/data/media/movies/"])
    assert lifecycle.under_media(path) is inside


def test_a_paused_delivery_still_counts_as_waiting():
    """Paused, nothing is running and four files are owed; a queue counting
    only what moves would read nothing."""
    lifecycle.open_run("i#1", runs.IMPORT, filling=True)
    for _ in range(4):
        runs.add_file("i#1")
    lifecycle.pause("someone")
    assert runs.workload() == (4, 0)


@pytest.mark.parametrize("first_on", [True, False])
def test_racing_commands_persist_in_order_without_blocking_dispatch(monkeypatch, first_on):
    if not first_on:
        lifecycle.pause("initial")
    writing, release, second_waiting = (threading.Event() for _ in range(3))
    original_write = lifecycle.write_json
    original_lock = lifecycle._pause_writer
    writes = []

    def blocked_write(path, record):
        if not writes:
            writing.set()
            assert release.wait(3)
        writes.append(record)
        original_write(path, record)

    # Signal the second writer reaching the lock before releasing the first.
    class Writer:
        def __enter__(self):
            if writing.is_set():
                second_waiting.set()
            original_lock.acquire()

        def __exit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(lifecycle, "_pause_writer", Writer())
    monkeypatch.setattr(lifecycle, "write_json", blocked_write)
    first, second = (
        (lifecycle.pause, lifecycle.resume) if first_on else (lifecycle.resume, lifecycle.pause)
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        a = pool.submit(first, "first")
        try:
            assert writing.wait(3)
            b = pool.submit(second, "second")
            assert second_waiting.wait(3)
            snapshot = pool.submit(lifecycle.snapshot).result(timeout=3)
            assert snapshot["paused"] is first_on
            assert snapshot["paused_by"] == ("first" if first_on else "")
            # Both scheduler commands and reporting remain available during
            # persistence; only another service-pause writer waits.
            assert pool.submit(work.scheduler.snapshot).result(timeout=3)["total"] == 0
            assert pool.submit(runs.snapshot).result(timeout=3)["runs"] == []
        finally:
            release.set()
        assert a.result(timeout=3)
        assert b.result(timeout=3)
    assert [record["paused"] for record in writes] == [first_on, not first_on]
    with open(flag_path()) as flag:
        persisted = json.load(flag)
    snapshot = lifecycle.snapshot()
    assert persisted == {
        "paused": snapshot["paused"],
        "by": snapshot["paused_by"],
        "at": snapshot["paused_at"],
    }
    assert [entry["event"] for entry in read_events()][-2:] == (
        ["paused", "resumed"] if first_on else ["resumed", "paused"]
    )


def test_a_repeated_pause_keeps_the_original_actor_and_timestamp():
    lifecycle.pause("first")
    original = lifecycle.snapshot()
    assert not lifecycle.pause("second")
    assert lifecycle.snapshot() == original
    with open(flag_path()) as flag:
        assert json.load(flag) == {"paused": True, "by": "first", "at": original["paused_at"]}


def test_loading_a_legacy_pause_without_metadata(caplog):
    os.makedirs(config.STATE_DIR)
    with open(flag_path(), "w") as flag:
        json.dump({"paused": True}, flag)
    lifecycle.load_paused()
    snapshot = lifecycle.snapshot()
    assert snapshot["paused"]
    assert snapshot["paused_by"] == snapshot["paused_at"] == ""
    assert "an earlier run" in caplog.text


def test_import_count_is_installed_before_dispatch_and_announced_after_unlock(monkeypatch):
    counting, release, claiming = (threading.Event() for _ in range(3))
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    original_add = runs.add_file
    original_publish = notify.publish
    seen = []

    def add(run):
        counting.set()
        assert release.wait(3)
        original_add(run)

    def dispatch_one():
        claiming.set()
        with work.scheduler.condition:
            task = claim(work.scheduler)
            seen.append(record.total)
        run_task(work.scheduler, task)

    def publish(topic):
        # A notification consumer can read scheduler state from another thread.
        # Doing this while admission still owns the condition would deadlock.
        with ThreadPoolExecutor() as readers:
            readers.submit(work.scheduler.snapshot).result(timeout=3)
        original_publish(topic)

    monkeypatch.setattr(runs, "add_file", add)
    monkeypatch.setattr(notify, "publish", publish)
    with ThreadPoolExecutor() as workers:
        admission = workers.submit(lifecycle.submit_import, "delivery", "/file", lambda: 42)
        try:
            assert counting.wait(3)
            dispatch = workers.submit(dispatch_one)
            assert claiming.wait(3)
            assert not dispatch.done()
        finally:
            release.set()
        phase = admission.result(timeout=3)
        dispatch.result(timeout=3)
    assert phase.result(timeout=3) == 42
    assert seen == [1]


@pytest.mark.parametrize("dropped", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_accounted_import_stays_visible_until_terminal_release(dropped, cancelled):
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    accounted, release = threading.Event(), threading.Event()

    def call():
        with lifecycle.import_result("delivery", "/file") as result:
            result.dropped = dropped
        accounted.set()
        assert release.wait(3)

    phase = lifecycle.submit_import("delivery", "/file", call)
    if cancelled:
        assert phase.cancel()
    lifecycle.seal("delivery")
    task = claim(work.scheduler)
    with ThreadPoolExecutor() as workers:
        executing = workers.submit(run_task, work.scheduler, task)
        try:
            assert accounted.wait(3)
            assert registered("delivery")
            assert work.scheduler.controls["delivery"].live == 1
            lifecycle.seal("delivery")
            assert registered("delivery")
        finally:
            release.set()
        executing.result(timeout=3)
    assert phase.settled.is_set()
    assert not registered("delivery")
    assert "delivery" not in work.scheduler.controls
    replacement = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    assert replacement is not record
    assert not work.scheduler.complete_file(phase.handle)
    assert registered("delivery")


@pytest.mark.parametrize("handoff", [False, True])
def test_closing_run_retains_probe_decision_and_rejects_new_admission(handoff):
    lifecycle.open_run("walk", runs.SWEEP)
    phase = work.scheduler.submit("walk", "/file", "probe", lambda: 42)
    task = claim(work.scheduler)
    run_task(work.scheduler, task)
    assert phase.result() == 42
    lifecycle.close_run("walk")
    assert registered("walk")
    with pytest.raises(ValueError, match="not open for admission"):
        work.scheduler.submit("walk", "/another", "probe", lambda: None)
    with pytest.raises(ValueError, match="run is closing"):
        lifecycle.open_run("walk", runs.SWEEP)
    if handoff:
        phase = work.scheduler.continue_file(phase.handle, lambda: 7)
        task = claim(work.scheduler)
        run_task(work.scheduler, task)
        assert phase.result() == 7
    else:
        assert work.scheduler.complete_file(phase.handle)
    assert not registered("walk")
    assert "walk" not in work.scheduler.controls
    lifecycle.close_run("walk")


def test_terminal_without_accounting_waits_for_booking():
    lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    phase = lifecycle.submit_import("delivery", "/file", lambda: None)
    lifecycle.seal("delivery")
    step(work.scheduler)
    assert phase.done()
    assert registered("delivery")
    lifecycle.tally("delivery", "conform")
    assert not registered("delivery")


def test_closed_scheduler_and_unknown_run_reject_before_counting():
    with pytest.raises(ValueError, match="not open for admission"):
        lifecycle.submit_import("unknown", "/file", lambda: None)
    assert not work.scheduler.tasks
    assert not runs.retire("unknown")
    work.scheduler.shutdown()
    with pytest.raises(ValueError, match="scheduler is closed"):
        lifecycle.open_run("delivery", runs.IMPORT)
    assert runs.snapshot()["runs"] == []


@pytest.mark.parametrize("command", [lifecycle.seal, lifecycle.close_run])
def test_admission_and_run_end_are_serialized(monkeypatch, command):
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    counting, release, ending = (threading.Event() for _ in range(3))
    original_add = runs.add_file

    def add(run):
        counting.set()
        assert release.wait(3)
        original_add(run)

    def end():
        ending.set()
        command("delivery")

    monkeypatch.setattr(runs, "add_file", add)
    with ThreadPoolExecutor() as workers:
        admission = workers.submit(lifecycle.submit_import, "delivery", "/file", lambda: None)
        try:
            assert counting.wait(3)
            closing = workers.submit(end)
            assert ending.wait(3)
            assert not closing.done()
        finally:
            release.set()
        phase = admission.result(timeout=3)
        closing.result(timeout=3)
    assert record.total == 1
    assert registered("delivery")
    step(work.scheduler)
    assert phase.done()
    lifecycle.tally("delivery", "conform")
    assert not registered("delivery")


def test_retirement_notifications_run_after_unlock(monkeypatch):
    lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    observed = []

    def publish(topic):
        with ThreadPoolExecutor() as readers:
            observed.append(readers.submit(lifecycle.snapshot).result(timeout=3)["runs"])

    monkeypatch.setattr(notify, "publish", publish)
    phase = lifecycle.submit_import("delivery", "/file", lambda: lifecycle.drop("delivery"))
    lifecycle.seal("delivery")
    step(work.scheduler)
    phase.result(timeout=3)
    assert observed[-1] == []


def test_startup_restores_the_pause_before_dispatch_can_claim(monkeypatch):
    """Order is the whole point: a worker that claims a file between the two
    would rewrite it despite the pause the last process left behind."""
    lifecycle.pause("operator")
    lifecycle.reset_paused()
    claimed = []
    original_start = work.scheduler.start

    def start():
        claimed.append(work.scheduler.paused)
        original_start()

    monkeypatch.setattr(work.scheduler, "start", start)
    lifecycle.startup()
    assert claimed == [True]
    assert lifecycle.paused()


def test_a_settings_save_wakes_dispatch(monkeypatch):
    """Budgets are read for every claim, so a raised one only needs the wake."""
    called = []
    monkeypatch.setattr(work.scheduler, "start", lambda: called.append(True))
    wake()
    assert called == [True]


def test_shutdown_stops_producers_drains_the_queue_then_stops_dispatch(monkeypatch):
    """`docker stop` arrives mid-rewrite: the encode it interrupts would be an
    hour thrown away, while the files behind it are not worth waiting for."""
    lifecycle.open_run("sweep", runs.SWEEP)
    holding = threading.Event()
    release = threading.Event()

    def rewriting():
        holding.set()
        assert release.wait(5)

    closed = threading.Event()
    close = lifecycle._producers.close

    def closing():
        close()
        closed.set()

    monkeypatch.setattr(lifecycle._producers, "close", closing)
    stopping = []
    work.scheduler.submit("sweep", "/one.mkv", "work", rewriting)
    work.scheduler.submit(
        "sweep", "/two.mkv", "work", lambda: stopping.append(work.scheduler.stopping("sweep"))
    )
    work.scheduler.start()
    assert holding.wait(5)
    with ThreadPoolExecutor() as stopper:
        stopped = stopper.submit(lifecycle.shutdown, 5)
        try:
            assert closed.wait(3)
            assert not lifecycle.producing()
            assert not stopped.done()
        finally:
            release.set()
        assert stopped.result(timeout=5)
    # The queued file was not waited out: it ran to its own early exit.
    assert stopping == [True]
    assert work.scheduler.halted
    with pytest.raises(ValueError, match="scheduler is closed"):
        lifecycle.open_run("later", runs.SWEEP)


def test_shutdown_gives_up_on_work_that_never_drains():
    """A worker wedged on a network mount must not hold the process open."""
    lifecycle.open_run("sweep", runs.SWEEP)
    work.scheduler.submit("sweep", "/stuck.mkv", "work", lambda: None)
    assert lifecycle.shutdown(0) is False
    assert work.scheduler.halted


def test_shutdown_waits_for_a_producer_that_admitted_nothing():
    """A sweep between its last file and its cache write owns no task at all.
    Stopping there loses the verdicts of everything it looked at."""
    writing, release = threading.Event(), threading.Event()

    def walking():
        with lifecycle.producer() as allowed:
            assert allowed
            writing.set()
            assert release.wait(5)

    with ThreadPoolExecutor() as pool:
        walk = pool.submit(walking)
        assert writing.wait(3)
        stopping = pool.submit(lifecycle.shutdown, 5)
        assert not stopping.done()
        release.set()
        assert stopping.result(timeout=5)
        walk.result(timeout=5)


def test_shutdown_gives_up_on_a_producer_that_never_finishes(caplog):
    """The honest answer, since the caller logs it and the process stops
    anyway: a lease that outlives the deadline is work left behind."""
    with lifecycle.producer() as allowed:
        assert allowed
        assert lifecycle.shutdown(0) is False
    assert "1 producer(s) still going" in caplog.text


def test_shutdown_writes_the_verdicts_still_inside_their_window(tmp_path):
    """`docker stop` lands in the middle of an import burst often enough. The
    drain is the last thing that could write them."""
    episode = tmp_path / "Show S01E01.mkv"
    episode.write_bytes(b"x" * 10)
    publish_verdict(
        str(episode),
        cache_key(str(episode), "eng"),
        Verdict(Status.CONFORM),
        Policy.from_config().fingerprint(),
    )

    assert lifecycle.shutdown(0) is True
    assert str(episode) in json.loads(Path(sweep_cache.cache_path()).read_text())["files"]


def test_a_producer_arriving_during_shutdown_does_not_start():
    """The cue to do nothing at all, rather than to open a run the drain has
    already stopped waiting for."""
    assert lifecycle.shutdown(0) is True
    with lifecycle.producer() as allowed:
        assert not allowed


def test_reset_quiesces_before_handing_back_fresh_state():
    """An old worker that outlived its scheduler would book its verdict against
    the next one's registry."""
    lifecycle.open_run("sweep", runs.SWEEP)
    work.scheduler.submit("sweep", "/one.mkv", "work", lambda: None)
    lifecycle.pause("operator")
    stale = work.scheduler

    with pytest.raises(RuntimeError, match="cannot reset"):
        lifecycle.reset()
    assert work.scheduler is stale
    # The synthetic pending task has no worker; settle it before retrying.
    step(stale)
    lifecycle.reset()

    assert stale.halted
    assert work.scheduler is not stale
    assert not work.scheduler.tasks
    assert not lifecycle.paused()
    assert lifecycle.snapshot()["runs"] == []
    assert lifecycle.producing()


def test_a_run_names_its_waiting_files_in_the_order_the_queue_will_reach_them():
    """One owner: what a run says is waiting is the queue's own order, so a
    file moved to the top of the queue is at the top of the run as well."""
    lifecycle.open_run("sweep", runs.SWEEP)
    work.scheduler.submit("sweep", "/first.mkv", "work", lambda: None, 90.0)
    work.scheduler.submit("sweep", "/second.mkv", "work", lambda: None, 30.0)
    work.scheduler.move_top({("sweep", "/second.mkv")})

    (run,) = lifecycle.snapshot()["runs"]
    assert run["queued"] == 2
    assert [row["path"] for row in run["upcoming"]] == ["/second.mkv", "/first.mkv"]
    assert [row["expected"] for row in run["upcoming"]] == [30.0, 90.0]


def test_a_claimed_file_waits_until_a_thread_opens_it():
    """A verdict answered from the cache claims a slot and never opens the
    file, so the row has to survive the claim and go on the thread taking it."""
    lifecycle.open_run("sweep", runs.SWEEP)
    work.scheduler.submit("sweep", "/film.mkv", "work", lambda: None, 90.0)
    task = claim(work.scheduler)

    (run,) = lifecycle.snapshot()["runs"]
    assert [row["path"] for row in run["upcoming"]] == ["/film.mkv"]

    runs.begin("sweep", "/film.mkv", task.expected)
    (run,) = lifecycle.snapshot()["runs"]
    assert run["queued"] == 0
    assert [row["path"] for row in run["active"]] == ["/film.mkv"]
    # The estimate travelled with the task, rather than being popped out of a
    # queue the registry kept of its own.
    assert run["rewrite_seconds"] >= 89


def test_a_file_between_its_probe_and_its_rewrite_is_on_no_queue():
    """It has no worker and no slot, and the walk has yet to say whether there
    is a rewrite to come. Counting it would promise work nobody has chosen."""
    lifecycle.open_run("sweep", runs.SWEEP)
    phase = work.scheduler.submit("sweep", "/film.mkv", "probe", lambda: None)
    step(work.scheduler)

    (run,) = lifecycle.snapshot()["runs"]
    assert (run["queued"], run["upcoming"], run["active"]) == (0, [], [])

    work.scheduler.continue_file(phase.handle, lambda: None, 30.0)
    (run,) = lifecycle.snapshot()["runs"]
    assert [row["path"] for row in run["upcoming"]] == ["/film.mkv"]


def test_skipping_an_active_file_marks_that_phase_and_nothing_else():
    """The switch comes back with the answer, so the signal lands on the phase
    the queue chose even where the same file is claimed again behind it."""
    lifecycle.open_run("walk", runs.SWEEP)
    work.scheduler.submit("walk", "/media/one.mkv", "work", lambda: None)
    work.scheduler.submit("walk", "/media/two.mkv", "work", lambda: None)
    claimed = claim(work.scheduler)
    waiting = work.scheduler.tasks["walk", "/media/two.mkv"]

    assert lifecycle.skip_file("walk", "/media/one.mkv") == ("active", 0)
    assert claimed.cancel.asked.is_set()

    # A file no worker has is stopped by its own early exit, so there is
    # nothing to signal and nothing to mark.
    assert lifecycle.skip_file("walk", "/media/two.mkv") == ("waiting", 0)
    assert not waiting.cancel.asked.is_set()


def test_a_skip_leaves_one_line_saying_where_the_file_was_and_what_it_would_have_done():
    """The worker gives the file up without a word, so this line carries the lot."""
    # Two rewrite slots, so a second file can be claimed and left waiting on
    # the lock the first one holds.
    set_config(MAX_CONCURRENT_REWRITES=2)
    cache(
        ("/media/one.mkv", pending()),
        ("/media/held.mkv", pending()),
        ("/media/two.mkv", pending()),
    )
    lifecycle.open_run("walk", runs.SWEEP)
    work.scheduler.submit("walk", "/media/one.mkv", "work", lambda: None)
    work.scheduler.submit("walk", "/media/held.mkv", "work", lambda: None)
    work.scheduler.submit("walk", "/media/two.mkv", "work", lambda: None)
    claim(work.scheduler)
    claim(work.scheduler)
    runs.begin("walk", "/media/one.mkv")
    runs.stage("walk", "/media/one.mkv", runs.ENCODING, 200.0)
    runs.progress("walk", "/media/one.mkv", 86.0, 4.0)
    runs.begin("walk", "/media/held.mkv")
    runs.stage("walk", "/media/held.mkv", runs.WAITING)

    assert lifecycle.skip_file("walk", "/media/one.mkv", by="admin") == ("active", 0)
    assert lifecycle.skip_file("walk", "/media/held.mkv", by="admin") == ("active", 0)
    assert lifecycle.skip({("walk", "/media/two.mkv")}, by="admin").changed == 1

    mid_encode, for_a_slot, waiting = read_events()
    assert mid_encode["event"] == for_a_slot["event"] == waiting["event"] == "skipped"
    assert (mid_encode["where"], mid_encode["by"]) == ("active", "admin")
    assert mid_encode["detail"] == "stopped 43% into the rewrite, nothing written"
    assert mid_encode["seconds"] >= 0
    assert mid_encode["reasons"] == ["add 2.0 downmix from stream 1 (6ch eng)"]
    assert (mid_encode["rules"], mid_encode["adds"]) == (["downmix"], ["2.0"])
    assert for_a_slot["where"] == "active"
    assert for_a_slot["detail"] == "stopped while it waited for a rewrite slot"
    assert waiting["where"] == "waiting"
    assert waiting["detail"] == "taken off the run before a worker reached it"
    assert "seconds" not in waiting
    assert waiting["adds"] == ["2.0"]


def test_activity_captures_reporting_under_scheduler_lock_and_formats_after_release(
    monkeypatch,
):
    lifecycle.open_run("same", runs.SWEEP, label="old")
    work.scheduler.submit("same", "/old.mkv", "work", lambda: None, counted=True)
    original_capture = runs.capture
    original_format = runs.Capture.as_json

    def lock_available(lock):
        with ThreadPoolExecutor() as pool:

            def attempt():
                acquired = lock.acquire(blocking=False)
                if acquired:
                    lock.release()
                return acquired

            return pool.submit(attempt).result(timeout=3)

    def capture():
        assert not lock_available(work.scheduler.condition)
        return original_capture()

    def format_capture(self, controls):
        assert lock_available(work.scheduler.condition)
        assert lock_available(runs._lock)
        # A newly opened run with the same id must not inherit old telemetry
        # or be paired with the controls captured before it existed.
        with ThreadPoolExecutor() as pool:

            def reuse():
                lifecycle.stop("same")
                step(work.scheduler)
                lifecycle.close_run("same")
                lifecycle.open_run("same", runs.IMPORT, instance_id="new")

            pool.submit(reuse).result(timeout=3)
        return original_format(self, controls)

    monkeypatch.setattr(runs, "capture", capture)
    monkeypatch.setattr(runs.Capture, "as_json", format_capture)
    snapshot = lifecycle.snapshot()
    (run,) = snapshot["runs"]
    assert (run["label"], run["stopping"]) == ("old", False)
    assert snapshot["queue"] == run["queued"] == 1
    assert snapshot["working"] == 0
    assert snapshot["queue_preview"][0]["path"] == run["upcoming"][0]["path"] == "/old.mkv"


def queue_film(*names, run="sweep", lane="work"):
    """Queue files under /media/Film, as a walk that found them would."""
    return [
        work.scheduler.submit(run, f"/media/Film/{name}.mkv", lane, lambda: None)
        for name in names
    ]


def test_a_claim_cannot_land_between_the_reads_a_title_page_joins(monkeypatch):
    """Held files and queued files used to be two reads, and a claim between
    them left the file out of both answers."""
    lifecycle.open_run("sweep", runs.SWEEP)
    queue_film("one")
    reading, release = threading.Event(), threading.Event()
    original_capture = runs.capture

    def capture():
        reading.set()
        assert release.wait(3)
        return original_capture()

    monkeypatch.setattr(runs, "capture", capture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answering = pool.submit(lifecycle.title_work, ["/media/Film"])
        try:
            assert reading.wait(3)
            claiming = pool.submit(claim, work.scheduler)
            # Where the old pair of reads let a worker in. The condition is
            # held across both, so the claim waits for the whole capture.
            assert not work.scheduler.condition.acquire(blocking=False)
        finally:
            release.set()
        answered = answering.result(timeout=3)
        task = claiming.result(timeout=3)
    assert [row["path"] for row in answered["queued"]] == ["/media/Film/one.mkv"]
    assert answered["active"] == []
    assert task.path == "/media/Film/one.mkv"


def test_a_title_is_filtered_and_formatted_off_the_dispatch_lock(monkeypatch):
    reached, release = threading.Event(), threading.Event()
    original_project = runs.Capture.active_under

    def project(self, folder, controls):
        reached.set()
        assert release.wait(3)
        return original_project(self, folder, controls)

    lifecycle.open_run("sweep", runs.SWEEP)
    queue_film("one")
    monkeypatch.setattr(runs.Capture, "active_under", project)
    with ThreadPoolExecutor() as pool:
        answering = pool.submit(lifecycle.title_work, ["/media/Film"])
        assert reached.wait(3)
        task = claim(work.scheduler)
        release.set()
        answered = answering.result(timeout=3)
    assert task.path == "/media/Film/one.mkv"
    # Dispatch went on during the projection, and the answer still describes
    # the queue as it stood at the capture.
    assert [row["path"] for row in answered["queued"]] == ["/media/Film/one.mkv"]


def test_a_file_between_its_phases_is_on_neither_of_a_titles_lists():
    """No worker holds it and the walk has not said whether a rewrite follows.
    Naming it active would invent a thread that has it."""
    lifecycle.open_run("sweep", runs.SWEEP)
    (phase,) = queue_film("one", lane="probe")
    step(work.scheduler)

    answered = lifecycle.title_work(["/media/Film"])
    assert (answered["queued"], answered["active"]) == ([], [])

    work.scheduler.continue_file(phase.handle, lambda: None, 30.0)
    assert [row["path"] for row in lifecycle.title_work(["/media/Film"])["queued"]] == [
        "/media/Film/one.mkv"
    ]


def test_a_title_names_the_held_files_under_it_and_no_sibling_folder():
    """Whole components: a folder starting with the same letters is another
    title, and the folder itself is one of a series' own files."""
    lifecycle.open_run("sweep", runs.SWEEP)
    for path in ("/media/Film/one.mkv", "/media/Film Extra/two.mkv", "/media/Film"):
        runs.begin("sweep", path)
    assert [row["path"] for row in lifecycle.title_work(["/media/Film"])["active"]] == [
        "/media/Film/one.mkv",
        "/media/Film",
    ]


@pytest.mark.parametrize("command", ["stop", "skip"])
def test_a_stopped_or_skipped_title_keeps_the_file_its_worker_still_holds(command):
    """Neither is a kill: the rewrite in flight finishes, so the page keeps a
    row for it while its waiting files leave."""
    lifecycle.open_run("sweep", runs.SWEEP)
    queue_film("one", "two")
    task = claim(work.scheduler)
    runs.begin("sweep", task.path)
    if command == "stop":
        lifecycle.stop("sweep")
    else:
        lifecycle.skip_file("sweep", "/media/Film/one.mkv")
        lifecycle.skip({("sweep", "/media/Film/two.mkv")})

    answered = lifecycle.title_work(["/media/Film"])
    assert answered["queued"] == []
    (held,) = answered["active"]
    assert (held["run"], held["path"]) == ("sweep", "/media/Film/one.mkv")
    assert (held["stopping"], held["skipped"]) == (command == "stop", command == "skip")


def test_a_title_held_in_two_folders_reads_as_one_queue():
    """Rows from both folders merge in the queue's own order, whichever order
    the folders were named in."""
    lifecycle.open_run("sweep", runs.SWEEP)
    for path in ("/media/Film/one.mkv", "/media/Other/x.mkv", "/media4k/Film/one.mkv"):
        work.scheduler.submit("sweep", path, "work", lambda: None)
    runs.begin("sweep", "/media4k/Film/two.mkv")
    answered = lifecycle.title_work(["/media4k/Film", "/media/Film"])
    assert [(row["path"], row["position"]) for row in answered["queued"]] == [
        ("/media/Film/one.mkv", 1),
        ("/media4k/Film/one.mkv", 3),
    ]
    assert [row["path"] for row in answered["active"]] == ["/media4k/Film/two.mkv"]


def test_an_activity_capture_does_not_follow_later_dispatch():
    lifecycle.open_run("sweep", runs.SWEEP)
    queue_film("one", "two")
    captured = lifecycle.activity()
    before = captured.for_folders(["/media/Film"])

    step(work.scheduler)
    queue_film("three")
    lifecycle.skip({("sweep", "/media/Film/two.mkv")})

    assert captured.for_folders(["/media/Film"]) == before
    assert captured.overview() == captured.overview()
    assert [row["path"] for row in lifecycle.title_work(["/media/Film"])["queued"]] == [
        "/media/Film/three.mkv"
    ]


def test_telemetry_capture_does_not_follow_progress_or_final_booking():
    lifecycle.open_run("sweep", runs.SWEEP)
    runs.begin("sweep", "/active.mkv")
    runs.begin("sweep", "/done.mkv")
    runs.finish("sweep", "/done.mkv")
    capture = runs.capture()
    before = capture.as_json({})
    runs.progress("sweep", "/active.mkv", 30, 2)
    lifecycle.tally("sweep", "modified", "/done.mkv")
    assert capture.as_json({}) == before


@pytest.mark.parametrize("failure", [False, True])
def test_launch_holds_a_lease_before_the_worker_starts(monkeypatch, failure):
    queued = []

    class Thread:
        def __init__(self, *, target, daemon, name):
            queued.append(target)

        def start(self):
            if failure:
                raise RuntimeError("cannot start")

    monkeypatch.setattr(lifecycle.threading, "Thread", Thread)
    called = []
    if failure:
        with pytest.raises(RuntimeError, match="cannot start"):
            lifecycle.launch(lambda: None, name="test")
        assert lifecycle.shutdown(0)
    else:
        assert lifecycle.launch(called.append, (42,), name="test")
        assert not lifecycle.shutdown(0)
        assert called == []
        queued.pop()()
        assert called == [42]
        assert lifecycle.shutdown(0)
    assert not lifecycle.launch(lambda: None, name="refused")


def test_launched_operation_releases_its_lease_on_exception(monkeypatch):
    queued = []

    class Thread:
        def __init__(self, *, target, daemon, name):
            queued.append(target)

        def start(self):
            pass

    def fail():
        raise ValueError("operation failed")

    monkeypatch.setattr(lifecycle.threading, "Thread", Thread)
    assert lifecycle.launch(fail, name="test")
    with pytest.raises(ValueError, match="operation failed"):
        queued.pop()()
    assert lifecycle.shutdown(0)


@pytest.mark.parametrize("producer", [False, True])
def test_reset_cannot_replace_state_under_a_live_owner(producer):
    entered, release = threading.Event(), threading.Event()
    scheduler = work.scheduler
    record = lifecycle.open_run("old", runs.SWEEP)

    def operation():
        entered.set()
        assert release.wait(5)
        lifecycle.tally("old", "conform")

    with ThreadPoolExecutor() as pool:
        if producer:

            def producing():
                with lifecycle.producer() as allowed:
                    assert allowed
                    operation()

            future = pool.submit(producing)
        else:
            phase = scheduler.submit("old", "/file", "work", operation)
            task = claim(scheduler)
            future = pool.submit(run_task, scheduler, task)
        try:
            assert entered.wait(3)
            with pytest.raises(RuntimeError, match="cannot reset"):
                lifecycle.reset()
            assert work.scheduler is scheduler
            assert record.done == 0
        finally:
            release.set()
        future.result(timeout=3)
        if not producer:
            assert phase.settled.is_set()
    assert record.done == 1
    lifecycle.reset()
    assert work.scheduler is not scheduler


def test_pause_and_stop_leave_an_active_phases_commit_gate_open():
    entered, release = threading.Event(), threading.Event()
    cancel = Cancel("/file")
    lifecycle.open_run("walk", runs.SWEEP)

    def editing():
        entered.set()
        assert release.wait(3)
        return cancel.commit()

    phase = work.scheduler.submit("walk", "/file", "work", editing, cancel=cancel)
    task = claim(work.scheduler)
    with ThreadPoolExecutor() as pool:
        editing = pool.submit(run_task, work.scheduler, task)
        try:
            assert entered.wait(3)
            assert lifecycle.pause()
            assert lifecycle.stop("walk")
            assert not cancel.stopped()
        finally:
            release.set()
        editing.result(timeout=3)
    assert phase.result()
