"""Carry out a plan, verifying the result before anything is overwritten."""

import contextlib
import enum
import errno
import logging
import os
import shutil
import subprocess
import tempfile
import time

from . import config
from .command import ffmpeg_args
from .media import duration, probe
from .planner import Plan, SourceSignature

log = logging.getLogger(__name__)

#: Staged rewrites are dotfiles so Plex and the *arrs skip them, with no
#: media extension in case something looks anyway.
TEMP_PREFIX = ".trackstarr-"
TEMP_SUFFIX = ".partial"


def _new_temp(directory: str) -> str:
    """An empty staging file in ``directory``, and its path.

    mkstemp, not a name built from the pid and the clock: two rewrites
    starting in the same second would share it, and the last to finish would
    be renamed over both sources.
    """
    os.makedirs(directory, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=directory)
    os.close(handle)
    return path


def _adopt(path: str, source: os.stat_result) -> None:
    """Give a staged file the mode and ownership of the file it replaces."""
    os.chmod(path, source.st_mode & 0o7777)
    # Already right when running as the media owner, which is normal; only
    # root can chown to a different uid.
    with contextlib.suppress(PermissionError):
        os.chown(path, source.st_uid, source.st_gid)


def _flush(path: str) -> None:
    """Get a staged file onto the disk before it is renamed.

    Durability, not visibility. ffmpeg and copyfile both return with the write
    still in the page cache, so a crash just after the rename could leave the
    final name pointing at half a file.

    Opened read-only: the staged file now carries the mode of the one it
    replaces, and a library kept at 0444 cannot be reopened for writing. fsync
    flushes the inode either way.
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(tmp: str, out_path: str, source: os.stat_result) -> None:
    """Move the finished rewrite into place, atomically, from anywhere.

    os.replace is atomic within a filesystem and raises EXDEV across one, so the
    free path is tried first. Trying beats predicting: mergerfs reports one
    st_dev for a pool of separate filesystems, so comparing st_dev would call a
    rename safe when it isn't.

    The cross-device path copies onto the target's filesystem under a name
    scanners ignore, then publishes with the same atomic rename.
    """
    _adopt(tmp, source)
    # Before the attempt, not after it fails: a flush is only worth something
    # ahead of the rename it protects.
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
    #: Nothing wrong with the file, just not safe to replace yet. Separate
    #: from FAILED so a benign race doesn't alert like a corruption.
    DEFERRED = "deferred"
    FAILED = "failed"


#: How far the result's duration may drift, with a floor for short files.
#: Covers container timestamp rounding.
DURATION_DRIFT_RATIO = 0.005
DURATION_DRIFT_FLOOR = 1.0

#: How much of ffmpeg's stderr a failure keeps. The tail, since the fatal
#: line comes last, after a damaged file's per-packet noise.
_STDERR_TAIL = 500


def _verify(plan: Plan, out_info: dict) -> str | None:
    """What is wrong with the rewrite, or None when it checks out.

    A truncated or stream-short result is the one failure that would silently
    damage the library, so both are checked against the source.
    """
    src_dur = plan.src_duration
    out_dur = duration(out_info)
    drift_allowed = max(DURATION_DRIFT_FLOOR, src_dur * DURATION_DRIFT_RATIO)
    if src_dur and abs(out_dur - src_dur) > drift_allowed:
        return f"duration mismatch: {src_dur:.1f}s -> {out_dur:.1f}s"
    out_streams = len(out_info.get("streams") or [])
    if out_streams != len(plan.streams):
        return f"stream count mismatch: expected {len(plan.streams)}, got {out_streams}"
    return None


def apply_plan(plan: Plan) -> tuple[Outcome, str]:
    """Rewrite the file, replacing it only once the result verifies.

    The detail says what went wrong when nothing was replaced. Nothing is
    logged here; the caller reports what comes back.
    """
    if plan.out_path != plan.path and os.path.exists(plan.out_path):
        # A remux lands beside the source, and an .mkv already there is not
        # ours to overwrite. Recurs every sweep, so the detail says which.
        return Outcome.FAILED, (
            f"remux target already exists: {plan.out_path} "
            f"(delete {plan.path} if the .mkv is a finished remux, "
            "or delete the .mkv to redo it)"
        )

    src_before = os.stat(plan.path)
    # A stale plan's stream maps can mangle the new file in ways _verify
    # cannot see. Defer; the next pass plans the file as it now is.
    if plan.src_signature and SourceSignature.of(src_before) != plan.src_signature:
        return Outcome.DEFERRED, "source changed since it was planned, nothing rewritten"

    # Staged only once the pre-flight checks pass; any earlier and each
    # return above leaked a temp file.
    try:
        tmp = _new_temp(config.WORK_DIR)
    except OSError as err:
        return Outcome.FAILED, f"could not stage the rewrite in {config.WORK_DIR}: {err}"

    args = ffmpeg_args(plan, tmp)
    log.info("ffmpeg %s", " ".join(args[1:]))
    try:
        res = subprocess.run(
            args, capture_output=True, text=True, timeout=config.FFMPEG_TIMEOUT
        )
        if res.returncode != 0:
            stderr_tail = res.stderr.strip()[-_STDERR_TAIL:]
            return Outcome.FAILED, f"ffmpeg failed ({res.returncode}): {stderr_tail}"

        problem = _verify(plan, probe(tmp))
        if problem:
            return Outcome.FAILED, f"{problem}, result discarded"

        # An upgrade can land while ffmpeg still reads the old inode, and
        # renaming over it would revert it. Its own webhook will follow.
        src_after = os.stat(plan.path)
        if SourceSignature.of(src_after) != SourceSignature.of(src_before):
            return Outcome.DEFERRED, "source changed during the rewrite, result discarded"

        _publish(tmp, plan.out_path, src_before)
        if plan.out_path != plan.path:
            # The converted file is already published, and the next sweep
            # rediscovers a leftover source, so this must not fail the job.
            try:
                os.remove(plan.path)
            except OSError as err:
                log.warning("could not remove %s after remux: %s", plan.path, err)
        log.info("rewrote %s", plan.out_path)
        return Outcome.APPLIED, ""
    except subprocess.TimeoutExpired:
        return Outcome.FAILED, f"ffmpeg timed out after {config.FFMPEG_TIMEOUT}s"
    finally:
        # Already gone when _publish renamed it; missing is an OSError too.
        with contextlib.suppress(OSError):
            os.remove(tmp)


def work_dir_errors() -> list[str]:
    """Whether WORK_DIR is usable, as ready-to-log messages.

    Creates it when missing and stages a file to prove the mount is writable,
    rather than finding out after the first ffmpeg run. Which filesystem it
    is on doesn't matter; see :func:`_publish`.
    """
    try:
        os.makedirs(config.WORK_DIR, exist_ok=True)
        probe_path = _new_temp(config.WORK_DIR)
        os.remove(probe_path)
    except OSError as err:
        return [f"WORK_DIR {config.WORK_DIR} is not usable: {err}"]
    return []


def audio_codec_errors() -> list[str]:
    """Whether AUDIO_CODEC names an audio encoder this ffmpeg carries.

    A typo otherwise surfaces as the first rewrite failing, hours after the
    restart that introduced it. A missing ffmpeg is startup's own check.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError, subprocess.SubprocessError:
        return []
    if out.returncode != 0:
        return []
    for line in out.stdout.splitlines():
        # One per line: " A....D aac   AAC (Advanced Audio Coding)". The
        # first flag is the codec type, A for audio.
        flags, _, rest = line.strip().partition(" ")
        if flags.startswith("A") and rest.split()[:1] == [config.AUDIO_CODEC]:
            return []
    return [
        (
            f"AUDIO_CODEC {config.AUDIO_CODEC!r} is not an audio encoder this ffmpeg "
            "provides (see ffmpeg -encoders)"
        )
    ]


def work_dir_is_remote() -> bool:
    """Whether publishing will have to copy rather than rename.

    Best effort, and only used to say so at startup. A union filesystem
    reports one st_dev for separate branches, so this can say no and _publish
    still meet EXDEV. It gates nothing.
    """
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
    """Remove a staged file left by a crash, if it can't be in use.

    Age is usually the only safe test, since a rewrite may be running in
    another process: anything older than the ffmpeg timeout has outlived the
    longest run its writer was allowed. ``force`` is for a caller holding
    every rewrite slot, which proves no writer exists.
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

    ``exclusive`` says the caller holds every rewrite slot, proving nothing
    here can still be written, so even fresh orphans go. Without it a file
    may be another process's live rewrite and only age is safe.

    WORK_DIR only. Cross-filesystem publishing stages beside the file it
    replaces, so :func:`trackstarr.sweep.walk_library` clears those.
    """
    if not config.WORK_DIR or not os.path.isdir(config.WORK_DIR):
        return
    for name in os.listdir(config.WORK_DIR):
        if is_staged_file(name):
            drop_staged(os.path.join(config.WORK_DIR, name), force=exclusive)
