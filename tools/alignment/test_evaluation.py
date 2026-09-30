"""Checks for the research harness, separate from the shipped package's coverage gate."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import evaluate as evaluation
import pytest
from corpus import digest, generate
from episode import prepare, probe
from evaluate import compare, parse_report, portable, run_process


def measurement(**changes):
    return dict(
        schema_version=2,
        mode="audio",
        dry_run=True,
        scale=1,
        sync_ms=0,
        score=12,
        samples=5,
        residual_ms=0,
        segments=[],
        gaps=[],
        **changes,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"scale": 0},
        {"scale": float("nan")},
        {"scale": float("inf")},
        {"scale": True},
        {"samples": 0},
        {"residual_ms": -1},
        {"dry_run": False},
        {"schema_version": 9},
        {"segments": None},
        {"gaps": None},
        {"mode": "subtitles"},
    ],
)
def test_invalid_measurements(changes):
    report = measurement() | changes
    with pytest.raises(ValueError):
        parse_report(json.dumps([report]))


@pytest.mark.parametrize("raw", ["{}", "[]", "null", "not json", "[{}, {}]", "[null]"])
def test_malformed_reports(raw):
    with pytest.raises(ValueError):
        parse_report(raw)


def test_offset_direction_and_inverse_tempo():
    case = {
        "expected": {"scale": "25/24", "offset_us": -1_500_000},
        "source_points_us": [3_000_000, 55_000_000],
    }
    report = measurement() | {"scale": 25 / 24, "sync_ms": -1500}
    assert compare(case, report, 1)["outcome"] == "mapping_recovered"
    assert compare(case, report | {"scale": 24 / 25}, 50_000)["outcome"] == "timing_error"
    assert compare(case, report | {"sync_ms": 1500}, 50_000)["outcome"] == "timing_error"


def test_wrong_cut_and_piecewise_never_pass():
    case = {"expected": None}
    assert compare(case, measurement(), 50_000)["outcome"] == "unsafe_linear_proposal"
    assert compare(case, measurement() | {"gaps": [{}]}, 50_000)["outcome"] == "review_required"
    assert (
        compare(case, measurement() | {"segments": [{}, {}]}, 50_000)["outcome"]
        == "review_required"
    )


@pytest.mark.parametrize("index", [2, -1, True, "2"])
def test_explicit_audio_streams(tmp_path, monkeypatch, index):
    binary = tmp_path / "binary"
    binary.write_bytes(b"test engine")
    monkeypatch.setattr(evaluation, "REDSYNC_SHA256", digest(binary))
    target, source = tmp_path / "target.mkv", tmp_path / "source.mkv"
    target.write_bytes(b"target")
    source.write_bytes(b"source")
    case = {
        "id": "surround",
        "target": target.name,
        "source": source.name,
        "target_audio_stream": index,
        "source_audio_stream": 3,
        "expected": {"scale": "1", "offset_us": 0},
        "source_points_us": [1_000_000],
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tolerance_us": 50_000,
                "files": {p.name: {"sha256": digest(p)} for p in (target, source)},
                "cases": [case],
            }
        )
    )
    calls = []

    def backend(command, workspace, timeout):
        calls.append(command)
        return {"timed_out": False, "returncode": 0, "stdout": json.dumps([measurement()])}

    monkeypatch.setattr(evaluation, "run_process", backend)
    if type(index) is int and index >= 0:
        result = evaluation.evaluate(tmp_path, binary, 5)
        assert result["results"][0]["outcome"] == "mapping_recovered"
        assert calls[0][-4:] == ["--reference-track", "2", "--target-track", "3"]
    else:
        with pytest.raises(ValueError, match="invalid target_audio_stream"):
            evaluation.evaluate(tmp_path, binary, 5)
        assert not calls


def test_timeout_and_output_capture(tmp_path):
    result = run_process([sys.executable, "-c", "print('ok')"], tmp_path, 10)
    assert result["stdout"] == "ok\n" and result["returncode"] == 0
    result = run_process([sys.executable, "-c", "import signal; signal.pause()"], tmp_path, 0.1)
    assert result["timed_out"] and result["returncode"] < 0


def test_resource_measurement(tmp_path):
    metrics = tmp_path / "metrics.json"
    result = run_process(
        [
            sys.executable,
            str(Path(__file__).with_name("measure.py")),
            str(metrics),
            sys.executable,
            "-c",
            "print('measured')",
        ],
        tmp_path,
        10,
    )
    assert result["returncode"] == 0 and result["stdout"] == "measured\n"
    assert json.loads(metrics.read_text())["max_process_rss_kib"] > 0


def test_corpus_timelines_and_preservation(tmp_path):
    root = tmp_path / "corpus"
    manifest = generate(root)
    assert len(manifest["cases"]) == 12
    assert all(
        digest(root / name) == facts["sha256"] for name, facts in manifest["files"].items()
    )
    # Check the media, independently of the manifest's expected mappings.
    durations = {
        name: float(probe(root / f"{name}.mkv")["format"]["duration"])
        for name in ("target", "trim_start", "leading_black", "speed_25_24", "fps_only")
    }
    assert durations["trim_start"] == pytest.approx(durations["target"] - 1.25, abs=0.05)
    assert durations["leading_black"] == pytest.approx(durations["target"] + 1.5, abs=0.05)
    assert durations["speed_25_24"] == pytest.approx(durations["target"] * 24 / 25, abs=0.05)
    assert durations["fps_only"] == pytest.approx(durations["target"], abs=0.05)
    assert probe(root / "fps_only.mkv")["streams"][0]["r_frame_rate"] == "25/1"
    assert probe(root / "surround.mkv")["streams"][1]["channels"] == 6
    with pytest.raises(FileExistsError):
        generate(root)


def test_missing_german_pair(tmp_path):
    episode = tmp_path / "episode.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=d=1:s=32x32",
            "-f",
            "lavfi",
            "-i",
            "sine=d=1",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "1:a",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:1",
            "language=ger",
            str(episode),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    before = digest(episode)
    root = tmp_path / "pair"
    manifest = prepare(episode, root, {"de", "deu", "ger"})
    assert digest(episode) == before
    assert manifest["cases"][0]["expected"] == {"scale": "1", "offset_us": 0}
    assert probe(root / "target.mkv")["streams"][1]["tags"]["language"] == "eng"
    assert probe(root / "source.mkv")["streams"][1]["tags"]["language"] == "ger"

    def decoded_hash(path, stream):
        return subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                stream,
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ],
            timeout=30,
        )

    assert decoded_hash(episode, "0:v:0") == decoded_hash(root / "target.mkv", "0:v:0")
    assert decoded_hash(episode, "0:v:0") == decoded_hash(root / "source.mkv", "0:v:0")
    assert decoded_hash(episode, "0:a:1") == decoded_hash(root / "source.mkv", "0:a:0")
    video_only = tmp_path / "video-only"
    prepare(root / "source.mkv", video_only, {"ger"})
    assert all(s["codec_type"] != "audio" for s in probe(video_only / "target.mkv")["streams"])
    with pytest.raises(ValueError, match="requested dub"):
        prepare(root / "target.mkv", tmp_path / "bad", {"ger"})
    assert not (tmp_path / "bad").exists()


def test_reports_name_the_corpus_temp_and_home_instead_of_host_paths(tmp_path):
    corpus = tmp_path / "corpus"
    workspace = Path(tempfile.gettempdir()) / "trackstarr-visual-1"
    report = {
        "command": [
            str(Path.home() / ".local/bin/ffmpeg"),
            str(corpus / "target.mkv"),
            str(workspace / "measurement.json"),
        ],
    }
    assert json.loads(portable(report, corpus)) == {
        "command": [
            "~/.local/bin/ffmpeg",
            "<corpus>/target.mkv",
            "<tmp>/trackstarr-visual-1/measurement.json",
        ]
    }
