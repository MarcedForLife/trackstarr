"""The activity registry and the pause switch."""

import pytest

from conftest import queued_control, set_config
from trackstarr import lifecycle, runlog, runs
from trackstarr.executor import Cancel


def test_import_labels_follow_connection_renames():
    record = lifecycle.open_run("delivery", runs.IMPORT, instance_id="radarr-4k", filling=True)
    for label in ("UHD", "Cinema"):
        set_config(RADARR_4K_NAME=label)
        assert runs.snapshot()["runs"][0]["label"] == label
    assert record.instance_id == "radarr-4k"
    assert record.label == ""


@pytest.fixture(autouse=True)
def _clean_registry():
    """Module state, so a run or a pause left behind would decide the next
    test. Cleared both sides: an assertion that fails mid-test still has to
    hand the suite back a running service."""
    runs.reset()
    runlog.forget()
    yield
    runs.reset()
    runlog.forget()


def test_a_sweep_reports_its_progress_while_it_runs():
    lifecycle.open_run("r#1", runs.SWEEP, dry_run=True)
    runs.set_total("r#1", 3)
    runs.begin("r#1", "/data/a.mkv")
    lifecycle.tally("r#1", "conform")

    (run,) = runs.snapshot()["runs"]
    assert run["type"] == "sweep"
    assert (run["done"], run["total"]) == (1, 3)
    assert run["counts"] == {"conform": 1}
    assert run["dry_run"] is True
    assert [active["path"] for active in run["active"]] == ["/data/a.mkv"]

    runs.finish("r#1", "/data/a.mkv")
    assert runs.snapshot()["runs"][0]["active"] == []
    # A sweep is closed by whatever is running it, never by its own counts.
    lifecycle.tally("r#1", "conform")
    lifecycle.tally("r#1", "conform")
    assert runs.snapshot()["runs"]
    lifecycle.close_run("r#1")
    assert runs.snapshot()["runs"] == []


def test_a_finished_file_keeps_its_row_and_gains_its_verdict():
    """A row that vanished the moment its file was let go took the verdict
    with it: the panel showed a name for a minute and then nothing at all."""
    lifecycle.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/a.mkv")
    runs.finish("r#1", "/data/a.mkv")
    # Up before the verdict is: the walking thread books it a moment after
    # the worker lets the file go.
    (waiting,) = runs.snapshot()["runs"][0]["recent"]
    assert (waiting["path"], waiting["status"]) == ("/data/a.mkv", "")

    lifecycle.tally("r#1", "pending", path="/data/a.mkv", detail="add 2.0 downmix")
    (settled,) = runs.snapshot()["runs"][0]["recent"]
    assert settled["status"] == "pending"
    assert settled["detail"] == "add 2.0 downmix"


def test_a_cached_verdict_gets_no_row_at_all():
    """Cached verdicts get no row: a settled library is almost entirely them,
    and there is nothing to open. The tally still counts them."""
    lifecycle.open_run("r#1", runs.SWEEP)
    lifecycle.tally("r#1", "conform", path="/data/a.mkv", cached=True)
    (run,) = runs.snapshot()["runs"]
    assert run["recent"] == []
    assert (run["done"], run["counts"]) == (1, {"conform": 1})


def test_a_file_decided_without_being_picked_up_still_gets_one():
    """A delivery parked behind a seeding download is booked as deferred
    before anything opens it, and that is a verdict worth a row."""
    lifecycle.open_run("r#1", runs.SWEEP)
    lifecycle.tally("r#1", "deferred", path="/data/a.mkv", detail="still hard-linked")
    (row,) = runs.snapshot()["runs"][0]["recent"]
    assert (row["status"], row["detail"], row["seconds"]) == (
        "deferred",
        "still hard-linked",
        0.0,
    )


def test_recent_files_are_newest_first_and_bounded(monkeypatch):
    monkeypatch.setattr(runs, "_RECENT_FILES", 3)
    lifecycle.open_run("r#1", runs.SWEEP)
    for at in range(5):
        lifecycle.tally("r#1", "failed", path=f"/data/{at}.mkv")
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
    lifecycle.open_run("r#1", runs.SWEEP)
    for _ in range(2):
        runs.begin("r#1", "/data/a.mkv")
        runs.finish("r#1", "/data/a.mkv")
    lifecycle.tally("r#1", "failed", path="/data/a.mkv")
    lifecycle.tally("r#1", "modified", path="/data/a.mkv")
    # Newest first out of the snapshot, so the second pass reads at the top.
    assert [row["status"] for row in runs.snapshot()["runs"][0]["recent"]] == [
        "modified",
        "failed",
    ]


def test_the_snapshot_says_which_registry_answered_it():
    """Nothing resumes a run, so a page has to be able to tell a sweep that
    finished from one a restart cut off. The stamp holds while the process
    does and changes with it."""
    lifecycle.open_run("r#1", runs.SWEEP)
    first = runs.snapshot()["up_since"]
    assert first and runs.snapshot()["up_since"] == first


