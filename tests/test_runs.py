"""The activity registry and the pause switch."""

import collections
import json
import logging
import os
import threading

import pytest

from conftest import read_events
from trackstarr import config, runs


@pytest.fixture(autouse=True)
def _clean_registry():
    """Module state, so a run or a pause left behind would decide the next
    test. Cleared both sides: an assertion that fails mid-test still has to
    hand the suite back a running service."""
    runs._runs.clear()
    runs._running.set()
    runs._paused_by = runs._paused_at = ""
    runs.forget_logs()
    yield
    runs._runs.clear()
    runs._running.set()
    runs._paused_by = runs._paused_at = ""
    runs.forget_logs()


def flag_path() -> str:
    return os.path.join(config.STATE_DIR, runs.PAUSED_FILE)


def test_pausing_holds_every_thread_that_asks_to_start():
    started = threading.Event()
    allowed = threading.Event()

    def work():
        started.set()
        if runs.hold():
            allowed.set()

    runs.pause("marc")
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert started.wait(2)
    # The gate is the whole mechanism: one flag, and every worker and every
    # sweep thread waits on it.
    assert not allowed.wait(0.2)

    runs.resume("marc")
    assert allowed.wait(2)
    thread.join(timeout=2)


def test_a_pause_survives_a_restart():
    runs.pause("marc")
    with open(flag_path()) as flag:
        assert json.load(flag)["paused"] is True

    # A fresh process: the flag is all it has.
    runs._running.set()
    runs._paused_by = runs._paused_at = ""
    runs.load_paused()
    assert runs.paused()
    assert runs.snapshot()["paused_by"] == "marc"


def test_resuming_clears_the_flag_a_restart_would_read():
    runs.pause("marc")
    runs.resume("marc")
    runs._running.set()
    runs.load_paused()
    assert not runs.paused()


