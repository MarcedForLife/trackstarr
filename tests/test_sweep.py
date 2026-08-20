"""Sweep scheduling and the DRY_RUN latch. The walk and the cache live in
the integration and sweep-cache suites."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import read_events
from trackstarr import config
from trackstarr import sweep as sweep_mod
from trackstarr.arr import LibraryIndex
from trackstarr.policy import Policy
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.sweep import _MIN_PROBE_WORKERS, Judged, seconds_until, sweep
from trackstarr.sweep_cache import Verdict


def _library(tmp_path, monkeypatch, count: int) -> list[str]:
    root = tmp_path / "library"
    root.mkdir()
    names = [f"{i:03d}.mkv" for i in range(count)]
    for name in names:
        (root / name).write_text("not really a video")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    return names


def test_concurrent_sweep_reports_in_walk_order(monkeypatch, tmp_path):
    """Verdicts are booked in walk order however many workers produced them, so
    raising concurrency doesn't reshuffle pending.tsv."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 4)
    _library(tmp_path, monkeypatch, 40)
    # Fixed order of our own, since os.walk's is the filesystem's business.
    walked = sorted(str(path) for path in (tmp_path / "library").iterdir())
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: walked)

    # Uneven, order-scrambling delays: under as_completed the odd-numbered
    # files would all land after the even ones.
    def slow_judge(path, **kwargs):
        time.sleep(0.02 if int(os.path.basename(path)[:3]) % 2 else 0.001)
        return Judged(Job(path), None, Verdict(Status.WOULD_FIX, "reorder streams"))

    monkeypatch.setattr("trackstarr.sweep._judge", slow_judge)
    sweep(dry_run=True)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert [row.split("\t")[2] for row in rows] == walked


def test_a_path_cannot_break_its_own_report_row(monkeypatch, tmp_path):
    """Tabs and newlines are legal in filenames and would shift every column
    after the path, so the row would parse as a different verdict about a
    different file. This is the one column that cannot be truncated instead."""
    awkward = str(tmp_path / "lib" / "two\tcolumns\nand a row.mkv")
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: [awkward])
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(
            Job(path), None, Verdict(Status.WOULD_FIX, "reorder streams")
        ),
    )
    sweep(dry_run=True)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert len(rows) == 1
    assert rows[0].split("\t")[2] == awkward.replace("\t", " ").replace("\n", " ")


def test_a_worker_raising_does_not_abandon_the_sweep(monkeypatch, tmp_path):
    """One unforeseen error must not cost every file queued behind it."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    _library(tmp_path, monkeypatch, 10)

    def explode(job, dry_run, source="webhook"):
        if job.path.endswith("004.mkv"):
            raise RuntimeError("something nobody predicted")
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", explode)

    counts = sweep(dry_run=True)
    assert counts[Status.FAILED] == 1
    assert counts[Status.CONFORM] == 9


@pytest.mark.parametrize("budget", [1, 6])
def test_probing_is_not_serialized_by_the_rewrite_budget(monkeypatch, tmp_path, budget):
    """The budget is enforced by the slots inside process(), not by the judging
    pool, so a budget of 1 must still probe concurrently (a cold report-only
    sweep is probe-bound) and a raised one must still get a worker per slot."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", budget)
    _library(tmp_path, monkeypatch, 1)
    captured = {}

    def spying_pool(max_workers, **kwargs):
        captured["workers"] = max_workers
        return ThreadPoolExecutor(max_workers=max_workers, **kwargs)

    monkeypatch.setattr("trackstarr.sweep.ThreadPoolExecutor", spying_pool)
    sweep(dry_run=True)
    assert captured["workers"] == max(_MIN_PROBE_WORKERS, budget)


