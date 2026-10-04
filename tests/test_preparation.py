"""Source preparation owns its reads and temporary audio, never the library."""

import array
import os
import subprocess
import sys
import threading
import time
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import set_config, set_layouts
from trackstarr import executor, mkvtag
from trackstarr.media import ProbeError, probe
from trackstarr.planner import FileRevision, PlanInput, TimingMapping
from trackstarr.policy import Policy


@pytest.fixture(autouse=True)
def preparation_settings(tmp_path):
    set_config(WORK_DIR=str(tmp_path / "work"))
    set_layouts("2.0:aac:192k")
    yield
    assert not executor._source_reads
    assert not executor._preparing


def prepared(inputs, cancel=None):
    return executor._prepare_inputs(inputs, Policy.from_config(), cancel or executor.Cancel())


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "source.mkv"
    path.write_bytes(b"source bytes")
    info = {
        "streams": [
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "flac",
                "channels": 2,
                "sample_rate": "48000",
                "start_time": "0",
            }
        ],
        "format": {"duration": "4"},
    }
    result = {
        "streams": [{"index": 0, "codec_type": "audio", "channels": 2}],
        "format": {"duration": "4"},
    }
    calls = []

    def run(args, on_progress=None, cancel=None):
        calls.append(args)
        assert executor.is_rewriting(str(path))
        Path(args[-1]).write_bytes(b"prepared bytes")
        return 0, ""

    monkeypatch.setattr(executor, "probe", lambda name: info if name == str(path) else result)
    monkeypatch.setattr(executor, "_run_ffmpeg", run)
    return SimpleNamespace(
        path=path,
        input=PlanInput(FileRevision.of(str(path)), 1),
        info=info,
        result=result,
        calls=calls,
    )


@pytest.mark.parametrize(
    "scale,offset",
    [(Fraction(0), 0), (Fraction(-1), 0), (1.01, 0), (Fraction(1), 0.5), (Fraction(1), True)],
)
def test_mapping_requires_positive_rational_scale_and_integer_offset(scale, offset):
    with pytest.raises(ValueError):
        TimingMapping(scale, offset)


def test_contracts_are_immutable_and_revisions_resolve_aliases(source, tmp_path):
    alias = tmp_path / "alias.mkv"
    alias.symlink_to(source.path)
    assert FileRevision.of(str(alias)) == source.input.revision
    with pytest.raises(FrozenInstanceError):
        source.input.mapping.offset_us = 1
    with pytest.raises(ValueError, match="non-negative"):
        replace(source.input, stream_index=-1)


@pytest.mark.parametrize("relative", [False, True])
def test_workspace_lifetime_and_live_cleanup(source, monkeypatch, relative):
    if relative:
        monkeypatch.chdir(source.path.parent)
        set_config(WORK_DIR="work")
    with prepared([source.input]) as tracks:
        (track,) = tracks
        assert track.source == source.input
        assert not track.encode
        path = Path(track.path)
        assert path.read_bytes() == b"prepared bytes"
        assert not executor.is_rewriting(str(source.path))
        assert executor.drop_staged(str(path.parent), force=True) is False
        executor.clean_work_dir(exclusive=True)
        assert path.exists()
    assert not path.parent.exists()
    assert source.path.read_bytes() == b"source bytes"


def test_empty_inputs_do_not_create_workspace(tmp_path):
    with prepared([]) as tracks:
        assert tracks == ()
    assert not (tmp_path / "work").exists()


def test_workspace_cleaned_after_consumer_failure(source):
    with pytest.raises(RuntimeError, match="render failed"), prepared([source.input]) as tracks:
        path = Path(tracks[0].path)
        raise RuntimeError("render failed")
    assert not path.parent.exists()


