"""Sweep scheduling and the DRY_RUN latch. The walk and cache behaviour
live in the integration and sweep-cache suites."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trackstarr import config, events
from trackstarr import sweep as sweep_mod
from trackstarr.policy import Policy
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.sweep import _MIN_PROBE_WORKERS, Judged, seconds_until, sweep


def _library(tmp_path, monkeypatch, count: int) -> list[str]:
    root = tmp_path / "library"
    root.mkdir()
    names = [f"{i:03d}.mkv" for i in range(count)]
    for name in names:
        (root / name).write_text("not really a video")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    return names


def test_concurrent_sweep_reports_in_walk_order(monkeypatch, tmp_path):
    """Results are booked in walk order however many workers produced them,
    so turning concurrency up doesn't reshuffle pending.tsv."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 4)
    _library(tmp_path, monkeypatch, 40)
    # Fixed order of our own, since os.walk's is the filesystem's business.
    walked = sorted(str(path) for path in (tmp_path / "library").iterdir())
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: walked)

    # Uneven, order-scrambling delays: under as_completed the odd-numbered
    # files would all land after the even ones.
    def slow_judge(path, **kwargs):
        time.sleep(0.02 if int(os.path.basename(path)[:3]) % 2 else 0.001)
        return Judged(Job(path), None, Status.WOULD_FIX, reasons="reorder streams")

    monkeypatch.setattr("trackstarr.sweep._judge", slow_judge)
    sweep(dry_run=True)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert [row.split("\t")[2] for row in rows] == walked


def test_a_path_cannot_break_its_own_report_row(monkeypatch, tmp_path):
    """Tabs and newlines are legal in filenames and would shift every column
    after the path, so a report row would parse as a different verdict about
    a different file. The reasons and detail cells go through _cell; this is
    the column that cannot be truncated to be made safe."""
    awkward = str(tmp_path / "lib" / "two\tcolumns\nand a row.mkv")
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: [awkward])
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(Job(path), None, Status.WOULD_FIX, "reorder streams"),
    )
    sweep(dry_run=True)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert len(rows) == 1
    assert rows[0].split("\t")[2] == awkward.replace("\t", " ").replace("\n", " ")


def test_a_worker_raising_does_not_abandon_the_sweep(monkeypatch, tmp_path):
    """One unforeseen error must not cost every file queued behind it."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    _library(tmp_path, monkeypatch, 10)

    def explode(job, dry_run, source="webhook", run=None):
        if job.path.endswith("004.mkv"):
            raise RuntimeError("something nobody predicted")
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", explode)

    counts = sweep(dry_run=True)
    assert counts[Status.FAILED] == 1
    assert counts[Status.CONFORM] == 9


@pytest.mark.parametrize("budget", [1, 6])
def test_probing_is_not_serialized_by_the_rewrite_budget(monkeypatch, tmp_path, budget):
    """The rewrite budget is enforced by the slots inside process(), not by
    the judging pool, so a budget of 1 must still probe files concurrently
    (a cold report-only sweep is probe-bound) and a budget above the floor
    must still get a worker per rewrite slot."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", budget)
    _library(tmp_path, monkeypatch, 1)
    captured = {}

    def spying_pool(max_workers, **kwargs):
        captured["workers"] = max_workers
        return ThreadPoolExecutor(max_workers=max_workers, **kwargs)

    monkeypatch.setattr("trackstarr.sweep.ThreadPoolExecutor", spying_pool)
    sweep(dry_run=True)
    assert captured["workers"] == max(_MIN_PROBE_WORKERS, budget)


def test_dry_run_overrides_an_applying_sweep(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", True)
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])

    sweep(dry_run=False)

    (entry,) = list(events.read())
    assert entry["event"] == "sweep"
    assert entry["dry_run"] is True


def test_checkpoints_come_from_time_not_file_count(monkeypatch, tmp_path):
    """An applying sweep can spend minutes on one file; what it learned must
    not wait on a 500-file count that a small library never reaches."""
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
    """os.walk yields nothing for a path that isn't there, which would make a
    wrong mount indistinguishable from an empty library."""
    real = tmp_path / "media"
    real.mkdir()
    (real / "f.mkv").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "gone"), str(real)])

    found = sweep_mod.walk_library(Policy.from_config())
    assert found == [str(real / "f.mkv")]
    assert "media dir" in caplog.text
    assert "does not exist" in caplog.text


def test_the_walk_clears_staged_files_scattered_through_the_library(tmp_path, monkeypatch):
    """Cross-filesystem publishing lands its copy beside the file it replaces,
    so a crash leaves these anywhere. This walk is the only thing that visits
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
    """A library sweep runs for hours. Without a periodic line the log looks
    like it has hung, and there is nothing to judge the rate from."""
    caplog.set_level(logging.INFO, logger="trackstarr.sweep")
    walked = [f"/data/{i:04d}.mkv" for i in range(500)]
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: walked)
    monkeypatch.setattr("trackstarr.sweep.path_index", lambda arrs: {})
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(Job(path), None, Status.CONFORM),
    )

    sweep(dry_run=True)
    assert "500/500" in caplog.text
