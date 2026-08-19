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
from .media import duration, probe
from .planner import Plan, SourceSignature, ffmpeg_args

log = logging.getLogger(__name__)

#: Staged rewrites are dotfiles so Plex, Radarr and Sonarr skip them, and
#: carry no media extension so nothing mistakes one for an import even if it
#: does look. The muxer is passed to ffmpeg explicitly instead.
TEMP_PREFIX = ".trackstarr-"
TEMP_SUFFIX = ".partial"


def _new_temp(directory: str) -> str:
    """An empty staging file in ``directory``, and its path.

    mkstemp, not a name built from the pid and the clock: two rewrites
    starting within the same second would share that name, both ffmpegs
    would write to it, and whichever finished last would be renamed over
    both sources.
    """
    os.makedirs(directory, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=directory)
    os.close(handle)
    return path


def _adopt(path: str, source: os.stat_result) -> None:
    """Give a staged file the mode and ownership of the file it replaces."""
    os.chmod(path, source.st_mode & 0o7777)
    # Already correct when running as the media owner, which is the normal
    # case; only root can chown to a different uid.
    with contextlib.suppress(PermissionError):
        os.chown(path, source.st_uid, source.st_gid)


def _flush(path: str) -> None:
    """Get a staged file's contents onto the disk before it is renamed.

    Durability, not visibility: the renames below are atomic either way, but
    ffmpeg and copyfile both return with the write still in the page cache,
    so without this a crash just after the rename can leave the final name
    pointing at a partially written file. Cheap in practice, since the kernel
    has been writing back throughout the rewrite and only the tail is left.

    Opened read-only, because by now the staged file carries the mode of the
    file it replaces, and a library kept at 0444 cannot be reopened for
    writing even by its owner. fsync flushes the inode's dirty pages however
    the descriptor asking for it was opened.
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(tmp: str, out_path: str, source: os.stat_result) -> None:
    """Move the finished rewrite into place, atomically, from anywhere.

    os.replace is atomic within a filesystem and raises EXDEV across one, so
    the free path is tried first and the copy only happens when it must.
    Trying beats predicting: a union filesystem like mergerfs reports one
    st_dev for the whole pool while its branches really are separate
    filesystems, so an st_dev comparison would say a rename is safe when it
    isn't. Asking the kernel is always right.

    The cross-device path copies onto the target's own filesystem under a
    name library scanners ignore, then publishes it with the same atomic
    rename. Readers still see the old file or the new one, never a partial.
    """
    _adopt(tmp, source)
    # Before the attempt, not after it fails: a flush is only worth anything
    # ahead of the rename it protects. The cost when EXDEV does fire is one
    # redundant flush on the path startup has already reported as the slow
    # one, which is the cheaper mistake.
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
    #: Nothing wrong with the file, just not safe to replace right now; the
    #: next webhook or sweep retries. Distinct from FAILED so a benign race
    #: doesn't alert like a corruption.
    DEFERRED = "deferred"
    FAILED = "failed"


#: The result's duration may drift from the source by this fraction (with a
#: floor for short files), covering container timestamp rounding.
DURATION_DRIFT_RATIO = 0.005
DURATION_DRIFT_FLOOR = 1.0

#: How much of ffmpeg's stderr a failure detail keeps. The tail, because the
#: fatal message comes last, after however much per-packet noise a damaged
#: file produced; the detail lands whole in events.jsonl and the logs.
_STDERR_TAIL = 500


def _verify(plan: Plan, out_info: dict) -> str | None:
    """What is wrong with the rewrite, or None when it checks out.

    A truncated or stream-short result is the one failure mode that would
    silently damage the library, so both are checked against the source.
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
    """Rewrite the file, replacing it only after the result verifies.

    The detail string says what went wrong when nothing was replaced. No
    failure is logged here; the caller reports the returned detail.
    """
    if plan.out_path != plan.path and os.path.exists(plan.out_path):
        # A remux lands beside the source under a new name; an .mkv sibling
        # already sitting there is not ours to overwrite. This recurs every
        # sweep until a human removes one of the two, so the detail says
        # which file to delete for which outcome.
        return Outcome.FAILED, (
            f"remux target already exists: {plan.out_path} "
            f"(delete {plan.path} if the .mkv is a finished remux, "
            "or delete the .mkv to redo it)"
        )

    src_before = os.stat(plan.path)
    # A plan goes stale waiting on the rewrite lock: another thread may have
    # rewritten the file since it was planned, and a stale plan's stream maps
    # can mangle the new file in ways _verify cannot see. Defer; the next
    # webhook or sweep plans the file as it now is.
    if plan.src_signature and SourceSignature.of(src_before) != plan.src_signature:
        return Outcome.DEFERRED, "source changed since it was planned, nothing rewritten"

    # Staged only once the pre-flight checks pass: the returns above happen
    # before the finally that cleans it up, so creating it earlier left an
    # empty temp file behind every time one of them fired.
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

        # An *arr upgrade can land while ffmpeg is still reading the old
        # inode. Renaming over it now would silently revert the upgrade, so a
        # source that changed since the pre-flight stat is left alone; the
        # upgrade's own webhook or the next sweep deals with the new file.
        src_after = os.stat(plan.path)
        if SourceSignature.of(src_after) != SourceSignature.of(src_before):
            return Outcome.DEFERRED, "source changed during the rewrite, result discarded"

        _publish(tmp, plan.out_path, src_before)
        if plan.out_path != plan.path:
            # The converted file is already published; a leftover source is
            # rediscovered by the next sweep, so failing to remove it must
            # not fail the job.
            try:
                os.remove(plan.path)
            except OSError as err:
                log.warning("could not remove %s after remux: %s", plan.path, err)
        log.info("rewrote %s", plan.out_path)
        return Outcome.APPLIED, ""
    except subprocess.TimeoutExpired:
        return Outcome.FAILED, f"ffmpeg timed out after {config.FFMPEG_TIMEOUT}s"
    finally:
        if os.path.exists(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)