def test_an_arr_outage_downgrades_an_applying_sweep(monkeypatch, tmp_path, caplog):
    """With original languages unknown, the languages rule would read a foreign
    film's own track as junk to drop. Report-only until the *arr answers."""
    _library(tmp_path, monkeypatch, 1)
    monkeypatch.setattr("trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, False))
    judged_dry = []

    def spy(job, dry_run, source="sweep"):
        judged_dry.append(dry_run)
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", spy)

    sweep(dry_run=False)
    assert judged_dry == [True]
    assert "report-only" in caplog.text
    (entry,) = read_events()
    assert entry["dry_run"] is True


def test_dry_run_overrides_an_applying_sweep(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", True)
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])

    sweep(dry_run=False)

    (entry,) = read_events()
    assert entry["event"] == "sweep"
    assert entry["dry_run"] is True


def test_checkpoints_come_from_time_not_file_count(monkeypatch, tmp_path):
    """An applying sweep can spend minutes on one file, so what it learned must not
    wait on a count a small library never reaches."""
    _library(tmp_path, monkeypatch, 3)
    monkeypatch.setattr("trackstarr.sweep._CHECKPOINT_SECONDS", 0.0)
    checkpoints = []
    monkeypatch.setattr(
        "trackstarr.sweep_cache.SweepCache.checkpoint", lambda self: checkpoints.append(1)
    )

    sweep(dry_run=True)
    assert len(checkpoints) == 3


@pytest.mark.parametrize(
    ("hour", "second", "expected"),
    [
        (1, 0, 3 * 3600),
        (5, 0, 23 * 3600),
        # Rescheduling seconds after the sweep fired must land on the next
        # run, not the one still on the clock's current minute.
        (4, 5, 24 * 3600 - 5),
    ],
    ids=["later today", "rolls to tomorrow", "never the slot that just fired"],
)
def test_seconds_until_the_next_4am_sweep(hour, second, expected):
    now = time.mktime((2026, 8, 13, hour, 0, second, 0, 0, -1))
    assert seconds_until("0 4 * * *", now) == pytest.approx(expected)


def test_seconds_until_rejects_garbage():
    with pytest.raises(ValueError):
        seconds_until("not-a-schedule")


def test_a_missing_media_dir_is_reported_not_walked_silently(tmp_path, monkeypatch, caplog):
    """os.walk yields nothing for a path that isn't there, so a wrong mount would
    look like an empty library."""
    real = tmp_path / "media"
    real.mkdir()
    (real / "f.mkv").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "gone"), str(real)])

    found = sweep_mod.walk_library(Policy.from_config())
    assert found == [str(real / "f.mkv")]
    assert "media dir" in caplog.text
    assert "does not exist" in caplog.text


def test_the_walk_clears_staged_files_scattered_through_the_library(tmp_path, monkeypatch):
    """Cross-filesystem publishing lands its copy beside the file it replaces, so
    a crash leaves these anywhere. This walk is the only thing that visits
    them, and the age gate is what makes dropping them safe."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 0)
    (tmp_path / "f.mkv").write_bytes(b"x")
    orphan = tmp_path / ".trackstarr-eeee.partial"
    orphan.write_text("orphaned by a crash")

    found = sweep_mod.walk_library(Policy.from_config())
    assert found == [str(tmp_path / "f.mkv")]
    assert not orphan.exists()


def test_hidden_directories_are_not_walked(tmp_path, monkeypatch):
    """@eaDir, .recycle and friends hold copies that must never be rewritten."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    (tmp_path / "f.mkv").write_bytes(b"x")
    hidden = tmp_path / ".recycle"
    hidden.mkdir()
    (hidden / "deleted.mkv").write_bytes(b"x")

    assert sweep_mod.walk_library(Policy.from_config()) == [str(tmp_path / "f.mkv")]


def test_a_long_sweep_logs_progress_as_it_goes(monkeypatch, caplog):
    """A library sweep runs for hours. Without a periodic line the log looks hung
    and there is nothing to judge the rate from."""
    caplog.set_level(logging.INFO, logger="trackstarr.sweep")
    walked = [f"/data/{i:04d}.mkv" for i in range(500)]
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: walked)
    monkeypatch.setattr("trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, True))
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(Job(path), None, Verdict(Status.CONFORM)),
    )

    sweep(dry_run=True)
    assert "500/500" in caplog.text
