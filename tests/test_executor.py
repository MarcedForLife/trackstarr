"""apply_plan's pre-flight checks. No media needed: every test stops before
ffmpeg would run."""

import contextlib
import errno
import os
import time
from pathlib import Path

import pytest

from conftest import fake_run
from trackstarr import config, executor
from trackstarr.executor import Outcome, apply_plan, audio_codec_errors, work_dir_errors
from trackstarr.planner import OutStream, Plan, SourceSignature
from trackstarr.policy import Policy


class _StopError(Exception):
    """Raised in place of running ffmpeg, to stop apply_plan mid-flight."""


@pytest.fixture(autouse=True)
def _work_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))


def test_rewrites_stage_in_the_work_dir(tmp_path, monkeypatch):
    """Not in the library: the point of WORK_DIR is that a half-written
    rewrite never appears beside the file it will replace."""
    staged: list[str] = []

    def capture(args, **kwargs):
        staged.append(args[-1])
        raise _StopError

    monkeypatch.setattr(executor.subprocess, "run", capture)
    path = tmp_path / "f.mkv"
    path.write_bytes(b"content")
    with contextlib.suppress(_StopError):
        apply_plan(Plan(path=str(path), reasons=["reorder streams"]))

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

    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def replace_across_devices(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        # Only the first hop, out of the "other filesystem", can't cross.
        if str(src) == str(staged):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(executor.os, "replace", replace_across_devices)
    executor._publish(str(staged), str(target), source_stat)

    assert target.read_bytes() == b"new content"
    assert not staged.exists(), "the staged file should be cleaned up"
    assert list(library.iterdir()) == [target], "no landing file left behind"
    # Two hops: the rename that failed, then the one that published the copy.
    assert len(calls) == 2
    assert calls[1][1] == str(target)


def test_publish_does_not_copy_when_a_rename_will_do(tmp_path):
    """The free path has to stay free; copying every rewrite would double
    the writing for nothing."""
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

    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        if str(src) == str(staged):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *args, **kwargs)

    def full_disk(src, dst, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(executor.os, "replace", replace)
    monkeypatch.setattr(executor.shutil, "copyfile", full_disk)

    with pytest.raises(OSError, match="No space"):
        executor._publish(str(staged), str(target), os.stat(target))
    assert target.read_bytes() == b"precious"
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["f.mkv", "staged.partial"]


def test_failed_preflight_leaves_no_staged_file(tmp_path, monkeypatch):
    """The remux-target check returns before the cleanup, so staging any
    earlier left an empty file behind on every collision."""
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    _no_ffmpeg(monkeypatch)
    source = tmp_path / "f.mp4"
    source.write_bytes(b"content")
    (tmp_path / "f.mkv").write_text("precious")

    outcome, detail = apply_plan(Plan(path=str(source)))
    assert outcome is Outcome.FAILED
    assert "already exists" in detail
    assert not os.path.isdir(config.WORK_DIR) or not os.listdir(config.WORK_DIR)


def test_clean_work_dir_age_gates_unless_no_rewrite_can_be_running(monkeypatch):
    """serve can restart while a ``sweep --apply`` in another process is
    mid-rewrite in the same WORK_DIR; a fresh staged file may be its live
    ffmpeg output, so only proof that every rewrite slot is free (exclusive)
    allows clearing them unconditionally."""
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

    assert executor.drop_if_stale(str(fresh)) is False
    assert executor.drop_if_stale(str(stale)) is True
    assert fresh.exists()
    assert not stale.exists()


def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(
        executor.subprocess, "run", lambda *a, **k: pytest.fail("ffmpeg must not run")
    )


def test_concurrent_rewrites_get_their_own_temp_file(tmp_path, monkeypatch):
    """Two rewrites starting in the same second must not share a temp path.

    A name built from the pid and whole seconds gave them one: both ffmpegs
    wrote to it and whichever finished last was renamed over both sources.
    Invisible while rewrites were serialized, data loss once they aren't.
    """
    staged: list[str] = []

    def capture(args, **kwargs):
        # The last argument is the temp path ffmpeg was told to write.
        staged.append(args[-1])
        raise _StopError

    monkeypatch.setattr(executor.subprocess, "run", capture)

    for name in ("a.mkv", "b.mkv", "c.mkv"):
        path = tmp_path / name
        path.write_bytes(b"content")
        plan = Plan(
            path=str(path),
            reasons=["reorder streams"],
            src_signature=SourceSignature.of(os.stat(path)),
        )
        with contextlib.suppress(_StopError):
            apply_plan(plan)

    assert len(staged) == 3
    assert len(set(staged)) == 3, f"temp paths collided: {staged}"


def test_stale_plan_is_deferred_before_ffmpeg(tmp_path, monkeypatch):
    """A plan whose file changed while it waited on the rewrite lock must not
    be applied: its stream maps describe a file that no longer exists."""
    _no_ffmpeg(monkeypatch)
    path = tmp_path / "f.mkv"
    path.write_bytes(b"planned content")
    plan = Plan(
        path=str(path),
        reasons=["reorder streams"],
        src_signature=SourceSignature.of(os.stat(path)),
    )
    path.write_bytes(b"rewritten by someone else")

    outcome, detail = apply_plan(plan)
    assert outcome is Outcome.DEFERRED
    assert "since it was planned" in detail


def test_matching_source_passes_the_staleness_check(tmp_path, monkeypatch):
    path = tmp_path / "f.mkv"
    path.write_bytes(b"content")
    plan = Plan(
        path=str(path),
        reasons=["reorder streams"],
        src_signature=SourceSignature.of(os.stat(path)),
    )

    ran = []
    fake = fake_run(returncode=1, stderr="boom")
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: ran.append(a) or fake)
    outcome, detail = apply_plan(plan)
    assert ran, "the rewrite should have been attempted"
    assert outcome is Outcome.FAILED
    assert "ffmpeg failed" in detail


