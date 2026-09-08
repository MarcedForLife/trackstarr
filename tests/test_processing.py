"""process() outcomes and their side effects. No media, no network."""

import contextlib
import fcntl
import os
import threading
import time

import pytest

from conftest import configured_arr, needed_plan
from trackstarr import config, holds, processing
from trackstarr.executor import Outcome
from trackstarr.media import ProbeError
from trackstarr.planner import OutStream, Plan
from trackstarr.processing import Job, process
from trackstarr.status import Status


def stereo_from(*, generated: bool) -> Plan:
    """A plan over one 5.1 track, either copied or downmixed beside."""
    tracks = [{"index": 0, "kind": "audio", "codec": "eac3", "channels": 6}]
    streams = [OutStream(src=0, kind="audio")]
    if generated:
        streams.append(OutStream(src=0, kind="audio", encode=True, channels=2))
    return needed_plan(tracks=tracks, streams=streams)


def test_a_rewrite_that_moved_no_track_records_nothing_to_compare():
    """A remux or a cleared title leaves the same streams in the same order, so
    a before-and-after is one list twice, on the verdict and on every line of
    the history."""
    assert processing._before(stereo_from(generated=False)) == {}


def test_a_rewrite_records_the_tracks_it_started_from():
    """The file itself is the after and the plan is gone by the time anything
    reads this, so the before is the one thing nothing else could recover."""
    told = processing._before(stereo_from(generated=True))
    assert [track["index"] for track in told["was"]] == [0]
    assert told["added"] == [1]


def test_deferred_rewrite_is_not_a_failure(stub_rewrite):
    """A benign mid-rewrite race must not alert like a corruption."""
    stub_rewrite(
        needed_plan(),
        Outcome.DEFERRED,
        "source changed during the rewrite",
    )
    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "deferred"
    assert "source changed" in result.detail


def test_a_rewritten_file_notifies_media_servers(monkeypatch, stub_rewrite):
    stub_rewrite(needed_plan())
    refreshed = []
    monkeypatch.setattr(processing, "refresh_servers", refreshed.append)

    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "modified"
    assert refreshed == ["/x.mkv"]


def test_a_video_in_a_container_we_never_write_is_its_own_verdict(tmp_path):
    """Skipped put an AVI in with the hardlinked and the silent, which are both
    about the moment. This one is about the file, and the reason travels with
    it."""
    stale = tmp_path / "old.avi"
    stale.write_bytes(b"not really an avi")
    result = process(Job(str(stale)), dry_run=True)
    assert result.status is Status.UNSUPPORTED
    assert result.plan is not None
    assert result.plan.skip == "container .avi not in ALLOWED_EXTS"


def test_a_file_that_is_not_a_video_at_all_is_still_only_skipped(tmp_path):
    """Nothing walks these, but `trackstarr fix` takes a path by hand, and
    "Unsupported" for a text file promises a container that could be added."""
    named = tmp_path / "notes.txt"
    named.write_text("not a film")
    assert process(Job(str(named)), dry_run=True).status is Status.SKIP


def test_report_mode_bottoms_out_in_process(monkeypatch):
    """No caller can rewrite on REWRITE_MODE's bottom rung, whatever dry_run it
    passes."""
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("report mode must not rewrite")
    )
    result = processing.process(Job("/x.mkv"), dry_run=False)
    assert result.status == "pending"


def test_a_held_file_is_planned_and_reported_but_never_rewritten(monkeypatch):
    """The whole point: a title somebody is watching goes on being judged, so
    the library still shows the work, and nothing touches the file."""
    holds.place("/data/media/movies/Dune (2024)", by="marc", reason="watching it")
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/data/media/movies"])
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("a hold must not rewrite")
    )
    result = process(Job("/data/media/movies/Dune (2024)/Dune (2024).mkv"), dry_run=False)
    assert result.status is Status.PENDING
    # The row and pending.tsv say why this one is not being rewritten, since
    # the plan's own reasons would read as work about to happen.
    assert "held by marc" in result.detail
    assert "watching it" in result.detail


def test_a_hold_on_one_title_leaves_the_rest_alone(monkeypatch, stub_rewrite):
    """A hold is not a pause; everything else goes on being rewritten."""
    holds.place("/data/media/movies/Dune (2024)", by="marc")
    stub_rewrite(needed_plan(path="/data/media/movies/Arrival (2016)/Arrival (2016).mkv"))
    result = process(Job("/data/media/movies/Arrival (2016)/Arrival (2016).mkv"), dry_run=False)
    assert result.status is Status.MODIFIED


def held_slot():
    """One claimed rewrite slot; closing the handle releases it, which is
    exactly what process() does around apply_plan."""
    return contextlib.closing(processing._claim_slot())


