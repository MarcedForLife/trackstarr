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
VARIANTS = ("reencode_trim", "reencode_pal")
#: PAL plays 25 frames where film had 24, or 23.976 from a 1001 rate.
FILM_RATES = (Fraction(24), Fraction(24000, 1001))
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


def probe_value(path: Path, entry: str, stream: bool = True) -> Fraction:
    selection = ["-select_streams", "v:0"] if stream else []
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            *selection,
            "-show_entries",
            entry,
            "-of",
            "csv=p=0",
            str(path),
        ],
        text=True,
        timeout=60,
    )
    return Fraction(raw.strip().rstrip(","))


def prepare(pair: Path, root: Path) -> dict:
    facts = json.loads((pair / "manifest.json").read_text())
    for name in ("target.mkv", "source.mkv"):
        if digest(pair / name) != facts["files"][name]["sha256"]:
            raise ValueError(f"pair fixture changed: {name}")
    source = pair / "source.mkv"
    rate = probe_value(source, "stream=r_frame_rate")
    if rate not in FILM_RATES:
        raise ValueError(f"expected a film frame rate, found {rate}")
    scales = {"reencode_trim": Fraction(1), "reencode_pal": Fraction(25) / rate}
    root.mkdir(parents=True, exist_ok=False)
    (root / "target.mkv").symlink_to(pair / "target.mkv")
    trim = root / "reencode_trim.mkv"
    subprocess.run(
        [*FFMPEG, "-i", str(source), "-map", "0:v:0", "-vf", RELEASE, *ENCODING, str(trim)],
        check=True,
        capture_output=True,
        timeout=7200,
    )
    # The same pictures played at 25 fps. Re-encoding at the film time base would drop
    # every 25th frame instead.
    subprocess.run(
        [
            *FFMPEG,
            *("-itsscale", f"{float(rate / 25):.12f}", "-i", str(trim)),
            *("-map", "0:v:0", "-c", "copy", str(root / "reencode_pal.mkv")),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    if digest(source) != facts["files"]["source.mkv"]["sha256"]:
        raise ValueError("source changed during re-encoding")
    # FFmpeg's trim counts from the container start, which audio can set before the video.
    # Target and source share one timeline, so the first kept source frame is target time.
    start = probe_value(source, "format=start_time", stream=False)
    times = frame_times(source, 24 * (TRIM_SECONDS + 1))
    kept = min(t for t in times if t - start >= TRIM_SECONDS)
    cases = []
    for name, scale in scales.items():
        variant = root / f"{name}.mkv"
        first = frame_times(variant, 1)[0]
        cases.append(
            {
                "id": name,
                "target": "target.mkv",
                "source": variant.name,
                "expected": {
                    "scale": str(scale),
                    "offset_us": round((kept - scale * first) * 1_000_000),
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
