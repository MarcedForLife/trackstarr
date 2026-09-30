"""Re-encode a pair's source as a stand-in for an independent release. Video only."""

import argparse
import json
import subprocess
from fractions import Fraction
from pathlib import Path

from corpus import digest

TRIM_SECONDS = 2
# A lower-quality release: another size, scaler, grade and encoder, starting late.
RELEASE = (
    "scale=1280:-2:flags=lanczos,eq=contrast=1.05:saturation=1.1:gamma=0.95,"
    f"trim=start={TRIM_SECONDS},setpts=PTS-STARTPTS"
)
#: Variant name to the scale its ground truth expects.
VARIANTS = {"reencode_trim": "1", "reencode_pal": "25/24"}
ENCODING = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-an"]
FFMPEG = ["ffmpeg", "-nostdin", "-v", "error", "-n"]


def frame_times(path: Path, count: int) -> list[Fraction]:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=pts_time",
            "-read_intervals",
            f"%+#{count}",
            "-of",
            "csv=p=0",
            str(path),
        ],
        text=True,
        timeout=120,
    )
    return [Fraction(line.strip().rstrip(",")) for line in raw.splitlines() if line.strip()]


def duration(path: Path) -> float:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        text=True,
        timeout=60,
    )
    return float(raw)


def prepare(pair: Path, root: Path) -> dict:
    facts = json.loads((pair / "manifest.json").read_text())
    for name in ("target.mkv", "source.mkv"):
        if digest(pair / name) != facts["files"][name]["sha256"]:
            raise ValueError(f"pair fixture changed: {name}")
    source = pair / "source.mkv"
    root.mkdir(parents=True, exist_ok=False)
    (root / "target.mkv").symlink_to(pair / "target.mkv")
    trim = root / "reencode_trim.mkv"
    subprocess.run(
        [*FFMPEG, "-i", str(source), "-map", "0:v:0", "-vf", RELEASE, *ENCODING, str(trim)],
        check=True,
        capture_output=True,
        timeout=7200,
    )
    # The same pictures played 25 where 24 stood. Re-encoding at a 1/24 time base
    # would drop every 25th frame instead.
    subprocess.run(
        [
            *FFMPEG,
            *("-itsscale", "0.96", "-i", str(trim)),
            *("-map", "0:v:0", "-c", "copy", str(root / "reencode_pal.mkv")),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    if digest(source) != facts["files"]["source.mkv"]["sha256"]:
        raise ValueError("source changed during re-encoding")
    # Target and source share one timeline, so the first kept source frame is target time.
    times = frame_times(source, 24 * (TRIM_SECONDS + 1))
    kept = next(t for t in times if t - times[0] >= TRIM_SECONDS)
    cases = []
    for name, scale in VARIANTS.items():
        variant = root / f"{name}.mkv"
        first = frame_times(variant, 1)[0]
        cases.append(
            {
                "id": name,
                "target": "target.mkv",
                "source": variant.name,
                "expected": {
                    "scale": scale,
                    "offset_us": round((kept - Fraction(scale) * first) * 1_000_000),
                },
                "source_points_us": [
                    int(duration(variant) * fraction * 1_000_000)
                    for fraction in (0.05, 0.3, 0.6, 0.95)
                ],
            }
        )
    manifest = {
        "schema_version": 1,
        "timeline": "target_time = scale * source_time + offset_us",
        "time_unit": "microseconds",
        "tolerance_us": 50_000,
        "derived_from": facts["files"],
        "release": {"filter": RELEASE, "encoding": ENCODING},
        "files": {
            p.name: {"sha256": digest(p), "bytes": p.stat().st_size}
            for p in (root / "target.mkv", *(root / f"{name}.mkv" for name in VARIANTS))
        },
        "cases": cases,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pair", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    prepare(args.pair.resolve(), args.directory.resolve())
