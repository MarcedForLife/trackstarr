"""Carry out a plan, verifying the result before anything is overwritten."""

import contextlib
import enum
import errno
import functools
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from fractions import Fraction
from typing import IO

from . import config
from .command import ffmpeg_args
from .langs import norm_lang
from .media import (
    FRAME_VIDEO_PROPERTIES,
    VIDEO_PROPERTIES,
    ProbeError,
    dolby_vision,
    duration,
    frame_sample,
    hdr_metadata,
    is_chapter_stream,
    probe,
    stream_lang,
    stream_title,
)
from .planner import FileRevision, Plan, PlanInput, PreparedTrack, SourceSignature
from .policy import Policy
from .tracks import CODECS, downmixed_layouts

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


class Cancel:
    """One rewrite's kill switch and the gate its mutation commits through.

    A path is not enough to kill by: between a skip choosing a file and the
    signal reaching it, the same path can be claimed again by another run, and
    the kill would land on that one instead. The owner marks this while it
    still holds the phase, and ffmpeg gives up at once if it was marked before
    the process started.

    A mark alone cannot stop a header edit, which is over in a second and has
    no process to signal. Every path that changes a file passes :meth:`commit`
    first, and the two are decided one at a time: a skip either arrives before
    the edit and prevents it, or arrives after and lets it finish.
    """

    def __init__(self, path: str = ""):
        #: The library file, for the abort that still works by name.
        self.path = path
        self.asked = threading.Event()
        self._gate = threading.Lock()
        self._committed = False

    def ask(self) -> bool:
        """Mark the phase stopped; whether it was in time to prevent a mutation."""
        with self._gate:
            self.asked.set()
            return not self._committed

    def stopped(self) -> bool:
        """Whether a skip has arrived. Read before entering a mutation."""
        return self.asked.is_set()

    def commit(self) -> bool:
        """Take the phase past the point a skip can stop it; whether it may go on.

        Held only while choosing between the two, never while a tool runs.
        """
        with self._gate:
            if self.asked.is_set() and not self._committed:
                return False
            self._committed = True
            return True

    def wait(self, timeout: float) -> bool:
        """Sleep, returning early on a skip. Whether one arrived."""
        return self.asked.wait(timeout)


#: The ffmpeg runs this process has going and what each is rewriting, so an
#: abort can reach them all and a skip can reach exactly one.
_running_ffmpeg: dict[subprocess.Popen, Cancel] = {}
_running_lock = threading.Lock()
# Header edits and source registration share this gate. Reads release it once
# registered, so unrelated edits remain possible during a long preparation.
_edit_lock = threading.Lock()
_source_reads: dict[FileRevision, int] = {}
_preparing: set[str] = set()


def running_count() -> int:
    """How many ffmpeg runs this process has going, so the page can say what
    an abort would waste."""
    with _running_lock:
        return len(_running_ffmpeg)


def is_rewriting(path: str) -> bool:
    """Whether this process is rewriting or preparing audio from ``path``.

    Source probes also register here, before opening a header. A run with no
    path on record answers for no file.
    """
    with _running_lock:
        if bool(path) and any(cancel.path == path for cancel in _running_ffmpeg.values()):
            return True
        revisions = tuple(_source_reads)
    # Filesystem lookups can stall on an unavailable mount. They must not
    # prevent cancellation from taking the process registry lock.
    if path and revisions:
        canonical = os.path.realpath(path)
        if any(revision.path == canonical for revision in revisions):
            return True
        # Hardlink aliases share the same header, even under another name.
        with contextlib.suppress(OSError):
            found = os.stat(path)
            return any(
                (revision.device, revision.inode) == (found.st_dev, found.st_ino)
                for revision in revisions
            )
    return False


def _check_revision(revision: FileRevision) -> None:
    try:
        current = FileRevision.of(revision.path)
    except OSError as err:
        raise InterruptedError("source is unavailable, preparation discarded") from err
    if current != revision:
        raise InterruptedError("source changed, preparation discarded")


@contextlib.contextmanager
def _read_source(revision: FileRevision) -> Iterator[None]:
    with _edit_lock:
        _check_revision(revision)
        with _running_lock:
            _source_reads[revision] = _source_reads.get(revision, 0) + 1
    try:
        yield
    finally:
        try:
            _check_revision(revision)
        finally:
            with _running_lock:
                _source_reads[revision] -= 1
                if not _source_reads[revision]:
                    del _source_reads[revision]


