"""Diagnostic verdict-store timings; run with .venv/bin/python tools/verdict_benchmark.py.

Use a disposable process. STATE_DIR is a fresh temporary directory, so nothing
here reads or writes a real library. Entries carry realistic tracks, plans and
reasons, since what is being measured is parsing and rewriting them.

Compare the same script and environment on both revisions. The header line
records the host and versions; the numbers mean nothing without it.
"""

import gc
import json
import os
import platform
import statistics
import tempfile
import threading
import time
import tracemalloc

STATE = tempfile.mkdtemp(prefix="verdict-benchmark-")
os.environ["STATE_DIR"] = STATE

from trackstarr import sweep_cache, verdict_store  # noqa: E402
from trackstarr.policy import Policy  # noqa: E402
from trackstarr.status import Status  # noqa: E402
from trackstarr.sweep_cache import SweepCache, Verdict  # noqa: E402

#: Roughly what a probed film carries: video, two audio layouts, two subtitles.
TRACKS = [
    {"index": 0, "kind": "video", "codec": "hevc", "lang": None, "title": ""},
    {"index": 1, "kind": "audio", "codec": "eac3", "lang": "eng", "channels": 6},
    {"index": 2, "kind": "audio", "codec": "aac", "lang": "eng", "channels": 2},
    {"index": 3, "kind": "subtitle", "codec": "subrip", "lang": "eng", "forced": False},
    {"index": 4, "kind": "subtitle", "codec": "subrip", "lang": "eng", "forced": True},
]
PLANNED = TRACKS[:4]
WHY = {
    "changes": ["add 2.0 downmix from stream 1 (6ch eng)", "drop forced subtitle"],
    "rules": ["downmix", "subtitles"],
}


def measure(call, repetitions=5):
    """The median of `repetitions` runs, in milliseconds."""
    values = []
    for _ in range(repetitions):
        start = time.perf_counter_ns()
        call()
        values.append((time.perf_counter_ns() - start) / 1e6)
    return round(statistics.median(values), 3)


def entries(size):
    """A library's worth of stored verdicts."""
    return {
        f"/media/Film/Title {index:06}/Title {index:06}.mkv": {
            "size": 4_000_000_000 + index,
            "mtime_ns": 1_700_000_000_000_000_000 + index,
            "nlink": 1,
            "lang": "eng",
            "status": "pending" if index % 3 else "conform",
            "reasons": "add 2.0 downmix from stream 1 (6ch eng)",
            "judged": 1_700_000_000,
            "tracks": TRACKS,
            "planned": PLANNED if index % 3 else [],
            "why": WHY if index % 3 else {},
            "duration": 41.5,
        }
        for index in range(size)
    }


def publications(fingerprint, count, first=0):
    """Book `count` verdicts one at a time, as an import burst does, and wait
    for the store to hold them.

    Each needs a real file: a publication verifies the key it books against.
    The flush is inside the timing because the batch is an optimisation, not a
    shorter promise: what is being measured is still durably stored verdicts.
    """
    for index in range(first, first + count):
        path = os.path.join(STATE, f"import-{index}.mkv")
        with open(path, "wb") as made:
            made.write(b"x" * 4096)
        with sweep_cache.observing(path) as observation:
            sweep_cache.publish(
                observation,
                path,
                path,
                sweep_cache.cache_key(path, "eng"),
                Verdict(Status.PENDING, "add 2.0 downmix", TRACKS, PLANNED, WHY, 41.5),
                fingerprint,
            )
    sweep_cache.flush()


def reads_while_publishing(store, fingerprint):
    """Median read against a continuous publisher: what a page waits behind."""
    running = threading.Event()
    running.set()
    published = 0

    def keep_publishing():
        nonlocal published
        while running.is_set():
            publications(fingerprint, 1, 900_000 + published)
            published += 1

    writer = threading.Thread(target=keep_publishing)
    writer.start()
    try:
        return measure(lambda: sweep_cache.read(store, fingerprint))
    finally:
        running.clear()
        writer.join()


def benchmark(store, size):
    fingerprint = Policy.from_config().fingerprint()
    verdict_store.write(store, fingerprint, entries(size))

    parses = 0
    plain_load = verdict_store.load

    def counted(target):
        nonlocal parses
        parses += 1
        return plain_load(target)

    verdict_store.load = counted
    try:
        tracemalloc.start()
        document = verdict_store.load(store)
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] / 1024 / 1024
        tracemalloc.stop()

        # A warm walk: every entry carried forward, nothing new judged.
        walk = SweepCache.load(store, fingerprint)
        for name in document.entries:
            walk.carry(name)

        before = parses
        publications(fingerprint, 1)
        per_publication = parses - before

        print(
            json.dumps(
                {
                    "size": size,
                    "stored_mib": round(os.path.getsize(store) / 1024 / 1024, 2),
                    "retained_mib": round(retained, 2),
                    "parses_per_publication": per_publication,
                    "load_ms": measure(lambda: verdict_store.load(store)),
                    "read_ms": measure(lambda: sweep_cache.read(store, fingerprint)),
                    "publication_ms": measure(lambda: publications(fingerprint, 1, 100)),
                    "burst_of_20_ms": measure(lambda: publications(fingerprint, 20, 200), 3),
                    "checkpoint_ms": measure(walk.keep),
                    "save_ms": measure(walk.save, 3),
                    "view_ms": measure(walk.publish_view),
                    "read_while_publishing_ms": reads_while_publishing(store, fingerprint),
                }
            ),
            flush=True,
        )
    finally:
        verdict_store.load = plain_load


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "host": platform.platform(),
                "cpus": os.cpu_count(),
            }
        ),
        flush=True,
    )
    for library_size in (10000, 50000):
        benchmark(sweep_cache.cache_path(), library_size)