def work_dir_errors() -> list[str]:
    """Whether WORK_DIR is usable, as ready-to-log messages.

    Creates it when missing, so a fresh install starts clean, and stages a
    file in it to prove the mount is writable rather than discovering it
    after the first ffmpeg run. Which filesystem it is on deliberately
    doesn't matter; see :func:`_publish`.
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

    A typo'd codec otherwise surfaces as the first rewrite failing, hours
    after the restart that introduced it. ffmpeg's absence is not reported
    here; startup already checks PATH separately.
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
        # One encoder per line: " A....D aac   AAC (Advanced Audio Coding)".
        # The first flag character is the codec type, A for audio.
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

    Best effort and only used to say so at startup: a union filesystem
    reports one st_dev for branches that are really separate, so this can
    say no and _publish still meet EXDEV. It never gates anything.
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
    """Remove a staged file left behind by a crash, if it can't be in use.

    Age is normally the only safe test: with rewrites running concurrently,
    and possibly in another process, a staged file younger than the ffmpeg
    timeout may still be being written. Anything older than that has
    outlived the longest run its writer was allowed. ``force`` is for the
    caller that holds every rewrite slot, which is proof no writer exists;
    see :func:`clean_work_dir`.
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

    ``exclusive`` says the caller holds every rewrite slot (see
    :func:`trackstarr.processing.all_slots_held`), which proves no staged
    file here can still be written, so even fresh orphans go. Without it a
    file may be another process's live rewrite — serve restarting while a
    ``sweep --apply`` runs beside it — and only age is safe.

    Only covers WORK_DIR. Cross-filesystem publishing stages its landing
    copy beside the file it replaces, so those orphans scatter across the
    library and the sweep clears them as it walks — see
    :func:`trackstarr.sweep.walk_library`.
    """
    if not config.WORK_DIR or not os.path.isdir(config.WORK_DIR):
        return
    for name in os.listdir(config.WORK_DIR):
        if is_staged_file(name):
            drop_staged(os.path.join(config.WORK_DIR, name), force=exclusive)