def test_an_import_retires_itself_once_its_last_file_is_done():
    lifecycle.open_run("r#1", runs.IMPORT, instance_id="sonarr", filling=True)
    runs.add_file("r#1")
    runs.add_file("r#1")
    lifecycle.seal("r#1")

    lifecycle.tally("r#1", "modified")
    assert runs.snapshot()["runs"], "one file still to go"
    lifecycle.tally("r#1", "conform")
    assert runs.snapshot()["runs"] == []


def test_an_import_stays_while_its_files_are_still_arriving():
    """A delivery whose first file finishes before its third is queued must
    stay one run, not close and reopen as two."""
    lifecycle.open_run("r#1", runs.IMPORT, instance_id="radarr", filling=True)
    runs.add_file("r#1")
    lifecycle.tally("r#1", "modified")
    assert runs.snapshot()["runs"], "still being handed files"

    runs.add_file("r#1")
    lifecycle.seal("r#1")
    lifecycle.tally("r#1", "modified")
    assert runs.snapshot()["runs"] == []


def test_a_released_parked_file_rejoins_the_delivery_that_queued_it():
    lifecycle.open_run("r#1", runs.IMPORT, instance_id="sonarr")
    first = runs.snapshot()["runs"][0]["started"]
    lifecycle.open_run("r#1", runs.IMPORT, instance_id="sonarr")
    (run,) = runs.snapshot()["runs"]
    assert run["started"] == first, "the same import resuming, not a second one"


def test_a_run_with_no_id_is_booked_against_nothing():
    """The CLI's fix carries no run, and every call site would otherwise need
    the same guard."""
    runs.begin(None, "/data/a.mkv")
    lifecycle.tally(None, "modified")
    runs.finish(None, "/data/a.mkv")
    assert runs.snapshot()["runs"] == []


def test_a_run_that_has_already_closed_takes_no_more_work():
    """A walk stopped mid-file still has telemetry in flight behind it, and
    each call has to land on nothing rather than reopen the run."""
    runs.walking("r#gone", False)
    runs.begin("r#gone", "/data/a.mkv", 300.0)
    assert runs.snapshot()["runs"] == []


def test_active_files_are_ordered_by_how_long_they_have_been_going():
    lifecycle.open_run("r#1", runs.SWEEP)
    for name in ("first", "second", "third"):
        runs.begin("r#1", f"/data/{name}.mkv")
    # Longest-running first: with room for one line, that is the file worth
    # showing.
    paths = [active["path"] for active in runs.snapshot()["runs"][0]["active"]]
    assert paths == ["/data/first.mkv", "/data/second.mkv", "/data/third.mkv"]


def test_runs_are_listed_oldest_first():
    lifecycle.open_run("r#1", runs.SWEEP)
    lifecycle.open_run("r#2", runs.IMPORT)
    assert [run["id"] for run in runs.snapshot()["runs"]] == ["r#1", "r#2"]


def waiting(*rows: runs.Queued) -> dict[str, runs.Control]:
    """A scheduler view of one run's queue, as a read is handed one."""
    return {"r#1": queued_control(*rows)}


def test_the_snapshot_names_the_files_still_waiting():
    """Nothing can be skipped that the page cannot name."""
    lifecycle.open_run("r#1", runs.SWEEP)
    view = waiting(runs.Queued("/data/first.mkv", 90.0), runs.Queued("/data/second.mkv", 30.0))
    upcoming = runs.snapshot(view)["runs"][0]["upcoming"]
    # In the order the sweep will reach them.
    assert [file["path"] for file in upcoming] == ["/data/first.mkv", "/data/second.mkv"]
    assert [file["skipped"] for file in upcoming] == [False, False]


def test_a_read_flags_the_rows_the_scheduler_says_are_stopping_or_skipped():
    """Stop and skip belong to the scheduler; a read joins them to the rows it
    draws rather than keeping its own copy."""
    lifecycle.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/first.mkv", 90.0)
    view = {
        "r#1": queued_control(
            runs.Queued("/data/second.mkv", 30.0),
            stopping=True,
            skipped=frozenset({"/data/first.mkv"}),
            claimed=(runs.Queued("/data/first.mkv", 90.0, True),),
        )
    }
    (run,) = runs.snapshot(view)["runs"]
    assert run["stopping"] is True
    assert [file["skipped"] for file in run["active"]] == [True]
    # The scheduler still holds the claim on the file being worked; only the
    # registry knows a thread has it, so only the read can drop that row.
    assert [file["path"] for file in run["upcoming"]] == ["/data/second.mkv"]
    # A run the scheduler has no control for has nothing left to stop or skip.
    (plain,) = runs.snapshot()["runs"]
    assert plain["stopping"] is False
    assert [file["skipped"] for file in plain["active"]] == [False]


