"""Offline RedSync evaluation. Measurements never authorise publication."""

import argparse
import contextlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from corpus import digest

REDSYNC_REVISION = "cb07566f9c48766d8f335df0caab157f5342569a"
REDSYNC_SHA256 = "3aa61215fe4534301a28c9d4d35291d61e7db925ad1556abde9eb1a8fb634acd"


def parse_report(raw: str) -> dict:
    reports = json.loads(raw)
    if not isinstance(reports, list) or len(reports) != 1:
        raise ValueError("expected exactly one RedSync report")
    report = reports[0]
    if not isinstance(report, dict) or report.get("schema_version") != 2:
        raise ValueError("unsupported RedSync schema")
    if report.get("dry_run") is not True or report.get("mode") != "audio":
        raise ValueError("expected audio analysis without rendering")
    for key in ("scale", "sync_ms", "score", "samples", "residual_ms"):
        value = report.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"invalid {key}")
    if report["scale"] <= 0 or report["samples"] < 1 or report["residual_ms"] < 0:
        raise ValueError("invalid measurement")
    if not isinstance(report.get("segments"), list) or not isinstance(report.get("gaps"), list):
        raise ValueError("missing segment or gap evidence")
    return report


def compare(case: dict, report: dict, tolerance_us: int) -> dict:
    if report["gaps"] or len(report["segments"]) > 1:
        return {"outcome": "review_required", "reason": "discontinuous_mapping"}
    expected = case["expected"]
    if expected is None:
        return {"outcome": "unsafe_linear_proposal"}
    scale = Fraction(str(report["scale"]))
    offset = Fraction(str(report["sync_ms"])) * 1000
    errors = [
        float(
            abs((scale - Fraction(expected["scale"])) * point + offset - expected["offset_us"])
        )
        for point in case["source_points_us"]
    ]
    return {
        "outcome": "mapping_recovered" if max(errors) <= tolerance_us else "timing_error",
        "point_errors_us": errors,
        "max_error_us": max(errors),
    }


def run_process(command: list[str], workspace: Path, timeout: float) -> dict:
    env = dict(os.environ, TMPDIR=str(workspace), XDG_CACHE_HOME=str(workspace / "cache"))
    started = time.monotonic()
    # File-backed output avoids retaining an unbounded backend log in memory.
    with (
        (workspace / "stdout").open("w+") as stdout,
        (workspace / "stderr").open("w+") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=env,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # Kill descendants as well, including on Ctrl-C or when their parent exited first.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        stdout.seek(0)
        stderr.seek(0)
        return {
            "returncode": process.returncode,
            "timed_out": timed_out,
            "wall_seconds": round(time.monotonic() - started, 3),
            "stdout": stdout.read(1_000_000),
            "stderr": stderr.read(64_000),
        }


def evaluate(corpus: Path, binary: Path, timeout: float) -> dict:
    if digest(binary) != REDSYNC_SHA256:
        raise ValueError("expected the pinned RedSync v0.2.2 Linux x64 binary")
    # Preflight prevents RedSync's fallback FFmpeg download on the tested audio path.
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            raise ValueError(f"{executable} must already be installed")
    manifest = json.loads((corpus / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported corpus schema")
    for name, facts in manifest["files"].items():
        if Path(name).name != name or digest(corpus / name) != facts["sha256"]:
            raise ValueError(f"fixture changed: {name}")
    results = []
    for case in manifest["cases"]:
        if any(case[key] not in manifest["files"] for key in ("target", "source")):
            raise ValueError("case references an unverified file")
        with tempfile.TemporaryDirectory(prefix="trackstarr-alignment-") as directory:
            workspace = Path(directory)
            command = [
                str(binary),
                "sync",
                str(corpus / case["target"]),
                str(corpus / case["source"]),
                "--dry-run",
                "--json",
                "--output",
                str(workspace / "unused.mka"),
            ]
            for key, flag in (
                ("target_audio_stream", "--reference-track"),
                ("source_audio_stream", "--target-track"),
            ):
                if key in case:
                    index = case[key]
                    if type(index) is not int or index < 0:
                        raise ValueError(f"invalid {key}")
                    command.extend([flag, str(index)])
            metrics = workspace / "resources"
            measured_command = [
                sys.executable,
                str(Path(__file__).with_name("measure.py")),
                str(metrics),
                *command,
            ]
            result = run_process(measured_command, workspace, timeout)
            result["command"] = command
            if metrics.exists() and not result["timed_out"]:
                result.update(json.loads(metrics.read_text()))
            if result["timed_out"]:
                result["outcome"] = "timeout"
            elif result["returncode"] != 0:
                # Upstream uses one exit code for both refusals and technical failures.
                result["outcome"] = "backend_error_or_refusal"
            else:
                try:
                    report = parse_report(result["stdout"])
                    result.update(compare(case, report, manifest["tolerance_us"]))
                    result["measurement"] = report
                except (ValueError, TypeError, KeyError) as error:
                    result.update(outcome="malformed_report", reason=str(error))
            if (workspace / "unused.mka").exists():
                result["outcome"] = "unexpected_output"
            result["workspace_bytes_at_exit"] = sum(
                p.stat().st_size for p in workspace.rglob("*") if p.is_file()
            )
            result["case"] = case["id"]
            results.append(result)
            print(f"{case['id']}: {result['outcome']}", flush=True)
    for name, facts in manifest["files"].items():
        if digest(corpus / name) != facts["sha256"]:
            raise ValueError(f"backend modified fixture: {name}")
    return {
        "schema_version": 1,
        "engine": "RedSync v0.2.2",
        "source_revision": REDSYNC_REVISION,
        "binary_sha256": digest(binary),
        "binary_bytes": binary.stat().st_size,
        "manifest_sha256": digest(corpus / "manifest.json"),
        "automatic_publication": False,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("binary", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    # Reserve the report first so an existing result is never overwritten.
    with args.report.open("x") as output:
        json.dump(
            evaluate(args.corpus.resolve(), args.binary.resolve(), args.timeout),
            output,
            indent=2,
        )
        output.write("\n")