@pytest.mark.parametrize("content", ["not json at all", '["a list"]', '{"paused": false}'])
def test_a_flag_that_says_nothing_useful_reads_as_running(content):
    """Better to come up sweeping than stuck paused with nothing in the UI
    able to say why; a pause is cheap to make again."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(flag_path(), "w") as flag:
        flag.write(content)
    runs.load_paused()
    assert not runs.paused()
    assert not runs.paused_on_disk()


def test_pausing_twice_changes_nothing_and_says_so():
    assert runs.pause("marc") is True
    assert runs.pause("marc") is False
    assert runs.resume("marc") is True
    assert runs.resume("marc") is False


def test_the_history_records_who_paused_and_resumed():
    runs.pause("marc")
    runs.resume("marc")
    kinds = [(entry["event"], entry.get("by")) for entry in read_events()]
    assert kinds == [("paused", "marc"), ("resumed", "marc")]


def test_a_stopped_run_tells_its_threads_to_give_up():
    runs.open_run("r#1", runs.SWEEP)
    assert runs.hold("r#1") is True
    runs.stop("r#1")
    assert runs.hold("r#1") is False
    # A run nobody registered cannot be stopped, which is how the API answers
    # a page acting on a sweep that has since finished.
    assert runs.stop("r#2") is False


def test_a_pause_lifted_into_a_stop_still_gives_up():
    """The stop arrives while the thread is already asleep on the pause: it
    must not go on to process a file once resumed."""
    runs.open_run("r#1", runs.SWEEP)
    runs.pause()
    verdict: list[bool] = []
    thread = threading.Thread(target=lambda: verdict.append(runs.hold("r#1")), daemon=True)
    thread.start()
    runs.stop("r#1")
    thread.join(timeout=5)
    assert verdict == [False]


def test_a_sweep_reports_its_progress_while_it_runs():
    runs.open_run("r#1", runs.SWEEP, dry_run=True)
    runs.set_total("r#1", 3)
    runs.begin("r#1", "/data/a.mkv")
    runs.tally("r#1", "conform")

    (run,) = runs.snapshot()["runs"]
    assert run["kind"] == "sweep"
    assert (run["done"], run["total"]) == (1, 3)
    assert run["counts"] == {"conform": 1}
    assert run["dry_run"] is True
    assert [active["path"] for active in run["active"]] == ["/data/a.mkv"]

    runs.finish("r#1", "/data/a.mkv")
    assert runs.snapshot()["runs"][0]["active"] == []
    # A sweep is closed by whatever is running it, never by its own counts.
    runs.tally("r#1", "conform")
    runs.tally("r#1", "conform")
    assert runs.snapshot()["runs"]
    runs.close_run("r#1")
    assert runs.snapshot()["runs"] == []


def test_a_finished_file_keeps_its_row_and_gains_its_verdict():
    """A row that vanished the moment its file was let go took the verdict
    with it: the panel showed a name for a minute and then nothing at all."""
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/a.mkv")
    runs.finish("r#1", "/data/a.mkv")
    # Up before the verdict is: the walking thread books it a moment after
    # the worker lets the file go.
    (waiting,) = runs.snapshot()["runs"][0]["recent"]
    assert (waiting["path"], waiting["status"]) == ("/data/a.mkv", "")

    runs.tally("r#1", "would-fix", path="/data/a.mkv", detail="add 2.0 downmix")
    (settled,) = runs.snapshot()["runs"][0]["recent"]
    assert settled["status"] == "would-fix"
    assert settled["detail"] == "add 2.0 downmix"


def test_a_cached_verdict_gets_no_row_at_all():
    """Cached verdicts get no row: a settled library is almost entirely them,
    and there is nothing to open. The tally still counts them."""
    runs.open_run("r#1", runs.SWEEP)
    runs.tally("r#1", "conform", path="/data/a.mkv", cached=True)
    (run,) = runs.snapshot()["runs"]
    assert run["recent"] == []
    assert (run["done"], run["counts"]) == (1, {"conform": 1})


def test_a_file_decided_without_being_picked_up_still_gets_one():
    """A delivery parked behind a seeding download is booked as deferred
    before anything opens it, and that is a verdict worth a row."""
    runs.open_run("r#1", runs.SWEEP)
    runs.tally("r#1", "deferred", path="/data/a.mkv", detail="still hard-linked")
    (row,) = runs.snapshot()["runs"][0]["recent"]
    assert (row["status"], row["detail"], row["seconds"]) == (
        "deferred",
        "still hard-linked",
        0.0,
    )


def test_recent_files_are_newest_first_and_bounded(monkeypatch):
    monkeypatch.setattr(runs, "_RECENT_FILES", 3)
    runs.open_run("r#1", runs.SWEEP)
    # Built by hand: the cap is read when the run is made, and this one
    # already exists.
    runs._runs["r#1"].recent = collections.deque(maxlen=3)
    for at in range(5):
        runs.tally("r#1", "failed", path=f"/data/{at}.mkv")
    (run,) = runs.snapshot()["runs"]
    assert [row["path"] for row in run["recent"]] == [
        "/data/4.mkv",
        "/data/3.mkv",
        "/data/2.mkv",
    ]


def test_the_same_file_twice_keeps_both_verdicts_apart():
    """Only a row still waiting for one takes a verdict, and the oldest of
    them first: a run handed the same file twice would otherwise have the
    second verdict land on the first pass."""
    runs.open_run("r#1", runs.SWEEP)
    for _ in range(2):
        runs.begin("r#1", "/data/a.mkv")
        runs.finish("r#1", "/data/a.mkv")
    runs.tally("r#1", "failed", path="/data/a.mkv")
    runs.tally("r#1", "fixed", path="/data/a.mkv")
    # Newest first out of the snapshot, so the second pass reads at the top.
    assert [row["status"] for row in runs.snapshot()["runs"][0]["recent"]] == [
        "fixed",
        "failed",
    ]


def test_a_workers_log_lines_are_kept_with_the_file_it_had_in_hand(caplog):
    caplog.set_level(logging.INFO)
    runs.capture_logs()
    runs.open_run("r#1", runs.SWEEP)
    logging.getLogger("trackstarr.test").info("before anything was picked up")
    runs.begin("r#1", "/data/a.mkv")
    logging.getLogger("trackstarr.test").info("ffmpeg -i a.mkv")
    runs.finish("r#1", "/data/a.mkv")
    logging.getLogger("trackstarr.test").info("after it was let go")

    kept = runs.lines("r#1", "/data/a.mkv")
    assert [line.split("INFO")[-1].strip() for line in kept] == ["ffmpeg -i a.mkv"]
    # Still readable once the file is done with, which is when somebody has a
    # verdict to explain.
    runs.tally("r#1", "fixed", path="/data/a.mkv")
    assert runs.lines("r#1", "/data/a.mkv") == kept
    assert runs.lines("r#1", "/data/never-touched.mkv") == []


def test_a_line_with_no_thread_on_it_belongs_to_no_file(monkeypatch, caplog):
    """The thread is the whole of how a line finds its file, so a build with
    logging.logThreads turned off keeps none of them rather than filing every
    worker's output against whichever file was picked up last."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logging, "logThreads", False)
    runs.capture_logs()
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/a.mkv")
    logging.getLogger("trackstarr.test").info("ffmpeg -i a.mkv")

    assert runs.lines("r#1", "/data/a.mkv") == []


