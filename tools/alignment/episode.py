"""Prepare a missing-dub pair from one episode, using stream copies in a new directory."""

import argparse
import json
import subprocess
from pathlib import Path

from corpus import digest


def probe(path: Path) -> dict:
    return json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            timeout=60,
        )
    )


def prepare(episode: Path, root: Path, languages: set[str]) -> dict:
    facts = probe(episode)
    streams = facts["streams"]
    video = [s["index"] for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    selected = [
        s["index"] for s in audio if s.get("tags", {}).get("language", "").lower() in languages
    ]
    retained = [s["index"] for s in audio if s["index"] not in selected]
    if not video or not selected:
        raise ValueError("episode needs video and the requested dub")
    original_hash = digest(episode)
    root.mkdir(parents=True, exist_ok=False)
    target = root / "target.mkv"
    source = root / "source.mkv"
    for output, indexes in ((target, retained), (source, selected)):
        maps = [arg for index in [video[0], *indexes] for arg in ("-map", f"0:{index}")]
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-copyts",
                "-i",
                str(episode),
                *maps,
                "-map_metadata",
                "0",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "disabled",
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=1800,
        )
    if digest(episode) != original_hash:
        raise ValueError("episode changed during fixture preparation")
    manifest = {
        "schema_version": 1,
        "timeline": "target_time = scale * source_time + offset_us",
        "time_unit": "microseconds",
        "tolerance_us": 50_000,
        "origin": {"path": str(episode), "sha256": original_hash},
        "removed_language_tags": sorted(languages),
        "files": {
            p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in (target, source)
        },
        "cases": [
            {
                "id": "missing_dub",
                "target": target.name,
                "source": source.name,
                "expected": {"scale": "1", "offset_us": 0},
                "source_points_us": [
                    int(float(facts["format"]["duration"]) * f * 1_000_000)
                    for f in (0.05, 0.3, 0.6, 0.95)
                ],
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--language-tags", default="de,deu,ger")
    args = parser.parse_args()
    prepare(
        args.episode.resolve(), args.directory.resolve(), set(args.language_tags.split(","))
    )