@pytest.mark.parametrize("replace_first", [False, True])
def test_two_sources_with_same_stream_index_and_recheck_before_render(
    source, monkeypatch, replace_first
):
    second = source.path.with_name("second.mkv")
    second.write_bytes(b"second source")
    inputs = [source.input, PlanInput(FileRevision.of(str(second)), 1)]
    monkeypatch.setattr(
        executor,
        "probe",
        lambda path: source.info if path in (str(source.path), str(second)) else source.result,
    )

    def run(args, **kwargs):
        path = args[args.index("-i") + 1]
        assert executor.is_rewriting(path)
        assert args[args.index("-map") + 1] == "0:1"
        Path(args[-1]).write_bytes(Path(path).read_bytes())
        if path == str(second) and replace_first:
            source.path.write_bytes(b"replaced during second preparation")
        return 0, ""

    monkeypatch.setattr(executor, "_run_ffmpeg", run)
    if replace_first:
        with pytest.raises(InterruptedError, match="source changed"), prepared(inputs):
            pytest.fail("stale first source accepted")
    else:
        with prepared(inputs) as tracks:
            assert [track.source for track in tracks] == inputs
            assert [Path(track.path).read_bytes() for track in tracks] == [
                b"source bytes",
                b"second source",
            ]
    assert not list(source.path.parent.joinpath("work").iterdir())