def test_only_so_many_files_worth_of_log_is_kept(monkeypatch, caplog):
    """A sweep of ten thousand files would otherwise hold every line it ever
    wrote."""
    monkeypatch.setattr(runs, "_LOGGED_FILES", 2)
    caplog.set_level(logging.INFO)
    runs.capture_logs()
    runs.open_run("r#1", runs.SWEEP)
    for at in range(3):
        runs.begin("r#1", f"/data/{at}.mkv")
        logging.getLogger("trackstarr.test").info("probing %d", at)
        runs.finish("r#1", f"/data/{at}.mkv")
    assert runs.lines("r#1", "/data/0.mkv") == [], "the oldest went first"
    assert len(runs.lines("r#1", "/data/2.mkv")) == 1


def test_the_snapshot_says_which_registry_answered_it():
    """Nothing resumes a run, so a page has to be able to tell a sweep that
    finished from one a restart cut off. The stamp holds while the process
    does and changes with it."""
    runs.open_run("r#1", runs.SWEEP)
    first = runs.snapshot()["up_since"]
    assert first and runs.snapshot()["up_since"] == first

    # A fresh process: the module is imported again and the stamp taken again.
    runs._UP = runs._UP + 60
    assert runs.snapshot()["up_since"] != first


def test_an_import_retires_itself_once_its_last_file_is_done():
    runs.open_run("r#1", runs.IMPORT, label="sonarr", filling=True)
    runs.add_file("r#1")
    runs.add_file("r#1")
    runs.seal("r#1")

    runs.tally("r#1", "fixed")
    assert runs.snapshot()["runs"], "one file still to go"
    runs.tally("r#1", "conform")
    assert runs.snapshot()["runs"] == []


def test_an_import_stays_while_its_files_are_still_arriving():
    """A delivery whose first file finishes before its third is queued must
    stay one run, not close and reopen as two."""
    runs.open_run("r#1", runs.IMPORT, label="radarr", filling=True)
    runs.add_file("r#1")
    runs.tally("r#1", "fixed")
    assert runs.snapshot()["runs"], "still being handed files"

    runs.add_file("r#1")
    runs.seal("r#1")
    runs.tally("r#1", "fixed")
    assert runs.snapshot()["runs"] == []


def test_a_released_parked_file_rejoins_the_delivery_that_queued_it():
    runs.open_run("r#1", runs.IMPORT, label="sonarr")
    first = runs.snapshot()["runs"][0]["started"]
    runs.open_run("r#1", runs.IMPORT, label="sonarr")
    (run,) = runs.snapshot()["runs"]
    assert run["started"] == first, "the same import resuming, not a second one"


def test_a_run_with_no_id_is_booked_against_nothing():
    """The CLI's fix carries no run, and every call site would otherwise need
    the same guard."""
    runs.begin(None, "/data/a.mkv")
    runs.queue(None, "/data/a.mkv", 300.0)
    runs.tally(None, "fixed")
    runs.finish(None, "/data/a.mkv")
    assert runs.snapshot()["runs"] == []


