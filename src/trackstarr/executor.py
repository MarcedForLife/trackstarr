"""Carry out a plan, verifying the result before anything is overwritten."""

from __future__ import annotations

import contextlib
import enum
import logging
import os
import subprocess
import time

from . import config
from .media import duration, probe
from .planner import Plan, ffmpeg_args

log = logging.getLogger(__name__)

TEMP_PREFIX = ".trackstarr-"


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
    n_out = len(out_info.get("streams") or [])
    if n_out != len(plan.streams):
        return f"stream count mismatch: expected {len(plan.streams)}, got {n_out}"
    return None


def apply_plan(plan: Plan) -> tuple[Outcome, str]:
    """Rewrite the file, replacing it only after the result verifies.

    The detail string says what went wrong when nothing was replaced. No
    failure is logged here; the caller reports the returned detail.
    """
    os.makedirs(config.WORK_DIR, exist_ok=True)
    ext = os.path.splitext(plan.out_path)[1]
    tmp = os.path.join(config.WORK_DIR, f"{TEMP_PREFIX}{os.getpid()}-{int(time.time())}{ext}")

    if plan.out_path != plan.path and os.path.exists(plan.out_path):
        # A remux lands beside the source under a new name; an .mkv sibling
        # already sitting there is not ours to overwrite.
        return Outcome.FAILED, f"remux target already exists: {plan.out_path}"

    src_before = os.stat(plan.path)

    args = ffmpeg_args(plan, tmp)
    log.info("ffmpeg %s", " ".join(args[1:]))
    try:
        res = subprocess.run(
            args, capture_output=True, text=True, timeout=config.FFMPEG_TIMEOUT
        )
        if res.returncode != 0:
            return Outcome.FAILED, f"ffmpeg failed ({res.returncode}): {res.stderr.strip()}"

        problem = _verify(plan, probe(tmp))
        if problem:
            return Outcome.FAILED, f"{problem}, result discarded"

        os.chmod(tmp, src_before.st_mode & 0o7777)
        # Already correct when running as the media owner, which is the normal
        # case; only root can chown to a different uid.
        with contextlib.suppress(PermissionError):
            os.chown(tmp, src_before.st_uid, src_before.st_gid)

        # An *arr upgrade can land while ffmpeg is still reading the old
        # inode. Renaming over it now would silently revert the upgrade, so a
        # source that changed since the pre-flight stat is left alone; the
        # upgrade's own webhook or the next sweep deals with the new file.
        src_after = os.stat(plan.path)
        if (src_after.st_size, src_after.st_mtime_ns) != (
            src_before.st_size,
            src_before.st_mtime_ns,
        ):
            return Outcome.DEFERRED, "source changed during the rewrite, result discarded"

        # Same filesystem as the library, so this is atomic: readers see either
        # the old file or the new one, never a partial write.
        os.replace(tmp, plan.out_path)
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


def clean_work_dir() -> None:
    """Drop temp files orphaned by a restart mid-rewrite.

    Safe unconditionally: a temp file is only ever renamed into the library
    after it verifies, so anything still sitting here is a failed attempt.
    """
    if not os.path.isdir(config.WORK_DIR):
        return
    for name in os.listdir(config.WORK_DIR):
        if not name.startswith(TEMP_PREFIX):
            continue
        try:
            os.remove(os.path.join(config.WORK_DIR, name))
            log.info("removed stale temp file %s", name)
        except OSError as err:
            log.warning("could not remove %s: %s", name, err)
