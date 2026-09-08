"""apply_plan's pre-flight checks. No media needed: every test stops before
ffmpeg would run."""

import contextlib
import errno
import io
import os
import threading
import time
from pathlib import Path

import pytest

from conftest import fake_run, needed_plan, set_layouts
from trackstarr import config, executor
from trackstarr.executor import Outcome, apply_plan, audio_codec_errors, work_dir_errors
from trackstarr.planner import OutStream, Plan, SourceSignature
from trackstarr.policy import Policy


class _StopError(Exception):
    """Raised in place of running ffmpeg, to stop apply_plan mid-flight."""


@pytest.fixture(autouse=True)
def _work_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))


@pytest.fixture(autouse=True)
def _fresh_encoder_list():
    """audio_encoders() is cached for the life of the process, so without
    this the first test to ask pins the real ffmpeg's answer and every
    stand-in below is answered from that cache instead of running."""
    executor.audio_encoders.cache_clear()
    yield
    executor.audio_encoders.cache_clear()


#: What _run_ffmpeg hands back. The rewrites patch that rather than
#: subprocess.run, which apply_plan no longer uses; the encoder list does.
def _ffmpeg_says(monkeypatch, code: int = 0, stderr: str = "") -> None:
    monkeypatch.setattr(
        executor, "_run_ffmpeg", lambda args, on_progress=None, rewriting="": (code, stderr)
    )


def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(
        executor,
        "_run_ffmpeg",
        lambda args, on_progress=None, rewriting="": pytest.fail("ffmpeg must not run"),
    )


def _capture_staged(monkeypatch) -> list[str]:
    """Collect the temp path each rewrite hands ffmpeg, stopping it there."""
    staged: list[str] = []

    def capture(args, on_progress=None, rewriting=""):
        # The last argument is the temp path ffmpeg was told to write.
        staged.append(args[-1])
        raise _StopError

    monkeypatch.setattr(executor, "_run_ffmpeg", capture)
    return staged


def _unremovable(monkeypatch) -> None:
    """A work dir gone read-only, or a file another worker still holds."""

    def refuse(path):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(executor.os, "remove", refuse)


def _exdev(monkeypatch, staged) -> list[tuple[str, str]]:
    """Make os.replace refuse to move ``staged``, as it does across
    filesystems, while every other rename still works. Records the calls."""
    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def replace_across_devices(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        # Only the first hop, out of the "other filesystem", can't cross.
        if str(src) == str(staged):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(executor.os, "replace", replace_across_devices)
    return calls


def test_rewrites_stage_in_the_work_dir(tmp_path, monkeypatch):
    """The point of WORK_DIR is that a half-written rewrite never appears beside
    the file it will replace."""
    staged = _capture_staged(monkeypatch)
    path = tmp_path / "f.mkv"
    path.write_bytes(b"content")
    with contextlib.suppress(_StopError):
        apply_plan(needed_plan(str(path)))

    (entry,) = staged
    assert os.path.dirname(entry) == config.WORK_DIR
    name = os.path.basename(entry)
    # Hidden and not a media extension, for the cross-device landing copy,
    # which does sit in the library for as long as it takes to write.
    assert name.startswith(".trackstarr-")
    assert name.endswith(".partial")
    assert not Policy.from_config().allowed_container(name)


def test_publish_falls_back_to_a_copy_across_filesystems(tmp_path, monkeypatch):
    """The whole point: WORK_DIR on another drive still publishes atomically."""
    library = tmp_path / "library"
    library.mkdir()
    target = library / "f.mkv"
    target.write_bytes(b"old content")
    source_stat = os.stat(target)

    staged = tmp_path / "elsewhere.partial"
    staged.write_bytes(b"new content")

    calls = _exdev(monkeypatch, staged)
    executor._publish(str(staged), str(target), source_stat)

    assert target.read_bytes() == b"new content"
    assert not staged.exists(), "the staged file should be cleaned up"
    assert list(library.iterdir()) == [target], "no landing file left behind"
    # Two hops: the rename that failed, then the one that published the copy.
    assert len(calls) == 2
    assert calls[1][1] == str(target)


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root's DAC_OVERRIDE ignores the mode this turns on, so it would pass "
    "either way; the CI job that gates coverage runs unprivileged",
)
@pytest.mark.parametrize("cross_device", [False, True], ids=["rename", "copy"])
def test_publish_handles_a_read_only_source(tmp_path, monkeypatch, cross_device):
    """A library kept at 0444 is still republished, though the staged file wears
    that mode before the flush and cannot be reopened for writing."""
    library = tmp_path / "library"
    library.mkdir()
    target = library / "f.mkv"
    target.write_bytes(b"old content")
    target.chmod(0o444)
    source_stat = os.stat(target)

    staged = tmp_path / "elsewhere.partial"
    staged.write_bytes(b"new content")
    if cross_device:
        _exdev(monkeypatch, staged)

    executor._publish(str(staged), str(target), source_stat)
    assert target.read_bytes() == b"new content"
    assert target.stat().st_mode & 0o777 == 0o444