def test_ffmpeg_stderr_in_the_detail_is_bounded(tmp_path, monkeypatch):
    """A damaged file can make ffmpeg log per-packet noise; the fatal message
    at the tail is what matters, and the detail lands in events.jsonl whole."""
    path = tmp_path / "f.mkv"
    path.write_bytes(b"content")
    noise = "deprecated pixel format used\n" * 1000 + "final: everything broke"
    fake = fake_run(returncode=1, stderr=noise)
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: fake)

    outcome, detail = apply_plan(Plan(path=str(path), reasons=["reorder streams"]))
    assert outcome is Outcome.FAILED
    assert "everything broke" in detail
    assert len(detail) < executor._STDERR_TAIL + 100


def test_work_dir_on_the_same_filesystem_is_fine(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    assert work_dir_errors() == []
    assert os.path.isdir(config.WORK_DIR), "the check should create WORK_DIR"


def test_a_work_dir_on_another_filesystem_is_allowed(tmp_path, monkeypatch):
    """The refusal this replaced ruled out every multi-drive library."""
    monkeypatch.setattr(config, "MEDIA_DIRS", ["/mnt/disk1/movies", "/mnt/disk2/tv"])
    assert work_dir_errors() == []


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


def test_a_known_audio_codec_passes(monkeypatch):
    _fake_encoders(monkeypatch)
    monkeypatch.setattr(config, "AUDIO_CODEC", "aac")
    assert audio_codec_errors() == []


def test_a_typoed_audio_codec_is_refused(monkeypatch):
    """A bad codec must fail the restart that introduced it, not the first
    rewrite hours later."""
    _fake_encoders(monkeypatch)
    monkeypatch.setattr(config, "AUDIO_CODEC", "acc")
    errors = audio_codec_errors()
    assert len(errors) == 1
    assert "'acc'" in errors[0]


def test_a_video_codec_is_not_an_audio_encoder(monkeypatch):
    _fake_encoders(monkeypatch)
    monkeypatch.setattr(config, "AUDIO_CODEC", "libx264")
    assert len(audio_codec_errors()) == 1


def test_missing_ffmpeg_is_not_this_checks_problem(monkeypatch):
    def no_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(executor.subprocess, "run", no_ffmpeg)
    assert audio_codec_errors() == []


def test_a_staged_file_that_cannot_be_removed_is_left_alone(tmp_path, monkeypatch, caplog):
    """Another worker may hold it, or the work dir may have gone read-only.
    Either way this runs at startup and must not stop the service coming up."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 0)
    staged = tmp_path / ".trackstarr-cccc.partial"
    staged.write_text("orphaned")

    def refuse(path):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(executor.os, "remove", refuse)
    assert executor.drop_if_stale(str(staged)) is False
    assert "could not remove stale staged file" in caplog.text
    assert staged.exists()


def test_a_vanished_staged_file_is_not_an_error(tmp_path, monkeypatch):
    """Two workers can clean the same orphan; the loser sees it already gone."""
    assert executor.drop_if_stale(str(tmp_path / "never-existed.partial")) is False


def test_an_unreachable_work_dir_is_not_reported_as_remote(monkeypatch):
    """Only used to log a note at startup, so an answer it cannot work out
    has to be the quiet one. work_dir_errors is what actually refuses."""
    monkeypatch.setattr(config, "WORK_DIR", "/definitely/not/here")
    assert executor.work_dir_is_remote() is False


def test_a_work_dir_that_cannot_be_staged_in_fails_the_plan(tmp_path, monkeypatch):
    """Distinct from a failing ffmpeg: nothing has been written yet, and the
    detail has to name the directory so the cause is obvious."""
    _no_ffmpeg(monkeypatch)

    def refuse(directory):
        raise OSError("no space left on device")

    monkeypatch.setattr(executor, "_new_temp", refuse)
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")

    outcome, detail = apply_plan(Plan(path=str(source), reasons=["reorder streams"]))
    assert outcome is Outcome.FAILED
    assert config.WORK_DIR in detail
    assert "no space left on device" in detail


def test_an_ffmpeg_timeout_is_a_failure_naming_the_limit(tmp_path, monkeypatch):
    """A wedged encode on one file must not stall a whole sweep silently."""
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 900)

    def hang(args, **kwargs):
        raise executor.subprocess.TimeoutExpired(cmd="ffmpeg", timeout=900)

    monkeypatch.setattr(executor.subprocess, "run", hang)
    source = tmp_path / "f.mkv"
    source.write_bytes(b"content")

    outcome, detail = apply_plan(Plan(path=str(source), reasons=["reorder streams"]))
    assert outcome is Outcome.FAILED
    assert "timed out after 900s" in detail
    # The partial encode must not be left behind for the next sweep to find.
    assert not os.listdir(config.WORK_DIR)


def test_a_publish_failure_that_is_not_cross_device_is_raised(tmp_path, monkeypatch):
    """EXDEV is the one os.replace failure with a fallback. Anything else —
    a full disk, a read-only mount — must surface, not be papered over."""

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
    """The commonest bad rewrite: ffmpeg exits 0 having written a fraction of
    the file. Publishing that would destroy the source."""
    plan = Plan(path="f.mkv", src_duration=3600.0)
    problem = executor._verify(plan, {"format": {"duration": "120.0"}, "streams": []})
    assert problem is not None
    assert "duration mismatch" in problem


def test_verification_allows_a_little_drift():
    """Container timestamps move by fractions of a second on a remux."""
    plan = Plan(path="f.mkv", src_duration=3600.0)
    assert executor._verify(plan, {"format": {"duration": "3600.4"}, "streams": []}) is None


def test_verification_catches_a_missing_stream():
    """A dropped track is silent otherwise: the file plays, just without the
    audio somebody wanted kept."""
    plan = Plan(path="f.mkv")
    plan.streams.extend([OutStream(src=0, kind="video"), OutStream(src=1, kind="audio")])
    problem = executor._verify(plan, {"format": {}, "streams": [{"index": 0}]})
    assert problem is not None
    assert "stream count mismatch: expected 2, got 1" in problem


def test_a_result_that_fails_verification_is_discarded(tmp_path, monkeypatch):
    """The source must still be there afterwards, untouched."""
    monkeypatch.setattr(executor, "_verify", lambda plan, info: "duration mismatch: 10s -> 1s")
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: fake_run())
    monkeypatch.setattr(executor, "probe", lambda path: {"format": {}, "streams": []})
    source = tmp_path / "f.mkv"
    source.write_bytes(b"original")

    outcome, detail = apply_plan(Plan(path=str(source), reasons=["reorder streams"]))
    assert outcome is Outcome.FAILED
    assert "result discarded" in detail
    assert source.read_bytes() == b"original"


def test_an_unremovable_remux_source_is_only_a_warning(tmp_path, monkeypatch, caplog):
    """The .mkv is already published at this point; leaving the .mp4 behind is
    untidy, not a failed import."""
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: fake_run())
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
    outcome, _ = apply_plan(Plan(path=str(source), reasons=["remux to mkv (REMUX_TO_MKV)"]))
    assert outcome is Outcome.APPLIED
    assert "could not remove" in caplog.text


def test_an_encoder_list_that_cannot_be_read_is_not_an_error(monkeypatch):
    """ffmpeg answering non-zero to -encoders says nothing about the codec, so
    the check declines to guess rather than refusing a valid one."""
    monkeypatch.setattr(executor.subprocess, "run", lambda *a, **k: fake_run(returncode=1))
    assert audio_codec_errors() == []


def test_a_work_dir_beside_the_library_is_not_remote(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
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

    def refuse(path):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(executor.os, "remove", refuse)
    executor.clean_work_dir(exclusive=True)
    assert "could not remove" in caplog.text