def _preparation_timing(
    source: PlanInput, stream: dict, policy: Policy
) -> tuple[Fraction, bool]:
    """Shared timing and codec checks for the preview and preparation."""
    mapping = source.mapping
    placed = mapping.scale * Fraction(stream.get("start_time", "0")) + Fraction(
        mapping.offset_us, 1_000_000
    )
    encode = mapping.scale != 1 or placed < 0
    if encode:
        # Object extensions are not reliably exposed by ffprobe. Refuse
        # their carrier codecs rather than silently flattening a source.
        codec = stream.get("codec_name", "")
        if not codec.startswith("pcm_") and codec not in {
            "flac",
            "alac",
            "aac",
            "ac3",
            "mp3",
            "opus",
            "vorbis",
        }:
            raise ValueError("retiming this codec could discard immersive audio")
        if not any(
            layout.channels == stream.get("channels") and layout.codec
            for layout in policy.audio_layouts
        ):
            raise ValueError("retiming requires an AUDIO_LAYOUTS encoder for this layout")
        if not Fraction(1, 2) <= mapping.scale <= 2:
            raise ValueError("audio preparation supports scales from 1/2 to 2")
    return placed, encode


def _prepare_track(
    source: PlanInput, dest: str, policy: Policy, cancel: Cancel
) -> PreparedTrack:
    """Materialise one audio stream. All source reads occur under the guard."""
    with _read_source(source.revision):
        if cancel.stopped():
            raise InterruptedError("audio preparation was stopped")
        info = probe(source.revision.path)
        stream: dict = next(
            (s for s in info.get("streams", []) if s["index"] == source.stream_index), {}
        )
        if stream.get("codec_type") != "audio":
            raise ValueError("source stream is not audio")
        mapping = source.mapping
        start = Fraction(stream.get("start_time", "0"))
        placed, encode = _preparation_timing(source, stream, policy)
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-xerror", "-copyts"]
        if not encode:
            args += ["-itsoffset", f"{mapping.offset_us / 1_000_000:.6f}"]
        args += [
            "-i",
            source.revision.path,
            "-map",
            f"0:{source.stream_index}",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-map_metadata:s:0",
            f"0:s:{source.stream_index}",
        ]
        if encode:
            filters = ["asetpts=PTS-STARTPTS"]
            if mapping.scale != 1:
                filters.append(f"atempo={float(1 / mapping.scale):.12g}")
            if placed < 0:
                filters.append(f"atrim=start={float(-placed):.9f}")
            filters.append(f"asetpts=PTS-STARTPTS+{float(max(placed, Fraction(0))):.9f}/TB")
            # Double PCM stores filter output without a lossy intermediate.
            args += ["-af", ",".join(filters), "-c:a", "pcm_f64le"]
            seconds = duration(info)
            if seconds <= 0:
                raise ValueError("source duration is required to budget lossless preparation")
            needed = int(
                (seconds + abs(float(start)))
                * float(mapping.scale)
                * int(stream["sample_rate"])
                * stream["channels"]
                * 8
            )
        else:
            args += ["-c:a", "copy"]
            needed = source.revision.size
        if shutil.disk_usage(os.path.dirname(dest)).free < needed * 1.05 + 1024 * 1024:
            raise OSError(errno.ENOSPC, "not enough space for audio preparation")
        args += ["-avoid_negative_ts", "disabled", "-f", "matroska", dest]
        code, stderr = _run_ffmpeg(args, cancel=cancel)
        if cancel.stopped() or code < 0:
            raise InterruptedError("audio preparation was stopped")
        if code:
            raise RuntimeError(f"audio preparation failed ({code}): {stderr[-_STDERR_TAIL:]}")
        result = probe(dest)
        tracks = result.get("streams", [])
        if len(tracks) != 1 or tracks[0].get("codec_type") != "audio":
            raise ValueError("prepared file must contain exactly one audio stream")
        if tracks[0].get("channels") != stream.get("channels"):
            raise ValueError("prepared audio channel count changed")
        if duration(result) <= float(max(placed, Fraction(0))):
            raise ValueError("prepared audio is empty")
    return PreparedTrack(dest, source, encode)


