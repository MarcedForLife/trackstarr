"""Sweep scheduling and the DRY_RUN latch. The walk and cache behaviour
live in the integration and sweep-cache suites."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from trackstarr import config, events
from trackstarr.processing import Job
from trackstarr.status import Status
from trackstarr.sweep import Judged, seconds_until, sweep


def _library(tmp_path, monkeypatch, count: int) -> list[str]:
    root = tmp_path / "library"
    root.mkdir()
    names = [f"{i:03d}.mkv" for i in range(count)]
    for name in names:
        (root / name).write_text("not really a video")
    monkeypatch.setattr(config, "MEDIA_ROOTS", [str(root)])
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


def test_a_worker_raising_does_not_abandon_the_sweep(monkeypatch, tmp_path):
    """One unforeseen error must not cost every file queued behind it."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    _library(tmp_path, monkeypatch, 10)

    def explode(job, dry_run, source="webhook", run=None):
        if job.path.endswith("004.mkv"):
            raise RuntimeError("something nobody predicted")
        return type("R", (), {"status": Status.CONFORM, "plan": None, "detail": ""})()

    monkeypatch.setattr("trackstarr.sweep.process", explode)

    counts = sweep(dry_run=True)
    assert counts[Status.FAILED] == 1
    assert counts[Status.CONFORM] == 9


def test_dry_run_overrides_an_applying_sweep(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", True)
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", [str(root)])

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


def test_seconds_until_later_today():
    now = time.mktime((2026, 8, 13, 1, 0, 0, 0, 0, -1))
    assert seconds_until("04:00", now) == pytest.approx(3 * 3600)


def test_seconds_until_rolls_to_tomorrow():
    now = time.mktime((2026, 8, 13, 5, 0, 0, 0, 0, -1))
    assert seconds_until("04:00", now) == pytest.approx(23 * 3600)


def test_seconds_until_rejects_garbage():
    with pytest.raises(ValueError):
        seconds_until("not-a-time")