@pytest.mark.parametrize("change", ["replace", "retag", "delete"])
@pytest.mark.parametrize("during", [False, True])
def test_stale_source_is_discarded_even_with_same_size_and_mtime(
    source, monkeypatch, change, during
):
    before = source.path.stat()

    def change_source():
        if change == "replace":
            other = source.path.with_suffix(".new")
            other.write_bytes(b"changed data")
            os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))
            other.replace(source.path)
        elif change == "retag":
            source.path.write_bytes(b"changed data")
            os.utime(source.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        else:
            source.path.unlink()

    if during:
        run = executor._run_ffmpeg

        def changed(args, **kwargs):
            value = run(args, **kwargs)
            change_source()
            return value

        monkeypatch.setattr(executor, "_run_ffmpeg", changed)
    else:
        change_source()
    with pytest.raises(InterruptedError, match="source"), prepared([source.input]):
        pytest.fail("stale preparation must never be handed to the renderer")
    assert list(source.path.parent.joinpath("work").iterdir()) == []


def test_guard_counts_readers_and_covers_aliases(source, tmp_path):
    alias = tmp_path / "alias.mkv"
    alias.symlink_to(source.path)
    hardlink = tmp_path / "hardlink.mkv"
    os.link(source.path, hardlink)
    revision = FileRevision.of(str(source.path))
    other = tmp_path / "other.mkv"
    other.touch()
    with executor._read_source(revision):
        assert executor.is_rewriting(str(alias))
        assert executor.is_rewriting(str(hardlink))
        assert not executor.is_rewriting(str(other))
        assert not executor.is_rewriting(str(tmp_path / "gone.mkv"))
        assert not executor.is_rewriting("")
        with executor._read_source(revision):
            assert executor._source_reads[revision] == 2
        assert executor.is_rewriting(str(source.path))
    assert not executor.is_rewriting(str(source.path))


def test_tag_edit_rechecks_read_guard_under_shared_lock(source):
    with executor._read_source(source.input.revision):
        result = mkvtag.edit_track(str(source.path), 1, mkvtag.Edit(lang="fra"))
    assert result.status == mkvtag.Outcome.REFUSED


def test_source_registration_waits_for_in_progress_header_edit(source, monkeypatch):
    reached = threading.Event()
    registered = threading.Event()
    problems = []
    real_check = executor._check_revision

    def checked(revision):
        assert reached.is_set()
        real_check(revision)

    monkeypatch.setattr(executor, "_check_revision", checked)

    def read():
        try:
            reached.set()
            with executor._read_source(source.input.revision):
                registered.set()
        except BaseException as err:
            problems.append(err)

    with executor._edit_lock:
        worker = threading.Thread(target=read)
        worker.start()
        assert reached.wait(2)
        assert not registered.is_set()
    worker.join(2)
    assert not worker.is_alive()
    assert not problems
    assert registered.is_set()


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "video",
        "immersive",
        "encoder",
        "scale",
        "duration",
        "space",
        "count",
        "kind",
        "channels",
        "empty",
    ],
)
def test_unsupported_or_invalid_preparation_is_cleaned(source, monkeypatch, problem):
    item = replace(source.input, mapping=TimingMapping(Fraction(1001, 960), -100_000))
    stream = source.info["streams"][0]
    if problem == "missing":
        source.info["streams"] = []
    elif problem == "video":
        stream["codec_type"] = "video"
    elif problem == "immersive":
        stream["codec_name"] = "eac3"
    elif problem == "encoder":
        set_layouts("2.0:keep")
    elif problem == "scale":
        item = replace(item, mapping=TimingMapping(Fraction(3)))
    elif problem == "duration":
        source.info["format"]["duration"] = "0"
    elif problem == "space":
        monkeypatch.setattr(executor.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    elif problem == "count":
        source.result["streams"] = []
    elif problem == "kind":
        source.result["streams"][0]["codec_type"] = "video"
    elif problem == "channels":
        source.result["streams"][0]["channels"] = 1
    else:
        source.result["format"]["duration"] = "0"
    with pytest.raises((ValueError, OSError)), prepared([item]):
        pytest.fail("invalid preparation accepted")
    assert not list(source.path.parent.joinpath("work").iterdir())


@pytest.mark.parametrize(
    "failure", ["cancel_before", "cancel_during", "signal", "error", "timeout", "probe"]
)
def test_process_failure_and_cancellation_clean_workspace(source, monkeypatch, failure):
    cancel = executor.Cancel()
    if failure == "cancel_before":
        cancel.ask()

    def run(args, **kwargs):
        if failure == "cancel_during":
            cancel.ask()
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        if failure == "probe":
            raise ProbeError("bad source")
        return (-15 if failure == "signal" else 1), "broken"

    monkeypatch.setattr(executor, "_run_ffmpeg", run)
    with (
        pytest.raises((InterruptedError, RuntimeError, subprocess.TimeoutExpired)),
        prepared([source.input], cancel),
    ):
        pytest.fail("failed preparation accepted")
    assert not list(source.path.parent.joinpath("work").iterdir())


def test_cancel_after_preparation_is_not_handed_to_renderer(source, monkeypatch):
    cancel = executor.Cancel()
    check = executor._check_revision
    checks = []

    def checked(revision):
        check(revision)
        checks.append(revision)
        if len(checks) == 3:
            cancel.ask()

    monkeypatch.setattr(executor, "_check_revision", checked)
    with pytest.raises(InterruptedError), prepared([source.input], cancel):
        pytest.fail("cancelled preparation accepted")


def test_changed_source_takes_precedence_over_a_failed_read(source, monkeypatch):
    def failed(args, **kwargs):
        source.path.unlink()
        return 1, "read failed"

    monkeypatch.setattr(executor, "_run_ffmpeg", failed)
    with pytest.raises(InterruptedError, match="unavailable"), prepared([source.input]):
        pytest.fail("missing source should be replanned")


def test_cancellation_reaches_descendants_before_workspace_cleanup(monkeypatch):
    set_config(FFMPEG_TIMEOUT=5)
    read_fd, write_fd = os.pipe()
    spawned = threading.Event()
    popen = subprocess.Popen
    cancel = executor.Cancel()
    results = []

    def start(args, **kwargs):
        kwargs["pass_fds"] = (write_fd,)
        process = popen(args, **kwargs)
        spawned.set()
        return process

    monkeypatch.setattr(executor.subprocess, "Popen", start)
    child = (
        "import os, signal, sys; "
        "signal.signal(signal.SIGTERM, "
        f"lambda *_: (os.write({write_fd}, b'stopped'), sys.exit())); "
        f"os.write({write_fd}, b'ready'); signal.pause()"
    )
    parent = (
        "import subprocess, sys; "
        f"subprocess.run([sys.executable, '-c', {child!r}], pass_fds=({write_fd},))"
    )

    def run():
        try:
            results.append(executor._run_ffmpeg([sys.executable, "-c", parent], cancel=cancel))
        except BaseException as err:
            results.append(err)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert spawned.wait(2)
        os.close(write_fd)
        assert os.read(read_fd, 5) == b"ready"
        executor.terminate_phase(cancel)
        assert os.read(read_fd, 7) == b"stopped"
    finally:
        executor.terminate_phase(cancel)
        worker.join(6)
        os.close(read_fd)
    assert not worker.is_alive()
    assert isinstance(results[0], tuple)
    assert results[0][0] < 0


def test_pcm_retiming_retains_channels_and_requires_final_encoding(source):
    source.info["streams"][0]["codec_name"] = "pcm_s24le"
    item = replace(source.input, mapping=TimingMapping(Fraction(1001, 960)))
    with prepared([item]) as tracks:
        assert tracks[0].encode


def test_orphan_cleanup_includes_preparation_directories(tmp_path):
    workspace = tmp_path / "work" / ".trackstarr-audio-orphan.partial"
    workspace.mkdir(parents=True)
    (workspace / "0.mka").write_bytes(b"partial")
    old = time.time() - 1 - executor.config.current().FFMPEG_TIMEOUT
    os.utime(workspace, (old, old))
    executor.clean_work_dir()
    assert not workspace.exists()


@pytest.fixture
def real_source(tmp_path):
    path = tmp_path / "real.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=size=16x16:duration=4",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=if(between(t\\,1\\,1.1)+between(t\\,3\\,3.1)\\,0.7*sin(2*PI*440*t)\\,0):s=48000:d=4",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "ffv1",
            "-c:a",
            "flac",
            "-ac",
            "2",
            "-af",
            "asetpts=PTS+0.5/TB",
            "-metadata:s:a:0",
            "language=fra",
            "-metadata:s:a:0",
            "title=French",
            str(path),
        ],
        check=True,
    )
    return PlanInput(FileRevision.of(str(path)), 1)