def test_a_run_that_has_already_closed_takes_no_more_work():
    """A walk stopped mid-file still has queue and walking calls in flight
    behind it, and each has to land on nothing rather than reopen the run."""
    runs.queue("r#gone", "/data/a.mkv", 300.0)
    runs.walking("r#gone", False)
    assert runs.snapshot()["runs"] == []


def test_the_running_sweep_is_findable_so_a_second_cannot_start():
    assert runs.running(runs.SWEEP) is None
    runs.open_run("r#1", runs.IMPORT)
    assert runs.running(runs.SWEEP) is None, "an import is not a sweep"
    runs.open_run("r#2", runs.SWEEP)
    assert runs.running(runs.SWEEP).id == "r#2"


def test_active_files_are_ordered_by_how_long_they_have_been_going():
    runs.open_run("r#1", runs.SWEEP)
    for name in ("first", "second", "third"):
        runs.begin("r#1", f"/data/{name}.mkv")
    # Longest-running first: with room for one line, that is the file worth
    # showing.
    paths = [active["path"] for active in runs.snapshot()["runs"][0]["active"]]
    assert paths == ["/data/first.mkv", "/data/second.mkv", "/data/third.mkv"]


def test_runs_are_listed_oldest_first():
    runs.open_run("r#1", runs.SWEEP)
    runs.open_run("r#2", runs.IMPORT)
    assert [run["id"] for run in runs.snapshot()["runs"]] == ["r#1", "r#2"]


def test_a_waiting_file_can_be_taken_off_a_run():
    """The sweep asks before it starts each queued file, so a skip lands before
    a slot is spent on it."""
    runs.open_run("r#1", runs.SWEEP)
    runs.queue("r#1", "/data/f.mkv", 90.0)
    assert runs.skip("r#1", "/data/f.mkv") == "waiting"
    assert runs.skipped("r#1", "/data/f.mkv")
    assert not runs.skipped("r#1", "/data/other.mkv")


def test_skipping_a_file_a_thread_already_has_says_so():
    """The caller kills that one encode; there is nothing else that separates
    skipping a file from waiting for it."""
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/f.mkv")
    assert runs.skip("r#1", "/data/f.mkv") == "active"


def test_a_file_the_run_has_reached_a_verdict_on_cannot_be_skipped():
    """Otherwise the page offers Skip on a row that has already been rewritten,
    and the answer says it worked."""
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/f.mkv")
    runs.finish("r#1", "/data/f.mkv")
    runs.tally("r#1", "fixed", path="/data/f.mkv")
    assert runs.skip("r#1", "/data/f.mkv") == ""


def test_a_file_between_its_probe_and_its_slot_can_still_be_skipped():
    """An applying sweep releases a file after the probe and queues it for a
    rewrite, so it is on the released list with no verdict while still being
    the next thing the run will do."""
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/f.mkv")
    runs.finish("r#1", "/data/f.mkv")
    runs.queue("r#1", "/data/f.mkv", 90.0)
    assert runs.skip("r#1", "/data/f.mkv") == "waiting"


def test_a_delivery_queued_file_can_be_skipped():
    """An import's queue is the work queue, which the registry never sees, so
    there is nothing here to match it against."""
    runs.open_run("r#1", runs.IMPORT, label="radarr", filling=True)
    runs.add_file("r#1")
    assert runs.skip("r#1", "/data/f.mkv") == "waiting"


def test_skipping_a_run_that_has_gone_says_so():
    assert runs.skip("r#gone", "/data/f.mkv") == ""


def test_a_skip_dies_with_its_run():
    """A skip is "not in this pass". Anything longer-lived is a hold."""
    runs.open_run("r#1", runs.SWEEP)
    runs.queue("r#1", "/data/f.mkv", 0.0)
    runs.skip("r#1", "/data/f.mkv")
    runs.close_run("r#1")
    runs.open_run("r#1", runs.SWEEP)
    assert not runs.skipped("r#1", "/data/f.mkv")