@contextlib.contextmanager
def _prepare_inputs(
    inputs: Sequence[PlanInput], policy: Policy, cancel: Cancel
) -> Iterator[tuple[PreparedTrack, ...]]:
    """Own prepared media until rendering finishes, including on cancellation.

    InterruptedError means stale or cancelled work and calls for replanning.
    Other errors leave the target untouched and remove all prepared media.
    """
    if not inputs:
        yield ()
        return
    work_dir = config.current().WORK_DIR
    os.makedirs(work_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{TEMP_PREFIX}audio-", suffix=TEMP_SUFFIX, dir=work_dir
    ) as workspace:
        with _running_lock:
            _preparing.add(workspace)
        try:
            prepared = tuple(
                _prepare_track(source, os.path.join(workspace, f"{index}.mka"), policy, cancel)
                for index, source in enumerate(inputs)
            )
            for source in inputs:
                _check_revision(source.revision)
            if cancel.stopped():
                raise InterruptedError("audio preparation was stopped")
            yield prepared
        finally:
            with _running_lock:
                _preparing.remove(workspace)


def _signal(procs: list[subprocess.Popen]) -> int:
    """SIGTERM each of these; how many were signalled.

    Safe at any moment: a rewrite is staged and published only once verified,
    so a kill costs the encode and never the library file. A signalled rewrite
    ends as :data:`Outcome.DEFERRED`, so nothing counts it as a failure of the
    file.
    """
    for proc in procs:
        # Gone between the snapshot and here is the normal race, not an error.
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGTERM)
    return len(procs)


def terminate_running(path: str = "") -> int:
    """SIGTERM every running ffmpeg, or only the ones rewriting ``path``; how
    many were signalled."""
    with _running_lock:
        procs = [
            proc
            for proc, cancel in _running_ffmpeg.items()
            if (not path or cancel.path == path) and cancel.ask()
        ]
    return _signal(procs)


