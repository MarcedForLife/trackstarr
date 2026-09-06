"""Carry out a plan, verifying the result before anything is overwritten."""

import contextlib
import enum
import errno
import functools
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from typing import IO

from . import config
from .command import ffmpeg_args
from .layouts import resolved_layouts
from .media import duration, probe
from .planner import Plan, SourceSignature

log = logging.getLogger(__name__)

#: Staged rewrites are dotfiles so Plex and the *arrs skip them, with no
#: media extension in case something looks anyway.
TEMP_PREFIX = ".trackstarr-"
TEMP_SUFFIX = ".partial"


def _new_temp(directory: str) -> str:
    """Create an empty staging file in ``directory`` and return its path.

    mkstemp, not pid plus clock: two rewrites starting in one second would
    share the name.
    """
    os.makedirs(directory, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=directory)
    os.close(handle)
    return path


def _adopt(path: str, source: os.stat_result) -> None:
    """Give a staged file the mode and ownership of the file it replaces."""
    os.chmod(path, source.st_mode & 0o7777)
    # Only root can chown to a different uid; as the media owner it is already
    # right.
    with contextlib.suppress(PermissionError):
        os.chown(path, source.st_uid, source.st_gid)


def _flush(path: str) -> None:
    """fsync a staged file before it is renamed, or a crash after the rename
    could leave the final name on half a file.

    Opened read-only: the file already wears the mode of the one it replaces,
    which may be 0444, and fsync flushes the inode either way.
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(tmp: str, out_path: str, source: os.stat_result) -> None:
    """Move the finished rewrite into place atomically, from any filesystem.

    os.replace is tried rather than predicted: mergerfs reports one st_dev for
    a pool of separate filesystems. Across devices the copy lands beside the
    target under a name scanners ignore, then the same rename publishes it.
    """
    _adopt(tmp, source)
    # A flush is only worth something ahead of the rename it protects.
    _flush(tmp)
    try:
        os.replace(tmp, out_path)
        return
    except OSError as err:
        if err.errno != errno.EXDEV:
            raise

    landing = _new_temp(os.path.dirname(out_path))
    try:
        shutil.copyfile(tmp, landing)
        _adopt(landing, source)
        _flush(landing)
        os.replace(landing, out_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(landing)
        raise
    with contextlib.suppress(OSError):
        os.remove(tmp)


class Outcome(enum.StrEnum):
    """What apply_plan did with the library file."""

    APPLIED = "applied"
    #: Nothing wrong with the file, only not safe to replace yet. Separate
    #: from FAILED so a benign race does not alert like a corruption.
    DEFERRED = "deferred"
    FAILED = "failed"


#: How far the result's duration may drift (container timestamp rounding), with
#: a floor for short files.
DURATION_DRIFT_RATIO = 0.005
DURATION_DRIFT_FLOOR = 1.0

#: How much of ffmpeg's stderr a failure keeps. The tail, where the fatal line
#: is.
_STDERR_TAIL = 500

#: The ffmpeg runs this process has going, so an abort can reach them.
_running_ffmpeg: set[subprocess.Popen] = set()
_running_lock = threading.Lock()


def running_count() -> int:
    """How many ffmpeg runs this process has going, so the page can say what
    an abort would waste."""
    with _running_lock:
        return len(_running_ffmpeg)


def terminate_running() -> int:
    """SIGTERM every running ffmpeg; how many were signalled.

    Safe at any moment: a rewrite is staged and published only once verified,
    so a kill costs the encode and never the library file.
    """
    with _running_lock:
        procs = list(_running_ffmpeg)
    for proc in procs:
        # Gone between the snapshot and here is the normal race, not an error.
        with contextlib.suppress(OSError):
            proc.terminate()
    return len(procs)


#: Called about once a second per encode with (seconds written, speed as a
#: multiple of realtime). On its own thread, so it must not block.
ProgressCallback = Callable[[float, float], None]


def _watch_progress(readout: IO[str], on_progress: ProgressCallback) -> None:
    """Turn ffmpeg's ``-progress`` stream into callback calls.

    ffmpeg writes a block of ``key=value`` lines a second, ending with
    ``progress=``; time and speed only mean something together, so the callback
    fires on that last line. Nothing may escape, or the pipe closes under
    ffmpeg.
    """
    done = speed = 0.0
    with readout:
        for line in readout:
            key, _, value = line.strip().partition("=")
            try:
                if key == "out_time_us":
                    done = int(value) / 1_000_000
                elif key == "speed":
                    speed = float(value.removesuffix("x"))
                elif key == "progress":
                    on_progress(done, speed)
            except Exception:
                # Mostly "N/A" before the first packet.
                log.debug("ignoring progress line %r", line.strip(), exc_info=True)


def _run_ffmpeg(
    args: list[str], on_progress: ProgressCallback | None = None
) -> tuple[int, str]:
    """Run ffmpeg to completion; its exit code and stderr.

    Not subprocess.run, which hides the Popen from :func:`terminate_running`.
    Raises TimeoutExpired like run() does, having killed the process.
    ``on_progress`` gets the readout down a pipe of its own, leaving
    ``communicate`` the standard streams.
    """
    read_fd = write_fd = -1
    if on_progress is not None:
        read_fd, write_fd = os.pipe()
        # A global option, so it goes with the others, ahead of the input.
        args = [args[0], "-progress", f"pipe:{write_fd}", *args[1:]]
    proc = None
    readout = None
    # A child started but not registered is unreachable: nothing can terminate
    # it, nobody waits on it, and apply_plan deletes the file it is writing.
    # Thread exhaustion is the realistic way in.
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=() if write_fd < 0 else (write_fd,),
        )
        if on_progress is not None:
            # Close our copy of the writing end, or the reader never sees EOF.
            os.close(write_fd)
            write_fd = -1
            readout = os.fdopen(read_fd)
            read_fd = -1
            threading.Thread(
                target=_watch_progress, args=(readout, on_progress), daemon=True
            ).start()
        with _running_lock:
            _running_ffmpeg.add(proc)
    except BaseException:
        if proc is not None:
            proc.kill()
            proc.communicate()
        if readout is not None:
            readout.close()
        for descriptor in (read_fd, write_fd):
            if descriptor >= 0:
                os.close(descriptor)
        raise
    try:
        _, stderr = proc.communicate(timeout=config.FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        with _running_lock:
            _running_ffmpeg.discard(proc)
    return proc.returncode, stderr or ""


def _verify(plan: Plan, out_info: dict) -> str | None:
    """What is wrong with the rewrite, or None. A truncated or stream-short
    result would silently damage the library."""
    src_dur = plan.src_duration
    out_dur = duration(out_info)
    drift_allowed = max(DURATION_DRIFT_FLOOR, src_dur * DURATION_DRIFT_RATIO)
    if src_dur and abs(out_dur - src_dur) > drift_allowed:
        return f"duration mismatch: {src_dur:.1f}s -> {out_dur:.1f}s"
    out_streams = len(out_info.get("streams") or [])
    if out_streams != len(plan.streams):
        return f"stream count mismatch: expected {len(plan.streams)}, got {out_streams}"
    return None


def apply_plan(
    plan: Plan,
    on_progress: ProgressCallback | None = None,
    on_encoded: Callable[[], None] | None = None,
) -> tuple[Outcome, str]:
    """Rewrite the file, replacing it only once the result verifies.

    The detail says what went wrong when nothing was replaced; the caller
    logs it. ``on_encoded`` fires when ffmpeg exits, before a cross-filesystem
    publish copies the file.
    """
    if plan.out_path != plan.path and os.path.exists(plan.out_path):
        # An .mkv already beside the source is not ours to overwrite. Recurs
        # every sweep, so the detail says what to do.
        return Outcome.FAILED, (
            f"remux target already exists: {plan.out_path} "
            f"(delete {plan.path} if the .mkv is a finished remux, "
            "or delete the .mkv to redo it)"
        )

    src_before = os.stat(plan.path)
    # A stale plan's stream maps could mangle the new file in ways _verify
    # cannot see.
    if plan.src_signature and SourceSignature.of(src_before) != plan.src_signature:
        return Outcome.DEFERRED, "source changed since it was planned, nothing rewritten"

    # Staged after the pre-flight checks, or each return above leaks a file.
    try:
        tmp = _new_temp(config.WORK_DIR)
    except OSError as err:
        return Outcome.FAILED, f"could not stage the rewrite in {config.WORK_DIR}: {err}"

    args = ffmpeg_args(plan, tmp)
    log.info("ffmpeg %s", " ".join(args[1:]))
    try:
        code, stderr = _run_ffmpeg(args, on_progress)
        if on_encoded is not None:
            on_encoded()
        if code < 0:
            # Signalled: someone pressed stop. Deferred, since nothing is wrong
            # with the file.
            return Outcome.DEFERRED, "the rewrite was stopped, nothing rewritten"
        if code != 0:
            stderr_tail = stderr.strip()[-_STDERR_TAIL:]
            return Outcome.FAILED, f"ffmpeg failed ({code}): {stderr_tail}"

        problem = _verify(plan, probe(tmp))
        if problem:
            return Outcome.FAILED, f"{problem}, result discarded"

        # An upgrade can land mid-rewrite; renaming over it would revert it.
        src_after = os.stat(plan.path)
        if SourceSignature.of(src_after) != SourceSignature.of(src_before):
            return Outcome.DEFERRED, "source changed during the rewrite, result discarded"

        _publish(tmp, plan.out_path, src_before)
        if plan.out_path != plan.path:
            # The .mkv is published; a leftover source is the next sweep's.
            try:
                os.remove(plan.path)
            except OSError as err:
                log.warning("could not remove %s after remux: %s", plan.path, err)
        log.info("rewrote %s", plan.out_path)
        return Outcome.APPLIED, ""
    except subprocess.TimeoutExpired:
        return Outcome.FAILED, f"ffmpeg timed out after {config.FFMPEG_TIMEOUT}s"
    finally:
        # Already gone when _publish renamed it.
        with contextlib.suppress(OSError):
            os.remove(tmp)


def work_dir_errors() -> list[str]:
    """Whether WORK_DIR is usable, as ready-to-log messages. Creates it and
    stages a file to prove the mount is writable."""
    try:
        os.makedirs(config.WORK_DIR, exist_ok=True)
        probe_path = _new_temp(config.WORK_DIR)
        os.remove(probe_path)
    except OSError as err:
        return [f"WORK_DIR {config.WORK_DIR} is not usable: {err}"]
    return []


@functools.cache
def audio_encoders() -> frozenset[str] | None:
    """Every audio encoder this ffmpeg carries, or None when it could not be
    asked. Cached, since the answer changes only with the binary."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if out.returncode != 0:
        return None
    names = set()
    for line in out.stdout.splitlines():
        # " A....D aac   AAC (Advanced Audio Coding)": the first flag is the
        # codec type.
        flags, _, rest = line.strip().partition(" ")
        if flags.startswith("A") and (name := rest.split()[:1]):
            names.add(name[0])
    return frozenset(names)


