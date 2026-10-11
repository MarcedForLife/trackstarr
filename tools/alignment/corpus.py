"""Generate disposable timing fixtures. No library media or extra Python dependencies."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

DURATION = 60
ENCODING = ["-c:v", "ffv1", "-level", "3", "-c:a", "pcm_s16le", "-threads", "1"]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ffmpeg(arguments: list[str], output: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-n", *arguments, *ENCODING, str(output)],
        check=True,
        capture_output=True,
        timeout=180,
    )


def generate(root: Path) -> dict:
    # A new directory avoids overwriting media or leaving a stale manifest after a failure.
    root.mkdir(parents=True, exist_ok=False)
    target = root / "target.mkv"
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate=24:duration={DURATION}",
            "-f",
            "lavfi",
            "-i",
            (
                "aevalsrc=0.2*sin(2*PI*(173*t+11*t*t))"
                f"+0.1*sin(2*PI*(487*t+3*t*t)):s=48000:d={DURATION}"
            ),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-metadata:s:a:0",
            "language=eng",
        ],
        target,
    )
    cases = []

    def add(name, video="null", audio="anull", scale="1", offset_us=0, **extra):
        path = root / f"{name}.mkv"
        ffmpeg(
            ["-i", str(target), "-vf", video, "-af", audio, "-fps_mode", "passthrough"], path
        )
        # Points are chosen from fixture ground truth, never from an engine's fit anchors.
        expected = None if scale is None else {"scale": scale, "offset_us": offset_us}
        cases.append(
            {
                "id": name,
                "target": target.name,
                "source": path.name,
                "expected": expected,
                "source_points_us": [3_000_000, 17_000_000, 33_000_000, 55_000_000],
                **extra,
            }
        )

    add("identity")
    add(
        "trim_start",
        "trim=start=1.25,setpts=PTS-STARTPTS",
        "atrim=start=1.25,asetpts=PTS-STARTPTS",
        offset_us=1_250_000,
    )
    add(
        "leading_black",
        "tpad=start_duration=1.5:color=black",
        "adelay=1500:all=1",
        offset_us=-1_500_000,
    )
    add("speed_25_24", "setpts=24/25*PTS", "atempo=25/24", scale="25/24")
    add(
        "speed_and_trim",
        "trim=start=1.25,setpts=24/25*(PTS-STARTPTS)",
        "atrim=start=1.25,asetpts=PTS-STARTPTS,atempo=25/24",
        scale="25/24",
        offset_us=1_250_000,
    )
    add("fps_only", "fps=25")
    add("crop", "crop=288:156:16:12,scale=320:180")
    add("surround", audio="pan=5.1|FL=c0|FR=c0|FC=c0|LFE=0*c0|BL=c0|BR=c0")
    add(
        "different_audio",
        audio="aeval=0.3*sin(2*PI*(911*t+7*t*t))",
        note="Unrelated synthetic audio under matching video, not a real dub.",
    )
    add(
        "internal_cut",
        "select='not(between(t,24,26))',setpts=N/(24*TB)",
        "aselect='not(between(t,24,26))',asetpts=N/SR/TB",
        scale=None,
    )
    add(
        "wrong_video_same_audio",
        "reverse",
        scale=None,
        note="An audio-only engine cannot establish visual correspondence.",
    )
    add("black_silence", "drawbox=color=black:t=fill", "volume=0", scale=None)
    manifest = {
        "schema_version": 1,
        "timeline": "target_time = scale * source_time + offset_us",
        "time_unit": "microseconds",
        "duration_seconds": DURATION,
        "tolerance_us": 50_000,
        "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
        "files": {
            p.name: {"sha256": digest(p), "bytes": p.stat().st_size}
            for p in sorted(root.glob("*.mkv"))
        },
        "cases": cases,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    generate(args.directory.resolve())