def terminate_phase(cancel: Cancel) -> int:
    """SIGTERM only the rewrite this phase started; how many were signalled.

    Marked first, so a process that has yet to register finds the answer
    waiting for it and never runs on.
    """
    if not cancel.ask():
        return 0
    with _running_lock:
        procs = [proc for proc, running in _running_ffmpeg.items() if running is cancel]
    return _signal(procs)


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
    args: list[str], on_progress: ProgressCallback | None = None, cancel: Cancel | None = None
) -> tuple[int, str]:
    """Run ffmpeg to completion; its exit code and stderr.

    Not subprocess.run, which hides the Popen from :func:`terminate_running`.
    Raises TimeoutExpired like run() does, having killed the process.
    ``on_progress`` gets the readout down a pipe of its own, leaving
    ``communicate`` the standard streams. ``cancel`` names the work this call
    is for, which is how an abort and a skip each find their process.
    """
    cancel = cancel or Cancel()
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
            start_new_session=True,
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
            _running_ffmpeg[proc] = cancel
    except BaseException:
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        if readout is not None:
            readout.close()
        for descriptor in (read_fd, write_fd):
            if descriptor >= 0:
                os.close(descriptor)
        raise
    # Skipped between the claim and this process starting: the skip's own
    # signal found nothing to reach, so it is answered here instead.
    if cancel.asked.is_set():
        _signal([proc])
    try:
        _, stderr = proc.communicate(timeout=config.current().FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    finally:
        with _running_lock:
            _running_ffmpeg.pop(proc, None)
    return proc.returncode, stderr or ""


def _verify(plan: Plan, out_info: dict) -> str | None:
    """What is wrong with the rewrite, or None. A truncated or stream-short
    result would silently damage the library."""
    src_dur = plan.src_duration
    out_dur = duration(out_info)
    drift_allowed = max(DURATION_DRIFT_FLOOR, src_dur * DURATION_DRIFT_RATIO)
    if src_dur and abs(out_dur - src_dur) > drift_allowed:
        return f"duration mismatch: {src_dur:.1f}s -> {out_dur:.1f}s"
    if plan.src_chapter_count and len(out_info.get("chapters") or []) != plan.src_chapter_count:
        return "chapter count mismatch"
    streams = out_info.get("streams") or []
    # MP4 generates a chapter track outside the explicit stream maps.
    if (
        plan.src_chapter_count
        and os.path.splitext(plan.out_path)[1].lower() in {".mp4", ".m4v"}
        and streams
        and is_chapter_stream(streams[-1])
    ):
        streams = streams[:-1]
    out_streams = len(streams)
    if out_streams != len(plan.streams):
        return f"stream count mismatch: expected {len(plan.streams)}, got {out_streams}"
    for stream, result in zip(plan.streams, streams, strict=True):
        if not stream.dv_strip:
            continue
        if dolby_vision(result) is not None:
            return "Dolby Vision configuration remains in output"
        for key in VIDEO_PROPERTIES:
            if stream.video_source.get(key) != result.get(key):
                return f"Dolby Vision removal changed video {key}"
        if hdr_metadata(stream.video_source) != hdr_metadata(result):
            return "Dolby Vision removal changed stream HDR metadata"
    return None


@functools.cache
def dv_filter_available() -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "bsf=dovi_rpu"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return out.returncode == 0 and "-strip " in out.stdout


def _verify_dv_frames(plan: Plan, staged: str) -> str | None:
    for index, stream in enumerate(plan.streams):
        if not stream.dv_strip:
            continue
        before = frame_sample(plan.path, stream.source.stream_index)
        after = frame_sample(staged, index)
        if len(before) != len(after):
            return "Dolby Vision verification frame count mismatch"
        for source, result in zip(before, after, strict=True):
            if dolby_vision(result) is not None:
                return "Dolby Vision data remains in sampled output frames"
            if hdr_metadata(source) != hdr_metadata(result):
                return "Dolby Vision removal changed sampled HDR metadata"
            for key in FRAME_VIDEO_PROPERTIES:
                if source.get(key) != result.get(key):
                    return f"Dolby Vision removal changed sampled video {key}"
    return None


def _target_taken(plan: Plan) -> str:
    """Why a remux may not publish, or "". An .mkv already beside the source is
    not ours to overwrite. Recurs every sweep, so the detail says what to do."""
    if plan.out_path == plan.path or not os.path.exists(plan.out_path):
        return ""
    return (
        f"remux target already exists: {plan.out_path} "
        f"(delete {plan.path} if the .mkv is a finished remux, "
        "or delete the .mkv to redo it)"
    )


def _input_problem(plan: Plan) -> str:
    used = set()
    for stream in plan.streams:
        index = stream.source.input_index
        if not index:
            continue
        if index > len(plan.inputs):
            return "source inputs require preparation before execution"
        if stream.kind != "audio" or stream.dv_strip or stream.sub_codec:
            return "external inputs must be audio"
        if stream.source.stream_index != plan.inputs[index - 1].stream_index:
            return "source stream does not match its selected input"
        used.add(index)
    if used != set(range(1, len(plan.inputs) + 1)):
        return "plan contains unused source inputs"
    return ""


def _render_plan(plan: Plan, prepared: Sequence[PreparedTrack]) -> Plan:
    """Resolve final encoders without changing the reviewed plan or provenance."""
    streams = []
    extension = os.path.splitext(plan.out_path)[1].lower()
    prepared_streams = [probe(track.path)["streams"][0] for track in prepared]
    for stream in plan.streams:
        if stream.source.input_index:
            track = prepared[stream.source.input_index - 1]
            info = prepared_streams[stream.source.input_index - 1]
            stream = replace(stream, title=stream.title or stream_title(info))
            if track.encode and not stream.encode:
                layout = next(
                    item
                    for item in plan.policy.audio_layouts
                    if item.channels == info["channels"] and item.codec
                )
                stream = replace(
                    stream,
                    channels=info["channels"],
                    codec=layout.codec,
                    bitrate=layout.bitrate,
                )
            if stream.encode or track.encode:
                codec = CODECS.get(stream.codec)
                if codec and (
                    extension not in codec.containers
                    or (stream.channels or 0) > codec.max_channels
                ):
                    raise ValueError(
                        "source encoder cannot preserve this layout in the target container"
                    )
        streams.append(stream)
    return replace(plan, streams=streams)


def _stream_end(stream: dict) -> float:
    """End on the container timeline, including Matroska's per-track duration."""
    if stream.get("duration") is not None:
        return float(stream.get("start_time", 0)) + float(stream["duration"])
    for key, value in stream.get("tags", {}).items():
        if key.upper() == "DURATION":
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError("cannot verify source audio end time")


def _source_stream(track: PreparedTrack) -> dict:
    with _read_source(track.source.revision):
        return next(
            stream
            for stream in probe(track.source.revision.path)["streams"]
            if stream["index"] == track.source.stream_index
        )


def _source_layout(track: PreparedTrack) -> str:
    # Matroska PCM reports channel count without the speaker layout. Restore
    # the source layout explicitly when encoding that intermediate.
    original = _source_stream(track)
    layout = original.get("channel_layout") or {1: "mono", 2: "stereo"}.get(
        original["channels"]
    )
    if not layout:
        raise ValueError("retiming requires a known source channel layout")
    return layout


def _sourced_args(plan: Plan, dest: str, prepared: Sequence[PreparedTrack]) -> list[str]:
    args = ffmpeg_args(plan, dest, prepared)
    for index, out in enumerate(plan.streams):
        if out.source.input_index and not out.encode:
            track = prepared[out.source.input_index - 1]
            if track.encode:
                args[-1:-1] = [f"-channel_layout:{index}", _source_layout(track)]
    return args


def _verify_sourced(
    plan: Plan,
    target: dict,
    prepared: Sequence[PreparedTrack],
    result: dict,
    staged: str,
    cancel: Cancel,
) -> str | None:
    """Check preservation and each import independently of container duration."""
    before_chapters = target.get("chapters", [])
    after_chapters = result.get("chapters", [])
    if len(before_chapters) != len(after_chapters):
        return "chapter count mismatch"
    for before, after in zip(before_chapters, after_chapters, strict=True):
        if any(
            abs(float(before[key]) - float(after[key])) > 0.002
            for key in ("start_time", "end_time")
        ) or before.get("tags", {}) != after.get("tags", {}):
            return "chapter content changed"
    tags = result.get("format", {}).get("tags", {})
    for key, value in target.get("format", {}).get("tags", {}).items():
        if key.lower() in {"encoder", "duration"}:
            continue
        expected = "" if key.lower() == "title" and plan.clear_container_title else value
        if tags.get(key, "") != expected:
            return f"target metadata changed: {key}"
    local = {item["index"]: item for item in target["streams"]}
    for index, (out, stream) in enumerate(zip(plan.streams, result["streams"], strict=False)):
        imported = bool(out.source.input_index)
        if imported:
            track = prepared[out.source.input_index - 1]
            original = probe(track.path)["streams"][0]
            if track.encode:
                original["channel_layout"] = _source_layout(track)
        else:
            original = local[out.source.stream_index]
        if stream.get("codec_type") != out.kind:
            return "stream kind mismatch"
        if (
            not out.encode
            and not out.sub_codec
            and not (imported and track.encode)
            and stream.get("codec_name") != original.get("codec_name")
        ):
            return "copied stream codec changed"
        if out.kind == "video":
            if any(original.get(key) != stream.get(key) for key in VIDEO_PROPERTIES):
                return "target video properties changed"
            if hdr_metadata(original) != hdr_metadata(stream):
                return "target video HDR metadata changed"
        if out.kind == "audio":
            channels = out.channels if out.encode else original.get("channels")
            if channels != stream.get("channels"):
                return "audio channel count mismatch"
            if not out.encode and original.get("channel_layout") != stream.get(
                "channel_layout"
            ):
                return "audio channel layout mismatch"
        if out.kind in {"audio", "subtitle"}:
            language = norm_lang(out.lang) if out.lang else stream_lang(original)
            title = "" if out.clear_title else out.title or stream_title(original)
            if stream_lang(stream) != language or stream_title(stream) != title:
                return f"stream {index} language or title mismatch"
            expected_flags = {
                key for key, value in original.get("disposition", {}).items() if value
            }
            if out.encode:
                expected_flags = set()
            elif imported:
                expected_flags.discard("default")
            flags = {key for key, value in stream.get("disposition", {}).items() if value}
            if flags != expected_flags:
                return "stream disposition mismatch"
        if out.kind in {"video", "audio"} and (
            abs(float(original.get("start_time", 0)) - float(stream.get("start_time", 0)))
            > 0.05
        ):
            return "stream start time mismatch"
        if imported:
            start = float(stream.get("start_time", 0))
            end = _stream_end(stream)
            if end <= start or abs(end - _stream_end(original)) > 0.05:
                return "imported audio end time mismatch"
            source = _source_stream(track)
            mapping = track.source.mapping
            scale, offset = float(mapping.scale), mapping.offset_us / 1_000_000
            expected_start = max(0, scale * float(source.get("start_time", 0)) + offset)
            expected_end = scale * _stream_end(source) + offset
            if abs(start - expected_start) > 0.05 or abs(end - expected_end) > 0.05:
                return "imported audio timing does not match the selected mapping"
            for fraction in (0.0, 0.5, 0.95):
                position = max(0, start + (end - start) * fraction)
                args = [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "info",
                    "-xerror",
                    "-seek_timestamp",
                    "1",
                    "-ss",
                    f"{position:.6f}",
                    "-i",
                    staged,
                    "-map",
                    f"0:{index}",
                    "-t",
                    "0.25",
                    "-af",
                    "ashowinfo",
                    "-f",
                    "null",
                    "-",
                ]
                code, stderr = _run_ffmpeg(args, cancel=cancel)
                if cancel.stopped() or code < 0:
                    raise InterruptedError("audio verification was stopped")
                if code or not re.search(r"nb_samples:[1-9]\d*", stderr):
                    return "imported audio sample could not be decoded"
    return None


def apply_plan(
    plan: Plan,
    on_progress: ProgressCallback | None = None,
    on_encoded: Callable[[], None] | None = None,
    cancel: Cancel | None = None,
    claim: Callable[[], bool] | None = None,
) -> tuple[Outcome, str]:
    """Rewrite the file, replacing it only once the result verifies.

    The detail says what went wrong when nothing was replaced; the caller
    logs it. ``on_encoded`` fires when ffmpeg exits, before a cross-filesystem
    publish copies the file. ``cancel`` comes from whoever owns the work; a
    direct call gets one of its own, abortable by name like any other.
    ``claim`` is asked for ownership of the source and the output just before
    the rename, and answers false where a skip arrived while it waited.
    """
    if input_problem := _input_problem(plan):
        return Outcome.FAILED, input_problem
    cancel = cancel or Cancel(plan.path)
    if taken := _target_taken(plan):
        return Outcome.FAILED, taken

    src_before = os.stat(plan.path)
    # A stale plan's stream maps could mangle the new file in ways _verify
    # cannot see.
    if plan.src_signature and SourceSignature.of(src_before) != plan.src_signature:
        return Outcome.DEFERRED, "source changed since it was planned, nothing rewritten"

    if any(stream.dv_strip for stream in plan.streams) and not dv_filter_available():
        return Outcome.FAILED, "Remove Dolby Vision requires FFmpeg with dovi_rpu strip support"

    # Staged after the pre-flight checks, or each return above leaks a file.
    work_dir = config.current().WORK_DIR
    try:
        tmp = _new_temp(work_dir)
    except OSError as err:
        return Outcome.FAILED, f"could not stage the rewrite in {work_dir}: {err}"

    workspace = contextlib.ExitStack()
    try:
        prepared: tuple[PreparedTrack, ...] = ()
        target: dict = {}
        revision = None
        if plan.inputs:
            revision = FileRevision.of(plan.path)
            for item in plan.inputs:
                _check_revision(item.revision)
                if (item.revision.device, item.revision.inode) == (
                    revision.device,
                    revision.inode,
                ):
                    raise ValueError("source audio must come from a different file")
            target = probe(plan.path)
            prepared = workspace.enter_context(
                _prepare_inputs(plan.inputs, plan.policy, cancel)
            )
            plan = _render_plan(plan, prepared)
            needed = src_before.st_size + sum(os.stat(track.path).st_size for track in prepared)
            if shutil.disk_usage(work_dir).free < needed * 1.05 + 1024 * 1024:
                raise OSError(errno.ENOSPC, "not enough space for sourced rendering")
        args = _sourced_args(plan, tmp, prepared) if prepared else ffmpeg_args(plan, tmp)
        log.info("ffmpeg %s", " ".join(args[1:]))
        code, stderr = _run_ffmpeg(args, on_progress, cancel)
        if on_encoded is not None:
            on_encoded()
        if code != 0 and (code < 0 or cancel.stopped()):
            # Stopped, not broken: ffmpeg traps SIGTERM and exits 255, so the
            # exit code alone cannot say which.
            return Outcome.DEFERRED, "the rewrite was stopped, nothing rewritten"
        if code != 0:
            stderr_tail = stderr.strip()[-_STDERR_TAIL:]
            return Outcome.FAILED, f"ffmpeg failed ({code}): {stderr_tail}"

        result = probe(tmp)
        problem = _verify(plan, result) or _verify_dv_frames(plan, tmp)
        if not problem and prepared:
            problem = _verify_sourced(plan, target, prepared, result, tmp, cancel)
        if problem:
            return Outcome.FAILED, f"{problem}, result discarded"

        # Ownership before the checks it makes good: waiting out another edit of
        # the same file is itself a moment the source can change under us.
        if claim is not None and not claim():
            return Outcome.DEFERRED, "the rewrite was stopped before it was published"

        # An upgrade can land mid-rewrite; renaming over it would revert it.
        src_after = os.stat(plan.path)
        if SourceSignature.of(src_after) != SourceSignature.of(src_before):
            return Outcome.DEFERRED, "source changed during the rewrite, result discarded"
        if taken := _target_taken(plan):
            return Outcome.FAILED, taken

        if revision is not None:
            _check_revision(revision)
            for item in plan.inputs:
                _check_revision(item.revision)

        # The last moment a skip can still leave the library file as it was.
        # Past this the rename is under way and a late one has nothing to undo.
        if not cancel.commit():
            return Outcome.DEFERRED, "the rewrite was stopped before it was published"
        _publish(tmp, plan.out_path, src_before)
        if plan.out_path != plan.path:
            # The .mkv is published; a leftover source is the next sweep's.
            try:
                os.remove(plan.path)
            except OSError as err:
                log.warning("could not remove %s after remux: %s", plan.path, err)
        log.info("rewrote %s", plan.out_path)
        return Outcome.APPLIED, ""
    except InterruptedError as err:
        return Outcome.DEFERRED, f"{err}, result discarded"
    except (OSError, ValueError, RuntimeError) as err:
        if not plan.inputs and not isinstance(err, ProbeError):
            raise
        return Outcome.FAILED, f"{err}, result discarded"
    except subprocess.TimeoutExpired:
        return Outcome.FAILED, f"ffmpeg timed out after {config.current().FFMPEG_TIMEOUT}s"
    finally:
        workspace.close()
        # Already gone when _publish renamed it.
        with contextlib.suppress(OSError):
            os.remove(tmp)


def work_dir_errors() -> list[str]:
    """Whether WORK_DIR is usable, as ready-to-log messages. Creates it and
    stages a file to prove the mount is writable."""
    work_dir = config.current().WORK_DIR
    try:
        os.makedirs(work_dir, exist_ok=True)
        probe_path = _new_temp(work_dir)
        os.remove(probe_path)
    except OSError as err:
        return [f"WORK_DIR {work_dir} is not usable: {err}"]
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

    A typo would otherwise surface as the first rewrite failing. Downmixed
    layouts only: the others name no encoder because nothing is made for them.
    A downmix missing one entirely is :func:`trackstarr.policy.errors`' report.
    """
    encoders = audio_encoders()
    if encoders is None:
        return []
    return [
        f"AUDIO_LAYOUTS makes {layout.name} with {layout.codec!r}, which is not an audio "
        "encoder this ffmpeg provides (see ffmpeg -encoders)"
        for layout in downmixed_layouts(config.current().AUDIO_LAYOUTS)
        if layout.codec not in encoders
    ]


def work_dir_is_remote() -> bool:
    """Whether publishing will probably copy rather than rename. Best effort,
    for a startup note only: a union filesystem can fool it."""
    settings = config.current()
    try:
        work_dev = os.stat(settings.WORK_DIR).st_dev
    except OSError:
        return False
    return any(
        os.stat(media_dir).st_dev != work_dev
        for media_dir in settings.MEDIA_DIRS
        if os.path.isdir(media_dir)
    )


def is_staged_file(name: str) -> bool:
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)


def drop_staged(path: str, force: bool = False) -> bool:
    """Remove a staged file left by a crash, if it cannot be in use.

    Anything older than the ffmpeg timeout has outlived its writer. ``force``
    is for a caller holding every rewrite slot, which proves no writer exists.
    """
    with _running_lock:
        if os.path.abspath(path) in _preparing:
            return False
    timeout = config.current().FFMPEG_TIMEOUT
    try:
        if not force and time.time() - os.stat(path).st_mtime <= timeout:
            return False
        if os.path.basename(path).startswith(f"{TEMP_PREFIX}audio-") and os.path.isdir(path):
            shutil.rmtree(path)
        else:
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
    work_dir = config.current().WORK_DIR
    if not work_dir or not os.path.isdir(work_dir):
        return
    for name in os.listdir(work_dir):
        if is_staged_file(name):
            drop_staged(os.path.join(work_dir, name), force=exclusive)
