"""Evidence checks must reject ambiguity and inconsistent timelines independently of truth."""

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import avsync_core
import pytest
from avsync_core import load_core
from compare_visual import score
from corpus import digest
from evaluate import run_process
from visual import Frame, assess, extract, fingerprint, match, read_signatures


def frames():
    rng = random.Random(7)  # noqa: S311 - deterministic synthetic fingerprints
    return [
        Frame(i * 100_000, 50, *fingerprint([rng.randrange(3) for _ in range(380)]))
        for i in range(192)
    ]


def test_ternary_distance():
    a = [0, 1, 2, 0, 2] * 76
    b = [1, 1, 0, 2, 2] * 76
    al, au = fingerprint(a)
    bl, bu = fingerprint(b)
    assert (al ^ bl).bit_count() + (au ^ bu).bit_count() == sum(
        abs(x - y) for x, y in zip(a, b, strict=True)
    )


@pytest.mark.parametrize("values", [[0] * 379, [3] * 380, [-1] * 380])
def test_bad_fingerprint(values):
    with pytest.raises(ValueError):
        fingerprint(values)


def test_offset_speed_and_nonzero_container_timestamps():
    source = frames()
    target = [
        Frame(round(f.time_us * 25 / 24) + 1_250_000, f.confidence, f.lower, f.upper)
        for f in source
    ]
    result = match(target, source)
    assert result["status"] == "proposed"
    assert result["mapping"]["scale"] == pytest.approx(25 / 24, abs=1e-7)
    assert result["mapping"]["offset_us"] == pytest.approx(1_250_000, abs=1)
    assert result["max_validation_residual_us"] < 1
    assert len({a["region"] for a in result["anchors"] if a["role"] == "validation"}) == 4


def test_repeated_content_is_ambiguous_even_with_perfect_matches():
    source = frames()
    target = source + [
        Frame(f.time_us + 19_200_000, f.confidence, f.lower, f.upper) for f in source
    ]
    result = match(target, source)
    assert result["status"] == "review_required"
    assert not result["anchors"]
    assert all(p["reason"] == "ambiguous_match" for p in result["probes"])


@pytest.mark.parametrize("negative", ["reverse", "cut", "black", "unrelated"])
def test_wrong_picture_or_cut_never_proposed(negative):
    target = frames()
    if negative == "reverse":
        source = [
            Frame(f.time_us, g.confidence, g.lower, g.upper)
            for f, g in zip(target, reversed(target), strict=True)
        ]
    elif negative == "cut":
        source = [
            Frame(f.time_us - (2_000_000 if i >= 96 else 0), f.confidence, f.lower, f.upper)
            for i, f in enumerate(target)
            if not 76 <= i < 96
        ]
    elif negative == "black":
        source = [Frame(f.time_us, 0, 0, 0) for f in target]
    else:
        source = [Frame(f.time_us, 50, f.upper, f.lower) for f in target]
    assert match(target, source)["status"] == "review_required"


def test_held_out_regions_cannot_change_fit_or_be_discarded():
    anchors = [
        {"source_time_us": i * 1_000_000, "target_time_us": i * 1_000_000} for i in range(80)
    ]
    for a in anchors:
        if (a["source_time_us"] // 10_000_000) % 2:
            a["target_time_us"] += 500_000
    result = assess(anchors, (0, 80_000_000), (0, 80_000_000))
    assert result["mapping"] == {"scale": 1, "offset_us": 0}
    assert result["reason"] == "inconsistent_timeline"
    assert result["max_fit_residual_us"] == 0
    assert result["max_validation_residual_us"] == 500_000
    assert len(result["anchors"]) == 80


def test_sparse_local_match_is_not_whole_file_evidence():
    anchors = [
        {"source_time_us": i * 100_000, "target_time_us": i * 100_000} for i in range(10)
    ]
    assert assess(anchors, (0, 60_000_000), (0, 60_000_000))["status"] == "review_required"


def xml(times=(0, 1000), unit=1000000):
    signature = " ".join(["1"] * 380)
    return (
        f"<Mpeg7><MediaTimeUnit>{unit}</MediaTimeUnit>"
        + "".join(
            f"<VideoFrame><MediaTimeOfFrame>{t}</MediaTimeOfFrame>"
            f"<FrameConfidence>30</FrameConfidence><FrameSignature>{signature}</FrameSignature>"
            "</VideoFrame>"
            for t in times
        )
        + "</Mpeg7>"
    )


@pytest.mark.parametrize(
    "raw",
    [
        xml((100, 100)),
        xml((100, 0)),
        xml(unit=1000),
        xml((0, 2**64 - 1)),
        "<!DOCTYPE foo><Mpeg7/>",
        xml((0,)),
    ],
)
def test_malformed_export(tmp_path, raw):
    path = tmp_path / "bad.xml"
    path.write_text(raw)
    with pytest.raises(ValueError):
        read_signatures(path)


def test_extract_preserves_pts_and_removes_audio_from_analysis(tmp_path):
    media = tmp_path / "offset.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=24:duration=2",
            "-vf",
            "setpts=PTS+2/TB",
            "-c:v",
            "ffv1",
            "-threads",
            "1",
            str(media),
        ],
        check=True,
        timeout=20,
    )
    result = extract(shutil.which("ffmpeg"), media, tmp_path / "signature.xml")
    assert result[0].time_us == 2_000_000
    assert len(result) == 48
    assert result[-1].time_us == 3_958_000


