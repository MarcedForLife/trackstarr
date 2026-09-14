"""Diagnostic queue timings; run with .venv/bin/python tools/queue_benchmark.py.

Use a disposable process, with no dispatcher. Admission time includes tracemalloc;
read timings do not. Compare the same script and environment on both revisions.
Activity includes whatever lifecycle.snapshot publishes on the measured revision.
"""

import gc
import json
import statistics
import time
import tracemalloc

from trackstarr import lifecycle, runs, work


def measure(call, repetitions=60):
    values = []
    for _ in range(repetitions):
        start = time.perf_counter_ns()
        call()
        values.append((time.perf_counter_ns() - start) / 1e6)
    return round(statistics.median(values), 4)


def benchmark(size):
    # No dispatcher exists in this disposable benchmark; discard its backlog.
    work.reset()
    lifecycle.reset()
    runs.reset()
    lifecycle.open_run("bench", runs.SWEEP)
    queue = work.scheduler
    tracemalloc.start()
    start = time.perf_counter()
    for index in range(size):
        queue.submit("bench", f"/shows/{index:06}.mkv", "work", lambda: None)
    admission = time.perf_counter() - start
    gc.collect()
    memory = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    tracemalloc.stop()

    def capture():
        with queue.condition:
            queue._showing()
            queue._capture()

    def poll():
        queue.submit("bench", f"/poll/{len(queue.tasks)}.mkv", "work", lambda: None)
        lifecycle.snapshot()
        queue.snapshot(limit=3)

    print(
        json.dumps(
            {
                "size": size,
                "admission_seconds": round(admission, 3),
                "retained_mib": round(memory, 2),
                "preview_ms": measure(lambda: queue.snapshot(limit=3)),
                "activity_ms": measure(lifecycle.snapshot),
                "count_ms": measure(lambda: queue.count("work")),
                "search_capture_lock_ms": measure(capture),
                "search_ms": measure(lambda: queue.snapshot("999", limit=50), 10),
                "submit_poll_ms": measure(poll),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    for size in (10000, 50000):
        benchmark(size)