def _is_locked(name: str) -> bool:
    """Whether another holder has the named slot; closing the probe handle
    releases whatever this took."""
    with open(os.path.join(processing._lock_dir(), name)) as probe_file:
        try:
            fcntl.flock(probe_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def test_rewrites_hold_a_cross_process_file_lock():
    """A sweep run via docker exec must queue behind serve's rewrites."""
    with held_slot():
        assert _is_locked("rewrite.lock.0")
    assert not _is_locked("rewrite.lock.0")


def test_every_slot_gets_its_own_lock_file(monkeypatch):
    """Two processes sharing one lock file would serialize whatever the
    budget says."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    with held_slot(), held_slot():
        held = sorted(
            name
            for name in os.listdir(processing._lock_dir())
            if name.startswith("rewrite.lock.") and _is_locked(name)
        )
    assert held == ["rewrite.lock.0", "rewrite.lock.1"]


def test_all_slots_held_yields_false_while_a_rewrite_runs():
    """Startup's WORK_DIR cleanup relies on this to tell a live rewrite from an
    orphan."""
    with held_slot(), processing.all_slots_held() as held:
        assert held is False
    with processing.all_slots_held() as held:
        assert held is True


def test_a_writable_state_dir_passes_and_is_created():
    assert processing.state_dir_errors() == []
    assert os.path.isdir(config.STATE_DIR), "the check should create STATE_DIR"


def test_an_unusable_state_dir_is_an_error(tmp_path, monkeypatch):
    """A STATE_DIR serve cannot use. A blocker file rather than mode bits, which
    mean nothing to the root the in-image suite runs as."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker))
    errors = processing.state_dir_errors()
    assert len(errors) == 1
    assert "not usable" in errors[0]


def test_a_state_dir_check_tolerates_a_slot_another_process_holds():
    """The question is whether the directory can be written, so a busy slot must
    not read as a broken mount."""
    with held_slot():
        assert processing.state_dir_errors() == []


def test_all_slots_held_sees_slots_beyond_our_budget(monkeypatch):
    """A process started with a bigger budget can hold a slot past our range;
    its lock file exists on disk, so it must be checked too."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    os.makedirs(processing._lock_dir(), exist_ok=True)
    with open(os.path.join(processing._lock_dir(), "rewrite.lock.7"), "w") as foreign:
        fcntl.flock(foreign, fcntl.LOCK_EX)
        with processing.all_slots_held() as held:
            assert held is False


def _peak_concurrency(monkeypatch, jobs: int) -> int:
    """Most rewrite slots held at once across ``jobs`` racing threads, with the
    poll interval shortened."""
    monkeypatch.setattr(processing, "_SLOT_POLL_SECONDS", 0.005)
    running = 0
    peak = 0
    counter_lock = threading.Lock()

    def hold() -> None:
        nonlocal running, peak
        with held_slot():
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


@pytest.mark.parametrize(
    ("budget", "jobs"),
    [(1, 4), (3, 8)],
    ids=["the default is still exclusive", "a raised budget is shared"],
)
def test_the_budget_bounds_concurrent_rewrites(monkeypatch, budget, jobs):
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", budget)
    assert _peak_concurrency(monkeypatch, jobs) == budget


def test_a_raising_rewrite_releases_its_slot(monkeypatch):
    """Leak one lock handle and the pool drains, hanging every later rewrite."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    for _ in range(3):
        with pytest.raises(RuntimeError), held_slot():
            raise RuntimeError("ffmpeg exploded")
    assert _peak_concurrency(monkeypatch, 2) == 1


def test_a_probe_failure_during_a_rewrite_is_reported_not_raised(tmp_path, monkeypatch):
    """One corrupt file must not take the rest of a sweep down with it."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")

    def fail(plan, on_progress=None, on_encoded=None):
        raise ProbeError("moov atom not found")

    monkeypatch.setattr(processing, "apply_plan", fail)
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: needed_plan(str(path)))
    result = process(Job(str(path)), dry_run=False)
    assert result.status is Status.FAILED
    assert "moov atom not found" in result.detail


def test_a_rewritten_file_asks_its_arr_to_rescan(tmp_path, monkeypatch, stub_rewrite):
    """Otherwise Radarr keeps reporting the old size and media info."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    rescanned: list[int] = []

    arr = configured_arr()
    arr.rescan = lambda item_id: rescanned.append(item_id)
    stub_rewrite(needed_plan(str(path)))

    result = process(Job(str(path), "eng", 12, arr), dry_run=False)
    assert result.status is Status.MODIFIED
    assert rescanned == [12]


def test_a_rewrite_waits_for_a_busy_slot_rather_than_failing(monkeypatch):
    """The budget is a queue, not a limit that rejects: an import arriving
    mid-sweep waits its turn."""
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
