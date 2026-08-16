"""process() outcomes and their side effects. No media, no network."""

import fcntl
import os
import threading
import time
from dataclasses import replace

import pytest

from trackstarr import config, processing
from trackstarr.arr import radarr
from trackstarr.executor import Outcome
from trackstarr.media import ProbeError
from trackstarr.planner import Plan
from trackstarr.processing import Job, process
from trackstarr.status import Status


def test_deferred_rewrite_is_not_a_failure(monkeypatch):
    """A benign mid-rewrite race must not alert like a corruption."""
    plan = Plan(path="/x.mkv", reasons=["reorder streams"])
    monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
    monkeypatch.setattr(
        processing,
        "apply_plan",
        lambda plan: (Outcome.DEFERRED, "source changed during the rewrite"),
    )
    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "deferred"
    assert "source changed" in result.detail


def test_fixed_file_notifies_media_servers(monkeypatch):
    plan = Plan(path="/x.mkv", reasons=["reorder streams"])
    monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
    monkeypatch.setattr(processing, "apply_plan", lambda plan: (Outcome.APPLIED, ""))
    refreshed = []
    monkeypatch.setattr(processing, "refresh_servers", refreshed.append)

    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "fixed"
    assert refreshed == ["/x.mkv"]


def test_global_dry_run_bottoms_out_in_process(monkeypatch):
    """No caller can rewrite under DRY_RUN, whatever dry_run it passes."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    plan = Plan(path="/x.mkv", reasons=["reorder streams"])
    monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("DRY_RUN must not rewrite")
    )
    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "would-fix"


def test_rewrites_hold_a_cross_process_file_lock():
    """A sweep run via docker exec must queue behind serve's rewrites."""
    lock_path = os.path.join(config.STATE_DIR, "rewrite.lock.0")
    with (
        processing._exclusive_rewrite(),
        open(lock_path) as probe_file,
        pytest.raises(BlockingIOError),
    ):
        fcntl.flock(probe_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(lock_path) as probe_file:
        fcntl.flock(probe_file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _is_locked(name: str) -> bool:
    with open(os.path.join(config.STATE_DIR, name)) as probe_file:
        try:
            fcntl.flock(probe_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def test_every_slot_gets_its_own_lock_file(monkeypatch):
    """Two processes sharing one lock file would serialize whatever the
    budget says."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    with processing._exclusive_rewrite(), processing._exclusive_rewrite():
        held = sorted(
            name
            for name in os.listdir(config.STATE_DIR)
            if name.startswith("rewrite.lock.") and _is_locked(name)
        )
    assert held == ["rewrite.lock.0", "rewrite.lock.1"]


def test_all_slots_held_yields_false_while_a_rewrite_runs():
    """Startup's WORK_DIR cleanup relies on this to know whether a staged
    file could still be another process's live rewrite."""
    with processing._exclusive_rewrite(), processing.all_slots_held() as held:
        assert held is False
    with processing.all_slots_held() as held:
        assert held is True


def test_all_slots_held_sees_slots_beyond_our_budget(monkeypatch):
    """A process started with a bigger budget can hold a slot past our range;
    its lock file exists on disk, so it must be checked too."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(os.path.join(config.STATE_DIR, "rewrite.lock.7"), "w") as foreign:
        fcntl.flock(foreign, fcntl.LOCK_EX)
        with processing.all_slots_held() as held:
            assert held is False


def _peak_concurrency(jobs: int) -> int:
    """Most rewrite slots held at once across ``jobs`` racing threads."""
    running = 0
    peak = 0
    counter_lock = threading.Lock()

    def hold() -> None:
        nonlocal running, peak
        with processing._exclusive_rewrite():
            with counter_lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with counter_lock:
                running -= 1

    threads = [threading.Thread(target=hold) for _ in range(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return peak


def test_one_is_still_one(monkeypatch):
    """The default has to behave exactly as the old exclusive lock did."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    assert _peak_concurrency(4) == 1


def test_budget_is_shared_and_bounded(monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    assert _peak_concurrency(8) == 3


def test_a_raising_rewrite_releases_its_slot(monkeypatch):
    """Leak one and the budget bleeds to zero, hanging every later rewrite."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    for _ in range(3):
        with pytest.raises(RuntimeError), processing._exclusive_rewrite():
            raise RuntimeError("ffmpeg exploded")
    assert processing._running == 0
    assert _peak_concurrency(2) == 1


def test_a_probe_failure_during_a_rewrite_is_reported_not_raised(tmp_path, monkeypatch):
    """One corrupt file must not take the rest of a sweep down with it."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")

    def fail(plan):
        raise ProbeError("moov atom not found")

    monkeypatch.setattr(processing, "apply_plan", fail)
    monkeypatch.setattr(
        processing, "build_plan", lambda p, lang: Plan(path=str(path), reasons=["reorder"])
    )
    result = process(Job(str(path)), dry_run=False)
    assert result.status is Status.FAILED
    assert "moov atom not found" in result.detail


def test_a_fixed_file_asks_its_arr_to_rescan(tmp_path, monkeypatch):
    """Otherwise Radarr keeps reporting the old size and media info."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    rescanned: list[int] = []

    arr = replace(radarr(), url="http://radarr:7878", key="key")
    monkeypatch.setattr(type(arr), "rescan", lambda self, item_id: rescanned.append(item_id))
    monkeypatch.setattr(
        processing, "build_plan", lambda p, lang: Plan(path=str(path), reasons=["reorder"])
    )
    monkeypatch.setattr(processing, "apply_plan", lambda plan: (Outcome.APPLIED, ""))

    result = process(Job(str(path), "eng", 12, arr), dry_run=False)
    assert result.status is Status.FIXED
    assert rescanned == [12]


def test_a_rewrite_waits_for_a_busy_slot_rather_than_failing(tmp_path, monkeypatch):
    """The budget is a queue, not a limit that rejects: a webhook import
    arriving mid-sweep waits its turn instead of being dropped."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    held = processing._claim_slot()
    released: list[bool] = []

    def release_on_first_wait(seconds):
        """Stand in for the other worker finishing while we poll."""
        if not released:
            released.append(True)
            held.close()

    monkeypatch.setattr(processing.time, "sleep", release_on_first_wait)
    got = processing._claim_slot()
    try:
        assert released == [True]
    finally:
        got.close()
