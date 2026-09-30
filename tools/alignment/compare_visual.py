"""Compare visual research backends in disposable, cancellable worker processes."""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from avsync_core import analyse
from consensus import CONSENSUS_SETTINGS, match_consensus
from corpus import digest
from episode_signatures import EPISODE_SETTINGS, extract_episode
from evaluate import run_process
from temporal import TEMPORAL_SETTINGS, match_temporal
from visual import SETTINGS, extract, match

MATCHERS = {"fingerprints": match, "temporal": match_temporal, "consensus": match_consensus}


def video_bounds(ffprobe: str, path: Path) -> tuple[float, float]:
    raw = subprocess.check_output(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=start_time:format=duration",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        timeout=10,
    )
    data = json.loads(raw)
    start = float(data["streams"][0]["start_time"]) * 1_000_000
    duration = float(data["format"]["duration"]) * 1_000_000
    if not math.isfinite(start) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid video timeline")
    return start, start + duration


def worker(args) -> None:
    measurement: dict
    target, source = args.target.resolve(), args.source.resolve()
    target_bounds = video_bounds(args.ffprobe, target)
    source_bounds = video_bounds(args.ffprobe, source)
    if args.whole_episodes:
        measurement = match_consensus(
            extract_episode(args.ffmpeg, target, Path.cwd() / "target.xml"),
            extract_episode(args.ffmpeg, source, Path.cwd() / "source.xml"),
        )
    elif max(b - a for a, b in (target_bounds, source_bounds)) > (
        SETTINGS["max_duration_seconds"] * 1_000_000
    ):
        measurement = {"status": "unsupported", "reason": "duration_exceeds_research_bound"}
    elif args.engine in MATCHERS:
        matcher = MATCHERS[args.engine]
        measurement = matcher(
            extract(args.ffmpeg, target, Path.cwd() / "target.xml"),
            extract(args.ffmpeg, source, Path.cwd() / "source.xml"),
        )
    else:
        measurement = analyse(
            args.avsync_source,
            args.ffmpeg,
            args.ffprobe,
            target,
            source,
            Path.cwd(),
            target_bounds,
            source_bounds,
        )
    measurement["automatic_publication"] = False
    args.report.write_text(json.dumps(measurement, indent=2) + "\n")


def score(case: dict, measurement: dict, tolerance: int) -> dict:
    # Ground truth is never passed to the worker or used to fit/accept its mapping.
    status = measurement["status"]
    if status != "proposed":
        return {"outcome": status}
    if case["expected"] is None:
        return {"outcome": "unsafe_linear_proposal"}
    mapping, expected = measurement["mapping"], case["expected"]
    errors = [
        abs(
            (mapping["scale"] - float(Fraction(expected["scale"]))) * p
            + mapping["offset_us"]
            - expected["offset_us"]
        )
        for p in case["source_points_us"]
    ]
    return {
        "outcome": "mapping_recovered" if max(errors) <= tolerance else "timing_error",
        "point_errors_us": errors,
        "max_error_us": max(errors),
    }


def evaluate(args) -> dict:
    corpus = args.corpus.resolve()
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported corpus schema")
    for name, facts in manifest["files"].items():
        if Path(name).name != name or digest(corpus / name) != facts["sha256"]:
            raise ValueError(f"fixture changed: {name}")
    results = []
    try:
        for case in manifest["cases"]:
            if any(case[key] not in manifest["files"] for key in ("target", "source")):
                raise ValueError("case references an unverified file")
            with tempfile.TemporaryDirectory(prefix="trackstarr-visual-") as directory:
                workspace = Path(directory)
                report, metrics = workspace / "measurement.json", workspace / "resources.json"
                command = [
                    args.python,
                    str(Path(__file__).resolve()),
                    "worker",
                    "--engine",
                    args.engine,
                    "--target",
                    str(corpus / case["target"]),
                    "--source",
                    str(corpus / case["source"]),
                    "--report",
                    str(report),
                    "--ffmpeg",
                    args.ffmpeg,
                    "--ffprobe",
                    args.ffprobe,
                ]
                if args.avsync_source:
                    command.extend(["--avsync-source", str(args.avsync_source.resolve())])
                if args.whole_episodes:
                    command.append("--whole-episodes")
                result = run_process(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("measure.py").resolve()),
                        str(metrics),
                        *command,
                    ],
                    workspace,
                    args.timeout,
                )
                result.update(case=case["id"], command=command)
                if metrics.exists() and not result["timed_out"]:
                    result.update(json.loads(metrics.read_text()))
                if result["timed_out"]:
                    result["outcome"] = "timeout"
                elif result["returncode"] != 0:
                    result["outcome"] = "backend_error"
                else:
                    measurement = json.loads(report.read_text())
                    result["measurement"] = measurement
                    result.update(score(case, measurement, manifest["tolerance_us"]))
                result["workspace_bytes_at_exit"] = sum(
                    p.stat().st_size for p in workspace.rglob("*") if p.is_file()
                )
                results.append(result)
                print(f"{case['id']}: {result['outcome']}", flush=True)
    finally:
        for name, facts in manifest["files"].items():
            if digest(corpus / name) != facts["sha256"]:
                raise ValueError(f"backend modified fixture: {name}")
    return {
        "schema_version": 1,
        "engine": args.engine,
        "automatic_publication": False,
        "settings": SETTINGS,
        "temporal_settings": TEMPORAL_SETTINGS if args.engine == "temporal" else None,
        "consensus_settings": CONSENSUS_SETTINGS if args.engine == "consensus" else None,
        "episode_settings": EPISODE_SETTINGS if args.whole_episodes else None,
        "manifest_sha256": digest(manifest_path),
        "ffmpeg_version": subprocess.check_output([args.ffmpeg, "-version"], text=True),
        "binary_sha256": digest(Path(args.ffmpeg)),
        "implementation_sha256": {
            name: digest(Path(__file__).with_name(name))
            for name in (
                "visual.py",
                "temporal.py",
                "consensus.py",
                "episode_signatures.py",
                "avsync_core.py",
                "compare_visual.py",
            )
        },
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "worker"])
    parser.add_argument("--engine", choices=[*MATCHERS, "avsync"], required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--avsync-source", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--timeout", type=float, default=120)
    # Consensus only: the other engines hold every frame pair or a scale window per frame.
    parser.add_argument("--whole-episodes", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    for key in ("python", "ffmpeg", "ffprobe"):
        binary = shutil.which(getattr(args, key))
        if binary is None:
            parser.error(f"{key} must already be installed")
        # Resolving a venv's Python symlink bypasses its site-packages.
        setattr(args, key, str(Path(binary).absolute()))
    if args.whole_episodes and args.engine != "consensus":
        parser.error("--whole-episodes requires --engine consensus")
    if args.engine == "avsync" and args.avsync_source is None:
        parser.error("avsync requires --avsync-source pointing to pinned AVSync_v14.py")
    if args.mode == "worker":
        if args.target is None or args.source is None:
            parser.error("worker requires --target and --source")
        worker(args)
    else:
        if args.corpus is None:
            parser.error("run requires --corpus")
        with args.report.open("x") as output:
            json.dump(evaluate(args), output, indent=2)
            output.write("\n")