def test_publish_does_not_copy_when_a_rename_will_do(tmp_path):
    """Copying every rewrite would double the writing for nothing."""
    target = tmp_path / "f.mkv"
    target.write_bytes(b"old")
    staged = tmp_path / ".trackstarr-x.partial"
    staged.write_bytes(b"new")

    executor._publish(str(staged), str(target), os.stat(target))
    assert target.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [target]


def test_a_failed_landing_copy_leaves_the_original_alone(tmp_path, monkeypatch):
    """A full target drive must not cost the file that was already there."""
    target = tmp_path / "f.mkv"
    target.write_bytes(b"precious")
    staged = tmp_path / "staged.partial"
    staged.write_bytes(b"new content")

    def full_disk(src, dst, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    _exdev(monkeypatch, staged)
    monkeypatch.setattr(executor.shutil, "copyfile", full_disk)

    with pytest.raises(OSError, match="No space"):
        executor._publish(str(staged), str(target), os.stat(target))
    assert target.read_bytes() == b"precious"
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["f.mkv", "staged.partial"]


def test_failed_preflight_leaves_no_staged_file(tmp_path, monkeypatch):
    """The remux-target check returns before the cleanup, so staging any earlier
    left an empty file behind on every collision."""
    _no_ffmpeg(monkeypatch)
    source = tmp_path / "f.mp4"
    source.write_bytes(b"content")
    (tmp_path / "f.mkv").write_text("precious")

    outcome, detail = apply_plan(Plan(path=str(source), remuxing=True))
    assert outcome is Outcome.FAILED
    assert "already exists" in detail
    assert not os.path.isdir(config.WORK_DIR) or not os.listdir(config.WORK_DIR)


def test_clean_work_dir_age_gates_unless_no_rewrite_can_be_running(monkeypatch):
    """serve can restart while a ``sweep --apply`` is mid-rewrite in the same
    WORK_DIR, so a fresh staged file may be its live ffmpeg output. Only proof
    that every slot is free allows clearing those."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 7200)
    work = Path(config.WORK_DIR)
    work.mkdir(parents=True)
    fresh = work / ".trackstarr-fresh.partial"
    stale = work / ".trackstarr-stale.partial"
    keeper = work / "keep-me.mkv"
    for staged_file in (fresh, stale, keeper):
        staged_file.write_text("content")
    old = time.time() - 7201
    os.utime(stale, (old, old))

    executor.clean_work_dir(exclusive=False)
    assert fresh.exists() and keeper.exists()
    assert not stale.exists()

    executor.clean_work_dir(exclusive=True)
    assert not fresh.exists()
    assert keeper.exists(), "files that are not ours stay, whoever holds the slots"


def test_stale_staged_files_are_dropped_but_live_ones_are_not(tmp_path, monkeypatch):
    """Age is the only safe test: a young one may be another worker's."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 7200)
    fresh = tmp_path / ".trackstarr-aaaa.partial"
    stale = tmp_path / ".trackstarr-bbbb.partial"
    fresh.write_text("in flight")
    stale.write_text("orphaned by a crash")
    old = time.time() - 7201
    os.utime(stale, (old, old))

    assert executor.drop_staged(str(fresh)) is False
    assert executor.drop_staged(str(stale)) is True
    assert fresh.exists()
    assert not stale.exists()


def test_concurrent_rewrites_get_their_own_temp_file(tmp_path, monkeypatch):
    """Two rewrites starting in the same second must not share a temp path. A
    name built from the pid and whole seconds gave them one, and whichever
    finished last was renamed over both sources."""
    staged = _capture_staged(monkeypatch)
    for name in ("a.mkv", "b.mkv", "c.mkv"):
        path = tmp_path / name
        path.write_bytes(b"content")
        plan = needed_plan(str(path), src_signature=SourceSignature.of(os.stat(path)))
        with contextlib.suppress(_StopError):
            apply_plan(plan)

    assert len(staged) == 3
    assert len(set(staged)) == 3, f"temp paths collided: {staged}"


def test_stale_plan_is_deferred_before_ffmpeg(tmp_path, monkeypatch):
    """A plan whose file changed while it waited on the lock describes streams
    that no longer exist."""
    _no_ffmpeg(monkeypatch)
    path = tmp_path / "f.mkv"
    path.write_bytes(b"planned content")
    plan = needed_plan(str(path), src_signature=SourceSignature.of(os.stat(path)))
    path.write_bytes(b"rewritten by someone else")

    outcome, detail = apply_plan(plan)
    assert outcome is Outcome.DEFERRED
    assert "since it was planned" in detail


def test_matching_source_passes_the_staleness_check(tmp_path, monkeypatch):
    path = tmp_path / "f.mkv"
    path.write_bytes(b"content")
    plan = needed_plan(str(path), src_signature=SourceSignature.of(os.stat(path)))

    ran = []
    monkeypatch.setattr(
        executor,
        "_run_ffmpeg",
        lambda args, on_progress=None, rewriting="": ran.append(args) or (1, "boom"),
    )
    outcome, detail = apply_plan(plan)
    assert ran, "the rewrite should have been attempted"
    assert outcome is Outcome.FAILED
    assert "ffmpeg failed" in detail


def test_ffmpeg_stderr_in_the_detail_is_bounded(tmp_path, monkeypatch):
    """A damaged file can make ffmpeg log per-packet noise; the fatal message at
    the tail is what matters."""
    path = tmp_path / "f.mkv"
    path.write_bytes(b"content")
    noise = "deprecated pixel format used\n" * 1000 + "final: everything broke"
    _ffmpeg_says(monkeypatch, code=1, stderr=noise)

    outcome, detail = apply_plan(needed_plan(str(path)))
    assert outcome is Outcome.FAILED
    assert "everything broke" in detail
    assert len(detail) < executor._STDERR_TAIL + 100


@pytest.mark.parametrize(
    "elsewhere", [False, True], ids=["beside the library", "on another drive"]
)
def test_a_writable_work_dir_passes_wherever_it_lives(tmp_path, monkeypatch, elsewhere):
    """Which filesystem it is on deliberately doesn't matter; the refusal this
    replaced ruled out every multi-drive library."""
    media = ["/mnt/disk1/movies", "/mnt/disk2/tv"] if elsewhere else [str(tmp_path)]
    monkeypatch.setattr(config, "MEDIA_DIRS", media)
    assert work_dir_errors() == []
    assert os.path.isdir(config.WORK_DIR), "the check should create WORK_DIR"


def test_unusable_work_dir_is_an_error(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    monkeypatch.setattr(config, "WORK_DIR", str(blocker))
    errors = work_dir_errors()
    assert len(errors) == 1
    assert "not usable" in errors[0]


ENCODERS_OUTPUT = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              H.264 / AVC / MPEG-4 AVC
 A....D aac                  AAC (Advanced Audio Coding)
 A....D ac3                  ATSC A/52A (AC-3)
"""


def _fake_encoders(monkeypatch):
    result = fake_run(stdout=ENCODERS_OUTPUT)
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: result)


def test_the_shipped_encoders_pass(monkeypatch):
    """The two a fresh install inherits, aac for 2.0 and ac3 for 5.1."""
    _fake_encoders(monkeypatch)
    assert audio_codec_errors() == []


@pytest.mark.parametrize("codec", ["acc", "libx264"], ids=["a typo", "a video encoder"])
def test_an_audio_codec_ffmpeg_cannot_encode_is_refused(monkeypatch, codec):
    """A bad codec has to fail the restart that introduced it, not the first
    rewrite hours later."""
    _fake_encoders(monkeypatch)
    set_layouts(monkeypatch, "2.0:aac:320k", f"5.1:{codec}:640k")
    errors = audio_codec_errors()
    assert len(errors) == 1
    assert repr(codec) in errors[0]
    # Named by the variable to go and fix, not by the encoder, since each
    # layout states its own and only one of them is wrong.
    assert "makes 5.1 with" in errors[0]


def test_every_layout_missing_an_encoder_is_named(monkeypatch):
    """One line each: two layouts on a typo'd encoder are two variables to fix."""
    _fake_encoders(monkeypatch)
    set_layouts(monkeypatch, "2.0:acc:320k", "5.1:acc:640k")
    errors = audio_codec_errors()
    assert len(errors) == 2
    assert {error.split(" with ")[0] for error in errors} == {
        "AUDIO_LAYOUTS makes 2.0",
        "AUDIO_LAYOUTS makes 5.1",
    }


def test_missing_ffmpeg_is_not_this_checks_problem(monkeypatch):
    def no_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(executor.subprocess, "run", no_ffmpeg)
    assert audio_codec_errors() == []


def test_a_staged_file_that_cannot_be_removed_is_left_alone(tmp_path, monkeypatch, caplog):
    """Another worker may hold it, or the work dir may have gone read-only. This
    runs at startup either way and must not stop the service coming up."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 0)
    staged = tmp_path / ".trackstarr-cccc.partial"
    staged.write_text("orphaned")

    _unremovable(monkeypatch)
    assert executor.drop_staged(str(staged)) is False
    assert "could not remove staged file" in caplog.text
    assert staged.exists()


def test_a_vanished_staged_file_is_not_an_error(tmp_path, monkeypatch):
    """Two workers can clean the same orphan; the loser sees it already gone."""
    assert executor.drop_staged(str(tmp_path / "never-existed.partial")) is False


def test_an_unreachable_work_dir_is_not_reported_as_remote(monkeypatch):
    """Only used for a startup note, so an answer it cannot work out has to be
    the quiet one. work_dir_errors is what refuses."""
    monkeypatch.setattr(config, "WORK_DIR", "/definitely/not/here")
    assert executor.work_dir_is_remote() is False


def test_a_work_dir_that_cannot_be_staged_in_fails_the_plan(tmp_path, monkeypatch):
    """Nothing has been written yet, so the detail names the directory."""
    _no_ffmpeg(monkeypatch)

    def refuse(directory):
        raise OSError("no space left on device")

    monkeypatch.setattr(executor, "_new_temp", refuse)
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")

    outcome, detail = apply_plan(needed_plan(str(source)))
    assert outcome is Outcome.FAILED
    assert config.WORK_DIR in detail
    assert "no space left on device" in detail


def test_an_ffmpeg_timeout_is_a_failure_naming_the_limit(tmp_path, monkeypatch):
    """A wedged encode on one file must not stall a whole sweep silently."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 900)

    def hang(args, on_progress=None, rewriting=""):
        raise executor.subprocess.TimeoutExpired(cmd="ffmpeg", timeout=900)

    monkeypatch.setattr(executor, "_run_ffmpeg", hang)
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")

    outcome, detail = apply_plan(needed_plan(str(source)))
    assert outcome is Outcome.FAILED
    assert "timed out after 900s" in detail
    # The partial encode must not be left behind for the next sweep to find.
    assert not os.listdir(config.WORK_DIR)


def test_a_publish_failure_that_is_not_cross_device_is_raised(tmp_path, monkeypatch):
    """EXDEV is the one os.replace failure with a fallback. A full disk or a
    read-only mount has to surface."""

    def refuse(src, dst):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(executor.os, "replace", refuse)
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")
    staged = tmp_path / "staged.partial"
    staged.write_bytes(b"rewritten")

    with pytest.raises(OSError, match="permission denied"):
        executor._publish(str(staged), str(source), os.stat(source))


def test_verification_catches_a_truncated_result():
    """The commonest bad rewrite: ffmpeg exits 0 having written a fraction of the
    file, and publishing that destroys the source."""
    plan = Plan(path="f.mkv", src_duration=3600.0)
    problem = executor._verify(plan, {"format": {"duration": "120.0"}, "streams": []})
    assert problem is not None
    assert "duration mismatch" in problem


def test_verification_allows_a_little_drift():
    """Container timestamps move by fractions of a second on a remux."""
    plan = Plan(path="f.mkv", src_duration=3600.0)
    assert executor._verify(plan, {"format": {"duration": "3600.4"}, "streams": []}) is None


def test_verification_catches_a_missing_stream():
    """A dropped track is silent otherwise: the file plays, just without the audio
    somebody wanted kept."""
    plan = Plan(path="f.mkv")
    plan.streams.extend([OutStream(src=0, kind="video"), OutStream(src=1, kind="audio")])
    problem = executor._verify(plan, {"format": {}, "streams": [{"index": 0}]})
    assert problem is not None
    assert "stream count mismatch: expected 2, got 1" in problem


def test_a_result_that_fails_verification_is_discarded(tmp_path, monkeypatch):
    """The source must still be there afterwards, untouched."""
    monkeypatch.setattr(executor, "_verify", lambda plan, info: "duration mismatch: 10s -> 1s")
    _ffmpeg_says(monkeypatch)
    monkeypatch.setattr(executor, "probe", lambda path: {"format": {}, "streams": []})
    source = tmp_path / "f.mkv"
    source.write_bytes(b"original")

    outcome, detail = apply_plan(needed_plan(str(source)))
    assert outcome is Outcome.FAILED
    assert "result discarded" in detail
    assert source.read_bytes() == b"original"


def test_an_unremovable_remux_source_is_only_a_warning(tmp_path, monkeypatch, caplog):
    """The .mkv is already published, so a leftover .mp4 is untidy, not a failure."""
    _ffmpeg_says(monkeypatch)
    monkeypatch.setattr(executor, "_verify", lambda plan, info: None)
    monkeypatch.setattr(executor, "probe", lambda path: {"format": {}, "streams": []})
    source = tmp_path / "f.mp4"
    source.write_bytes(b"content")

    real_remove = executor.os.remove

    def refuse(path):
        if path == str(source):
            raise PermissionError("read-only file system")
        real_remove(path)

    monkeypatch.setattr(executor.os, "remove", refuse)
    outcome, _ = apply_plan(
        Plan(path=str(source), remuxing=True, reasons=["remux to mkv (RULE_REMUX)"])
    )
    assert outcome is Outcome.APPLIED
    assert "could not remove" in caplog.text


def test_an_encoder_list_that_cannot_be_read_is_not_an_error(monkeypatch):
    """ffmpeg answering non-zero to -encoders says nothing about the codec, so the
    check declines to guess."""
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: fake_run(returncode=1))
    assert audio_codec_errors() == []


def test_a_work_dir_beside_the_library_is_not_remote(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    os.makedirs(config.WORK_DIR, exist_ok=True)
    assert executor.work_dir_is_remote() is False


def test_cleaning_an_absent_work_dir_does_nothing(monkeypatch):
    """serve calls this before anything creates the directory."""
    monkeypatch.setattr(config, "WORK_DIR", str(Path("/definitely/not/here")))
    executor.clean_work_dir()


def test_an_exclusive_clean_warns_rather_than_stopping_startup(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))
    (tmp_path / ".trackstarr-dddd.partial").write_text("orphaned")

    _unremovable(monkeypatch)
    executor.clean_work_dir(exclusive=True)
    assert "could not remove" in caplog.text


def test_a_rewrite_in_flight_can_be_stopped(tmp_path, monkeypatch):
    """The activity page's "stop rewrites now". A real child process, since
    what is being tested is that the registry can reach one and signal it."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 60)
    result: list[tuple[int, str]] = []
    running = threading.Thread(
        target=lambda: result.append(executor._run_ffmpeg(["sleep", "30"])), daemon=True
    )
    running.start()
    # Registered by the time it is running, or the button would be a no-op on
    # exactly the rewrite somebody is trying to stop.
    for _ in range(200):
        if executor._running_ffmpeg:
            break
        time.sleep(0.01)

    assert executor.terminate_running() == 1
    running.join(timeout=10)
    (code, _) = result[0]
    assert code < 0, "signalled, not a clean exit"
    # And it lets go, so a later abort does not signal a process that has gone.
    assert executor.terminate_running() == 0


def test_a_stopped_rewrite_is_deferred_rather_than_failed(tmp_path, monkeypatch):
    """Nothing is wrong with the file and the next pass will rewrite it, so a
    stop must not alert like a corruption or fail a sweep's exit code."""
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")
    _ffmpeg_says(monkeypatch, code=-15)

    outcome, detail = apply_plan(needed_plan(str(source)))
    assert outcome is Outcome.DEFERRED
    assert "stopped" in detail
    assert source.read_bytes() == b"content"
    # The partial goes with it, rather than waiting on the age gate.
    assert not os.listdir(config.WORK_DIR)


def _open_fds() -> set[int]:
    """Every descriptor this process holds, for the two leak checks below.

    /dev/fd rather than /proc/self/fd, which macOS has no equivalent of: on
    Linux the first is a symlink to the second, so both read the same list and
    the checks run wherever the suite does.
    """
    return {int(name) for name in os.listdir("/dev/fd")}


def test_a_spawn_that_never_happened_leaves_no_pipe_behind():
    """The progress pipe is made before ffmpeg is, so a failed spawn has two
    descriptors to give back. One rewrite per delivery, and a leak here runs
    the whole process out of them."""
    before = _open_fds()
    with pytest.raises(OSError):
        executor._run_ffmpeg(["/nonexistent/ffmpeg"], on_progress=lambda done, speed: None)
    assert _open_fds() == before


def test_a_child_nothing_could_reach_is_killed_rather_than_left_writing(monkeypatch):
    """Thread exhaustion between spawn and registration. Unregistered, the child
    is unreachable and still writing into a file about to be deleted."""

    class NeverStarts:
        def __init__(self, *args, **kwargs):
            """Takes what threading.Thread takes, and does none of it."""

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    before = _open_fds()
    monkeypatch.setattr(executor.threading, "Thread", NeverStarts)
    with pytest.raises(RuntimeError):
        executor._run_ffmpeg(["sleep", "30"], on_progress=lambda done, speed: None)

    assert executor._running_ffmpeg == {}
    assert _open_fds() == before


def test_a_wedged_process_is_killed_at_the_timeout(monkeypatch):
    """communicate() only stops waiting; without the kill the child would go
    on holding the CPU the timeout was meant to take back."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 0.2)
    with pytest.raises(executor.subprocess.TimeoutExpired):
        executor._run_ffmpeg(["sleep", "30"])
    assert executor._running_ffmpeg == {}, "and it is no longer abortable"


def test_the_progress_readout_reaches_the_callback():
    """ffmpeg reports the time and the speed on separate lines, so nothing may
    be passed on until the block's own end line says the pair is complete."""
    seen: list[tuple[float, float]] = []
    executor._watch_progress(
        io.StringIO(
            "frame=120\nout_time_us=5000000\nspeed=12.5x\nprogress=continue\n"
            "frame=240\nout_time_us=9500000\nspeed=13x\nprogress=end\n"
        ),
        lambda done, speed: seen.append((done, speed)),
    )
    assert seen == [(5.0, 12.5), (9.5, 13.0)]


def test_a_readout_that_cannot_be_parsed_is_dropped_rather_than_raised():
    """ffmpeg reports N/A before the first packet is written, and a readout is
    never worth a rewrite: the pipe has to go on being drained either way, or
    ffmpeg blocks writing to it and the encode dies at the timeout."""
    seen: list[tuple[float, float]] = []
    executor._watch_progress(
        io.StringIO(
            "out_time_us=N/A\nspeed=N/A\nprogress=continue\n"
            "out_time_us=3000000\nspeed=4x\nprogress=continue\n"
        ),
        lambda done, speed: seen.append((done, speed)),
    )
    # The first block still reports, with the nothing it knew at the time.
    assert seen == [(0.0, 0.0), (3.0, 4.0)]


def test_a_callback_that_throws_never_stops_the_encode():
    """Same reason, one step further out: whatever the page's end of this does
    with the numbers, the pipe keeps draining and the rewrite runs on."""
    seen: list[float] = []

    def throw_once(done: float, speed: float) -> None:
        if not seen:
            seen.append(done)
            raise RuntimeError("the overview fell over")
        seen.append(done)

    executor._watch_progress(
        io.StringIO(
            "out_time_us=1000000\nprogress=continue\nout_time_us=2000000\nprogress=end\n"
        ),
        throw_once,
    )
    assert seen == [1.0, 2.0]


def test_the_encode_is_declared_over_before_the_file_is_published(tmp_path, monkeypatch):
    """The verify probe and a cross-device copy come after ffmpeg exits, and a
    readout would otherwise report them as an encode pinned at 100%."""
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")
    _ffmpeg_says(monkeypatch)
    monkeypatch.setattr(executor, "probe", lambda path: {})
    monkeypatch.setattr(executor, "_verify", lambda plan, info: None)

    order: list[str] = []
    monkeypatch.setattr(
        executor,
        "_publish",
        lambda tmp, out_path, src: order.append("published"),
    )
    outcome, _ = apply_plan(
        needed_plan(str(source)), on_encoded=lambda: order.append("encoded")
    )
    assert outcome is Outcome.APPLIED
    assert order == ["encoded", "published"]
