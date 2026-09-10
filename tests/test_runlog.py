"""Worker log lines kept per file, driven the way the registry drives them."""

import logging

import pytest

from trackstarr import runlog, runs


@pytest.fixture(autouse=True)
def _clean_buffer():
    """Both are module state. The registry is here too because begin() and
    finish() are what attach and detach the buffer."""
    runs.reset()
    runlog.forget()
    yield
    runs.reset()
    runlog.forget()


def test_a_workers_log_lines_are_kept_with_the_file_it_had_in_hand(caplog):
    caplog.set_level(logging.INFO)
    runlog.capture()
    runs.open_run("r#1", runs.SWEEP)
    logging.getLogger("trackstarr.test").info("before anything was picked up")
    runs.begin("r#1", "/data/a.mkv")
    logging.getLogger("trackstarr.test").info("ffmpeg -i a.mkv")
    runs.finish("r#1", "/data/a.mkv")
    logging.getLogger("trackstarr.test").info("after it was let go")

    kept = runlog.lines("r#1", "/data/a.mkv")
    assert [line.split("INFO")[-1].strip() for line in kept] == ["ffmpeg -i a.mkv"]
    # Still readable once the file is done with, which is when somebody has a
    # verdict to explain.
    runs.tally("r#1", "modified", path="/data/a.mkv")
    assert runlog.lines("r#1", "/data/a.mkv") == kept
    assert runlog.lines("r#1", "/data/never-touched.mkv") == []


def test_a_line_with_no_thread_on_it_belongs_to_no_file(monkeypatch, caplog):
    """The thread is the whole of how a line finds its file, so a build with
    logging.logThreads turned off keeps none of them rather than filing every
    worker's output against whichever file was picked up last."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logging, "logThreads", False)
    runlog.capture()
    runs.open_run("r#1", runs.SWEEP)
    runs.begin("r#1", "/data/a.mkv")
    logging.getLogger("trackstarr.test").info("ffmpeg -i a.mkv")

    assert runlog.lines("r#1", "/data/a.mkv") == []


def test_only_so_many_files_worth_of_log_is_kept(monkeypatch, caplog):
    """A sweep of ten thousand files would otherwise hold every line it ever
    wrote."""
    monkeypatch.setattr(runlog, "_LOGGED_FILES", 2)
    caplog.set_level(logging.INFO)
    runlog.capture()
    runs.open_run("r#1", runs.SWEEP)
    for at in range(3):
        runs.begin("r#1", f"/data/{at}.mkv")
        logging.getLogger("trackstarr.test").info("probing %d", at)
        runs.finish("r#1", f"/data/{at}.mkv")
    assert runlog.lines("r#1", "/data/0.mkv") == [], "the oldest went first"
    assert len(runlog.lines("r#1", "/data/2.mkv")) == 1