def audio_codec_errors() -> list[str]:
    """Layouts whose encoder this ffmpeg lacks, as ready-to-log messages.

    A typo would otherwise surface as the first rewrite failing. A layout with
    no encoder at all is :func:`trackstarr.policy.errors`' report.
    """
    encoders = audio_encoders()
    if encoders is None:
        return []
    return [
        f"{config.codec_variable(layout.name)}={layout.codec!r} is not an audio encoder "
        "this ffmpeg provides (see ffmpeg -encoders)"
        for layout in resolved_layouts()
        if layout.codec not in encoders
    ]


def work_dir_is_remote() -> bool:
    """Whether publishing will probably copy rather than rename. Best effort,
    for a startup note only: a union filesystem can fool it."""
    try:
        work_dev = os.stat(config.WORK_DIR).st_dev
    except OSError:
        return False
    return any(
        os.stat(media_dir).st_dev != work_dev
        for media_dir in config.MEDIA_DIRS
        if os.path.isdir(media_dir)
    )


def is_staged_file(name: str) -> bool:
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)


def drop_staged(path: str, force: bool = False) -> bool:
    """Remove a staged file left by a crash, if it cannot be in use.

    Anything older than the ffmpeg timeout has outlived its writer. ``force``
    is for a caller holding every rewrite slot, which proves no writer exists.
    """
    try:
        if not force and time.time() - os.stat(path).st_mtime <= config.FFMPEG_TIMEOUT:
            return False
        os.remove(path)
    except OSError as err:
        log.warning("could not remove staged file %s: %s", path, err)
        return False
    log.info("removed staged file %s", path)
    return True


def clean_work_dir(exclusive: bool = False) -> None:
    """Drop staged files orphaned by a restart mid-rewrite.

    ``exclusive`` says the caller holds every rewrite slot, so fresh orphans
    go too. WORK_DIR only; :func:`trackstarr.sweep.walk_library` clears the
    ones cross-filesystem publishing stages beside the target.
    """
    if not config.WORK_DIR or not os.path.isdir(config.WORK_DIR):
        return
    for name in os.listdir(config.WORK_DIR):
        if is_staged_file(name):
            drop_staged(os.path.join(config.WORK_DIR, name), force=exclusive)