def test_the_snapshot_names_the_files_still_waiting():
    """Nothing can be skipped that the page cannot name."""
    runs.open_run("r#1", runs.SWEEP)
    runs.queue("r#1", "/data/first.mkv", 90.0)
    runs.queue("r#1", "/data/second.mkv", 30.0)
    runs.skip("r#1", "/data/second.mkv")
    upcoming = runs.snapshot()["runs"][0]["upcoming"]
    # In the order the sweep will reach them.
    assert [file["path"] for file in upcoming] == ["/data/first.mkv", "/data/second.mkv"]
    assert [file["skipped"] for file in upcoming] == [False, True]


def test_the_waiting_list_is_bounded():
    """A first-night sweep queues thousands; every open tab polls this."""
    runs.open_run("r#1", runs.SWEEP)
    for at in range(runs._UPCOMING + 5):
        runs.queue("r#1", f"/data/{at}.mkv", 0.0)
    run = runs.snapshot()["runs"][0]
    assert len(run["upcoming"]) == runs._UPCOMING
    assert run["queued"] == runs._UPCOMING + 5, "and the count is still the whole queue"


def test_aborting_one_file_signals_only_its_rewrite(monkeypatch):
    signalled: list[str] = []
    monkeypatch.setattr(runs, "terminate_running", lambda path="": signalled.append(path) or 1)
    assert runs.abort("/data/f.mkv") == 1
    assert signalled == ["/data/f.mkv"]


def test_aborting_signals_every_rewrite_in_flight(monkeypatch):
    signalled: list[str] = []
    monkeypatch.setattr(runs, "terminate_running", lambda path="": signalled.append(path) or 2)
    assert runs.abort() == 2
    assert signalled == [""], "every rewrite, not one named file"


def test_a_pause_that_cannot_be_written_still_pauses(monkeypatch, caplog):
    """The flag is how a pause survives a restart; losing it must not lose the
    pause itself, which is the thing keeping the machine quiet right now."""

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(runs, "write_json", refuse)
    assert runs.pause("marc") is True
    assert runs.paused()
    assert "could not persist" in caplog.text


def test_a_hold_looks_up_from_the_pause_now_and_then(monkeypatch):
    """It waits in slices rather than one sleep, so a stop arriving mid-pause
    is noticed rather than sat out until the resume."""
    monkeypatch.setattr(runs, "_HOLD_TICK", 0.01)
    looks = []
    real = runs.stopping
    monkeypatch.setattr(runs, "stopping", lambda run_id: looks.append(run_id) or real(run_id))

    runs.open_run("r#1", runs.SWEEP)
    runs.pause()
    thread = threading.Thread(target=lambda: runs.hold("r#1"), daemon=True)
    thread.start()
    while len(looks) < 3:
        pass
    runs.resume()
    thread.join(timeout=5)
    assert len(looks) >= 3, "it woke up more than once while held"


def test_bookkeeping_for_a_run_that_has_already_finished_is_dropped():
    """The page can stop a run in the same second it closes, and a worker can
    book its last file just after; neither may raise."""
    runs.set_total("gone", 40)
    runs.add_file("gone")
    runs.begin("gone", "/data/a.mkv")
    runs.finish("gone", "/data/a.mkv")
    runs.tally("gone", "fixed")
    runs.seal("gone")
    runs.close_run("gone")
    assert runs.snapshot()["runs"] == []


def test_aborting_with_nothing_running_says_nothing(monkeypatch, caplog):
    monkeypatch.setattr(runs, "terminate_running", lambda path="": 0)
    assert runs.abort() == 0
    assert "aborted" not in caplog.text


def test_the_workload_counts_every_run_whatever_asked_for_it():
    """The queue is what the machine still owes the library, from every run, not
    the import queue alone."""
    assert runs.workload() == (0, 0)

    # A delivery: three files queued, one picked up, none finished.
    runs.open_run("i#1", runs.IMPORT)
    for _ in range(3):
        runs.add_file("i#1")
    runs.begin("i#1", "/data/film.mkv")

    # A sweep part way through a library, two probes being worked on.
    runs.open_run("s#1", runs.SWEEP)
    runs.set_total("s#1", 40)
    for _ in range(10):
        runs.tally("s#1", "conform")
    runs.begin("s#1", "/data/one.mkv")
    runs.begin("s#1", "/data/two.mkv")

    # 2 of the delivery and 28 of the sweep, with three files being worked on.
    assert runs.workload() == (30, 3)