def test_avsync_pin_checked_before_any_dependency_import(tmp_path):
    path = tmp_path / "upstream.py"
    path.write_text("raise RuntimeError('must not execute')")
    with pytest.raises(ValueError, match="pinned"):
        load_core(path, "ffmpeg", "ffprobe")


def test_avsync_isolation_compatibility_and_failed_extraction(tmp_path, monkeypatch):
    path = tmp_path / "upstream.py"
    path.write_text(
        "raise RuntimeError('top-level workflow must not execute')\n"
        "def run_ffmpeg(command, **kwargs):\n"
        "    return ('fail' not in command, command)\n"
        + "".join(
            f"def {name}(): pass\n"
            for name in sorted(avsync_core.FUNCTIONS)
            if name != "run_ffmpeg"
        )
    )
    monkeypatch.setattr(avsync_core, "SOURCE_SHA256", digest(path))

    class Dependency:
        tqdm = None

    monkeypatch.setattr(avsync_core.importlib, "import_module", lambda _: Dependency())
    core = load_core(path, "ffmpeg", "ffprobe")
    assert core["run_ffmpeg"](["ffmpeg", "-vsync", "vfr"])[1] == ["ffmpeg", "-fps_mode", "vfr"]
    with pytest.raises(RuntimeError, match="extraction failed"):
        core["run_ffmpeg"](["fail"])


def test_timeout_kills_descendants(tmp_path):
    child_pid = tmp_path / "child.pid"
    child_code = (
        "import os, signal; from pathlib import Path; "
        f"Path({str(child_pid)!r}).write_text(str(os.getpid())); signal.pause()"
    )
    parent_code = (
        f"import subprocess, sys; subprocess.run([sys.executable, '-c', {child_code!r}])"
    )
    result = run_process([sys.executable, "-c", parent_code], tmp_path, 1)
    assert result["timed_out"]
    pid = int(child_pid.read_text())
    stat = Path(f"/proc/{pid}/stat")
    # A killed child can remain a zombie briefly until PID 1 reaps it.
    assert not stat.exists() or stat.read_text().split(") ", 1)[1].split()[0] == "Z"


def test_ground_truth_can_only_score_a_proposal():
    mapping = {"scale": 1, "offset_us": 0}
    assert score({"expected": None}, {"status": "proposed", "mapping": mapping}, 50_000) == {
        "outcome": "unsafe_linear_proposal"
    }
    assert score({}, {"status": "review_required", "mapping": mapping}, 50_000) == {
        "outcome": "review_required"
    }


def test_worker_failure_is_recorded_and_report_cannot_be_overwritten(tmp_path):
    media = tmp_path / "broken.mkv"
    media.write_text("not media")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tolerance_us": 50_000,
                "files": {media.name: {"sha256": digest(media)}},
                "cases": [{"id": "broken", "target": media.name, "source": media.name}],
            }
        )
    )
    report = tmp_path / "report.json"
    command = [
        sys.executable,
        str(Path(__file__).with_name("compare_visual.py")),
        "run",
        "--engine",
        "fingerprints",
        "--corpus",
        str(tmp_path),
        "--report",
        str(report),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=20)
    result = json.loads(report.read_text())
    assert not result["automatic_publication"]
    assert result["results"][0]["outcome"] == "backend_error"
    saved = report.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=20).returncode != 0
    assert report.read_bytes() == saved