def samples(path):
    data = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-f",
            "f64le",
            "-c:a",
            "pcm_f64le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    result = array.array("d")
    result.frombytes(data)
    return result[::2]


@pytest.mark.parametrize(
    "scale,offset",
    [
        (Fraction(1), 0),
        (Fraction(1), 250_000),
        (Fraction(1), -250_000),
        (Fraction(1), -750_000),
        (Fraction(1001, 960), 200_000),
        (Fraction(960, 1001), -250_000),
    ],
)
def test_generated_audio_uses_container_timeline_and_preserves_source(
    real_source, scale, offset
):
    item = replace(real_source, mapping=TimingMapping(scale, offset))
    original = Path(item.revision.path).read_bytes()
    with prepared([item]) as tracks:
        track = tracks[0]
        info = probe(track.path)
        stream = info["streams"][0]
        assert stream["index"] == 0
        assert stream["channels"] == 2
        assert stream["tags"]["language"] == "fra"
        assert stream["tags"]["title"] == "French"
        decoded = samples(track.path)
        start = float(stream["start_time"])
        expected_start = max(0, float(scale) * 0.5 + offset / 1_000_000)
        assert start == pytest.approx(expected_start, abs=0.002)
        expected_end = float(scale) * 4.5 + offset / 1_000_000
        assert float(info["format"]["duration"]) == pytest.approx(expected_end, abs=0.04)
        for position in (1.5, 3.5):
            expected = float(scale) * position + offset / 1_000_000
            lower = max(0, int((expected - start - 0.06) * 48000))
            upper = int((expected - start + 0.16) * 48000)
            assert max(abs(value) for value in decoded[lower:upper]) > 0.3
            first = next(index for index in range(lower, upper) if abs(decoded[index]) > 0.1)
            assert start + first / 48000 == pytest.approx(expected, abs=0.04)
        if not track.encode:
            assert decoded == samples(item.revision.path)
        else:
            assert stream["codec_name"] == "pcm_f64le"
            if scale == 1:
                assert decoded == samples(item.revision.path)[12000:]
    assert Path(item.revision.path).read_bytes() == original
    assert not Path(track.path).exists()
