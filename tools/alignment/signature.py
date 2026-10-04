"""Measure FFmpeg's native signature report without constructing an alignment engine."""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from corpus import digest
from evaluate import portable, run_process

MATCH = re.compile(
    r"matching of video 0 at (-?\d+\.\d+) and 1 at (-?\d+\.\d+), (\d+) frames matching"
)


def parse_measurement(stderr: str) -> dict:
    matches = MATCH.findall(stderr)
    absent = "no matching of video 0 and 1" in stderr
    if absent and not matches:
        return {"matched": False}
    if len(matches) != 1 or absent:
        raise ValueError("expected one native signature result for two inputs")
    target, source, frames = matches[0]
    if int(frames) <= 0:
        raise ValueError("invalid matching frame count")
    return {
        "matched": True,
        "target_time_us": int(Fraction(target) * 1_000_000),
        "source_time_us": int(Fraction(source) * 1_000_000),
        "matching_frames": int(frames),
        "whole_video_message": "whole video matching" in stderr,
    }


def diagnose(case: dict, measurement: dict) -> dict:
    # Ground truth scores the observation, never supplies a scale to the backend.
    if not measurement["matched"]:
        return {"outcome": "no_match"}
    expected = case["expected"]
    if expected is None:
        return {"outcome": "match_on_negative_case"}
    error = abs(
        measurement["target_time_us"]
        - Fraction(expected["scale"]) * measurement["source_time_us"]
        - expected["offset_us"]
    )
    return {
        "outcome": "single_pair_only",
        "ground_truth_pair_error_us": float(error),
        "observed_target_minus_source_us": (
            measurement["target_time_us"] - measurement["source_time_us"]
        ),
    }


def command_for(binary: str, corpus: Path, case: dict) -> list[str]:
    # Preserve input timestamps. No resampling or guessed speed transform.
    return [
        binary,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-copyts",
        "-threads",
        "1",
        "-i",
        str(corpus / case["target"]),
        "-threads",
        "1",
        "-i",
        str(corpus / case["source"]),
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        "[0:v:0][1:v:0]signature=nb_inputs=2:detectmode=full[v]",
        "-map",
        "[v]",
        "-an",
        "-fps_mode",
        "passthrough",
        "-f",
        "null",
        "-",
    ]


def evaluate(corpus: Path, binary: str, timeout: float) -> dict:
    version = subprocess.check_output([binary, "-version"], text=True, timeout=10)
    help_text = subprocess.check_output(
        [binary, "-hide_banner", "-h", "filter=signature"],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    if "detectmode" not in help_text:
        raise ValueError("FFmpeg signature filter unavailable")
    manifest = json.loads((corpus / "manifest.json").read_text())
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
            with tempfile.TemporaryDirectory(prefix="trackstarr-signature-") as directory:
                workspace = Path(directory)
                command = command_for(binary, corpus, case)
                metrics = workspace / "resources"
                result = run_process(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("measure.py")),
                        str(metrics),
                        *command,
                    ],
                    workspace,
                    timeout,
                )
                result["command"] = command
                if metrics.exists() and not result["timed_out"]:
                    result.update(json.loads(metrics.read_text()))
                if result["timed_out"]:
                    result["outcome"] = "timeout"
                elif result["returncode"] != 0:
                    result["outcome"] = "backend_error"
                else:
                    try:
                        measurement = parse_measurement(result["stderr"])
                        result["measurement"] = measurement
                        result.update(diagnose(case, measurement))
                    except ValueError as error:
                        result.update(outcome="unrecognised_report", reason=str(error))
                result["workspace_bytes_at_exit"] = sum(
                    p.stat().st_size for p in workspace.rglob("*") if p.is_file()
                )
                result["case"] = case["id"]
                results.append(result)
                print(f"{case['id']}: {result['outcome']}", flush=True)
    finally:
        for name, facts in manifest["files"].items():
            if digest(corpus / name) != facts["sha256"]:
                raise ValueError(f"backend modified fixture: {name}")
    return {
        "schema_version": 1,
        "engine": "FFmpeg signature",
        "ffmpeg_version": version,
        "binary_sha256": digest(Path(binary)),
        "manifest_sha256": digest(corpus / "manifest.json"),
        "automatic_publication": False,
        "mapping_recovered": False,
        "evidence_limit": "One timestamp pair and a frame count, no fitted timing scale.",
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    executable = shutil.which(args.ffmpeg)
    if executable is None:
        parser.error("FFmpeg must already be installed")
    with args.report.open("x") as output:
        corpus = args.corpus.resolve()
        output.write(portable(evaluate(corpus, executable, args.timeout), corpus))