def test_the_waiting_list_is_bounded():
    """A first-night sweep queues thousands; every open tab polls this."""
    lifecycle.open_run("r#1", runs.SWEEP)
    view = waiting(*(runs.Queued(f"/data/{at}.mkv") for at in range(runs.UPCOMING + 5)))
    run = runs.snapshot(view)["runs"][0]
    assert len(run["upcoming"]) == runs.UPCOMING
    assert run["queued"] == runs.UPCOMING + 5, "and the count is still the whole queue"


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


def test_aborting_a_phase_that_never_started_an_encode_signals_nothing():
    """A skipped probe, or a rewrite still waiting on its slot. The switch is
    still marked, so the encode that was about to start does not."""
    cancel = Cancel("/data/f.mkv")
    assert runs.abort_phase(cancel) == 0
    assert cancel.asked.is_set()


def test_bookkeeping_for_a_run_that_has_already_finished_is_dropped():
    """The page can stop a run in the same second it closes, and a worker can
    book its last file just after; neither may raise."""
    runs.set_total("gone", 40)
    runs.add_file("gone")
    runs.begin("gone", "/data/a.mkv")
    runs.finish("gone", "/data/a.mkv")
    lifecycle.tally("gone", "modified")
    lifecycle.seal("gone")
    lifecycle.close_run("gone")
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
    lifecycle.open_run("i#1", runs.IMPORT)
    for _ in range(3):
        runs.add_file("i#1")
    runs.begin("i#1", "/data/film.mkv")

    # A sweep part way through a library, two probes being worked on.
    lifecycle.open_run("s#1", runs.SWEEP)
    runs.set_total("s#1", 40)
    for _ in range(10):
        lifecycle.tally("s#1", "conform")
    runs.begin("s#1", "/data/one.mkv")
    runs.begin("s#1", "/data/two.mkv")

    # 2 of the delivery and 28 of the sweep, with three files being worked on.
    assert runs.workload() == (30, 3)


def test_a_dropped_file_leaves_the_run_able_to_retire():
    """A stopped delivery's queued files get no verdict, but a run whose done
    never reaches its total never closes."""
    lifecycle.open_run("i#1", runs.IMPORT, filling=True)
    for _ in range(3):
        runs.add_file("i#1")
    lifecycle.tally("i#1", "modified")
    lifecycle.seal("i#1")
    assert runs.workload() == (2, 0)

    lifecycle.drop("i#1")
    assert runs.workload() == (1, 0), "one fewer to get through, and no verdict for it"
    lifecycle.drop("i#1")
    # Nothing left that it will ever reach, so it retires rather than sitting
    # on the overview for ever.
    assert runs.snapshot()["runs"] == []
    # And one for a run that has already gone is not an error.
    lifecycle.drop("i#1")
    lifecycle.drop(None)


def test_cache_holder_names_either_walk_and_nothing_else():
    """A sweep and a re-check both write the cache whole, so a second would
    lose the first's verdicts. An import writes no cache."""
    runs.reset()
    assert runs.cache_holder() is None

    lifecycle.open_run("i#1", runs.IMPORT)
    assert runs.cache_holder() is None, "a delivery holds nothing"

    lifecycle.open_run("r#1", runs.RECHECK)
    assert runs.cache_holder().id == "r#1"
    lifecycle.close_run("r#1")

    lifecycle.open_run("s#1", runs.SWEEP)
    assert runs.cache_holder().id == "s#1"
    runs.reset()


def test_a_file_in_hand_reports_how_far_into_it_the_rewrite_is():
    """What the per-file bar is drawn from. The stage is half the answer: the
    queue for a rewrite slot is minutes on a busy machine, and a bar sitting at
    nothing through it would read as an encode that has stalled."""
    lifecycle.open_run("r#1", runs.SWEEP)
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


def test_settle_keeps_a_finished_encode_finishing():
    lifecycle.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/a.mkv")
    runs.begin("r#1", "/data/b.mkv")
    runs.stage("r#1", "/data/a.mkv", runs.FINISHING)
    runs.stage("r#1", "/data/b.mkv", runs.ENCODING, 60.0)
    runs.settle("r#1", "/data/a.mkv")
    runs.settle("r#1", "/data/b.mkv")
    stages = {file["path"]: file["stage"] for file in runs.snapshot()["runs"][0]["active"]}
    assert stages == {"/data/a.mkv": runs.FINISHING, "/data/b.mkv": runs.WORKING}


def test_progress_for_a_file_nobody_is_holding_is_ignored():
    """The readout arrives from ffmpeg's own thread, which can outlive the
    rename by a moment; a run with no such file is a no-op, not a crash."""
    lifecycle.open_run("r#1", runs.SWEEP)
    runs.progress("r#1", "/data/gone.mkv", 10.0, 2.0)
    runs.stage("r#1", "/data/gone.mkv", runs.ENCODING, 60.0)
    runs.progress(None, "/data/a.mkv", 10.0, 2.0)
    assert runs.snapshot()["runs"][0]["active"] == []
