"""Measure warm title-detail construction and JSON encoding with synthetic copies.

Run from the repository: .venv/bin/python tools/benchmarks/title_detail.py
No server, media, credentials or persistent state are read or modified. Cache,
catalogue and rewrite-history acquisition are stubbed; grouping, rollups, file
projection and JSON serialization use production code. Times exclude network,
disk reads and browser rendering. No machine-dependent pass/fail threshold.
"""

import argparse
import json
import math
import platform
import statistics
import time
from unittest.mock import patch

from trackstarr import library
from trackstarr.sweep_cache import Stored


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", type=positive, default=50_000)
    parser.add_argument("--episodes", type=positive, default=1_000)
    parser.add_argument("--copies", type=positive, default=3)
    parser.add_argument("--repeats", type=positive, default=15)
    args = parser.parse_args()
    titles = [
        library.Title(
            f"copy:{at}",
            "Benchmark series",
            f"/library{at}/Show",
            "series",
            provider_id="tvdb:1",
        )
        for at in range(2)
    ]
    tracks = [
        {"index": 0, "kind": "video", "codec": "hevc"},
        {"index": 1, "kind": "audio", "codec": "eac3", "channels": 6, "lang": "eng"},
        {"index": 2, "kind": "subtitle", "codec": "subrip", "lang": "eng"},
    ]
    entry = {
        "status": "pending",
        "size": 2_000_000_000,
        "duration": 2700,
        "tracks": tracks,
        "planned": tracks,
        "why": {"reasons": ["keep original audio"], "rules": []},
    }
    files = {f"/background/Title{at}/file.mkv": dict(entry) for at in range(args.background)}
    for title in titles:
        for episode in range(args.episodes):
            for copy in range(args.copies):
                path = (
                    f"{title.folder}/Show.S{episode // 100 + 1:03}"
                    f"E{episode % 100 + 1:03}.copy{copy}.mkv"
                )
                files[path] = {**entry, "status": "conform" if copy else "pending"}
    stored = Stored(files, True)
    shelf = library.Shelf(titles, index={title.id: title for title in titles})
    results = []
    with (
        patch.object(library, "_read_cache", return_value=stored),
        patch.object(library, "_shelf", return_value=shelf),
        patch.object(library.rewrites, "against", return_value={}),
    ):
        depths = sorted(
            {1, min(2, math.ceil(args.episodes / 200)), math.ceil(args.episodes / 200)}
        )
        for pages in depths:
            library.title(titles[0].id, pages=pages)
            build, encode = [], []
            for _ in range(args.repeats):
                start = time.perf_counter()
                answer = library.title(titles[0].id, pages=pages)
                middle = time.perf_counter()
                payload = json.dumps(answer).encode()
                end = time.perf_counter()
                build.append((middle - start) * 1000)
                encode.append((end - middle) * 1000)
            total = [a + b for a, b in zip(build, encode, strict=True)]
            results.append(
                {
                    "pages": pages,
                    "returned_files": len(answer["files"]),
                    "json_bytes": len(payload),
                    "build_median_ms": round(statistics.median(build), 2),
                    "encode_median_ms": round(statistics.median(encode), 2),
                    "total_median_ms": round(statistics.median(total), 2),
                    "total_p95_ms": round(sorted(total)[math.ceil(len(total) * 0.95) - 1], 2),
                }
            )
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "parameters": vars(args),
                "total_library_files": len(files),
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