def test_stopping_everything_asks_each_run_once():
    runs.open_run("s#1", runs.SWEEP)
    runs.open_run("i#1", runs.IMPORT)
    assert runs.stop_all() == 2
    assert all(run.stopping for run in runs._runs.values())
    # Nothing left to ask the second time, which is what the page reports.
    assert runs.stop_all() == 0


def test_a_dropped_file_leaves_the_run_able_to_retire():
    """A stopped delivery's queued files get no verdict, but a run whose done
    never reaches its total never closes."""
    runs.open_run("i#1", runs.IMPORT, filling=True)
    for _ in range(3):
        runs.add_file("i#1")
    runs.tally("i#1", "fixed")
    runs.seal("i#1")
    assert runs.workload() == (2, 0)

    runs.drop("i#1")
    assert runs.workload() == (1, 0), "one fewer to get through, and no verdict for it"
    runs.drop("i#1")
    # Nothing left that it will ever reach, so it retires rather than sitting
    # on the overview for ever.
    assert runs.snapshot()["runs"] == []
    # And one for a run that has already gone is not an error.
    runs.drop("i#1")
    runs.drop(None)


def test_a_paused_delivery_still_counts_as_waiting():
    """Paused, nothing is running and four files are owed; a queue counting
    only what moves would read nothing."""
    runs.open_run("i#1", runs.IMPORT, filling=True)
    for _ in range(4):
        runs.add_file("i#1")
    runs.pause("someone")
    assert runs.workload() == (4, 0)


def test_cache_holder_names_either_walk_and_nothing_else():
    """A sweep and a re-check both write the cache whole, so a second would
    lose the first's verdicts. An import writes no cache."""
    runs._runs.clear()
    assert runs.cache_holder() is None

    runs.open_run("i#1", runs.IMPORT)
    assert runs.cache_holder() is None, "a delivery holds nothing"

    runs.open_run("r#1", runs.RECHECK)
    assert runs.cache_holder().id == "r#1"
    runs.close_run("r#1")

    runs.open_run("s#1", runs.SWEEP)
    assert runs.cache_holder().id == "s#1"
    runs._runs.clear()


def test_a_file_in_hand_reports_how_far_into_it_the_rewrite_is():
    """What the per-file bar is drawn from. The stage is half the answer: the
    queue for a rewrite slot is minutes on a busy machine, and a bar sitting at
    nothing through it would read as an encode that has stalled."""
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/a.mkv")

    (file,) = runs.snapshot()["runs"][0]["active"]
    assert (file["stage"], file["duration"], file["done"]) == (runs.WORKING, 0.0, 0.0)

    runs.stage("r#1", "/data/a.mkv", runs.WAITING)
    assert runs.snapshot()["runs"][0]["active"][0]["stage"] == runs.WAITING

    runs.stage("r#1", "/data/a.mkv", runs.ENCODING, 5400.0)
    runs.progress("r#1", "/data/a.mkv", 1350.0, 12.5)
    (file,) = runs.snapshot()["runs"][0]["active"]
    assert (file["stage"], file["duration"]) == (runs.ENCODING, 5400.0)
    assert (file["done"], file["speed"]) == (1350.0, 12.5)

    # A second attempt at the same file must not be drawn against the first
    # one's numbers, so each stage starts its own readout.
    runs.stage("r#1", "/data/a.mkv", runs.ENCODING, 5400.0)
    (file,) = runs.snapshot()["runs"][0]["active"]
    assert (file["done"], file["speed"]) == (0.0, 0.0)


def test_progress_for_a_file_nobody_is_holding_is_ignored():
    """The readout arrives from ffmpeg's own thread, which can outlive the
    rename by a moment; a run with no such file is a no-op, not a crash."""
    runs.open_run("r#1", runs.SWEEP)
    runs.progress("r#1", "/data/gone.mkv", 10.0, 2.0)
    runs.stage("r#1", "/data/gone.mkv", runs.ENCODING, 60.0)
    runs.progress(None, "/data/a.mkv", 10.0, 2.0)
    assert runs.snapshot()["runs"][0]["active"] == []
