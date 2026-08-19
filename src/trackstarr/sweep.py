"""The library sweep: plan every file, report, and rewrite when asked.

The sweep exists to catch files that arrived without a webhook. It writes
one row per actionable file to STATE_DIR/pending.tsv, remembers untouched
verdicts in the sweep cache, and leaves a summary event behind.
"""

from __future__ import annotations

import functools
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import config, events
from .arr import LibraryItem, all_arrs, match_path, path_index
from .executor import drop_if_stale, is_staged_file
from .planner import describe
from .policy import Policy
from .processing import Job, process
from .status import Status
from .sweep_cache import FileKey, SweepCache, Verdict, cache_key

log = logging.getLogger(__name__)

#: The statuses worth a row in pending.tsv.
REPORTED_STATUSES = frozenset({Status.WOULD_FIX, Status.FIXED, Status.DEFERRED, Status.FAILED})

#: Verdicts that leave the file untouched, safe for the sweep cache. Fixed
#: changes the file, and failures may be transient.
CACHEABLE_STATUSES = frozenset({Status.SKIP, Status.CONFORM, Status.WOULD_FIX})


def walk_library(policy: Policy) -> list[str]:
    found: list[str] = []
    for root in config.MEDIA_ROOTS:
        if not os.path.isdir(root):
            # os.walk would yield nothing, making a wrong mount or a typo in
            # MEDIA_ROOTS indistinguishable from an empty library.
            log.warning("media root %s does not exist", root)
            continue
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [child for child in dirnames if not child.startswith(".")]
            for name in names:
                if policy.allowed_container(name):
                    found.append(os.path.join(dirpath, name))
                elif is_staged_file(name):
                    # Cross-filesystem publishing lands its copy beside the
                    # file being replaced, so a crash scatters these through
                    # the library rather than into one directory startup
                    # could clear. This walk is the only thing visiting them.
                    drop_if_stale(os.path.join(dirpath, name))
    return found


#: Longest pending.tsv cell; a 30-track plan's reasons get cut, not the row.
_CELL_MAX = 400

#: Fewest seconds between mid-sweep cache checkpoints. Each checkpoint
#: rewrites the whole cache, so on a big, fully cached library (thousands of
#: files per second, no probes) anything more eager would spend more time
#: serializing the cache than sweeping.
_CHECKPOINT_SECONDS = 60


def _cell(text: str) -> str:
    """One pending.tsv cell: tabs and newlines collapsed, bounded."""
    return " ".join(text.split())[:_CELL_MAX]


@dataclass(frozen=True)
class Judged:
    """One file's verdict, and what the sweep needs to book it."""

    job: Job
    key: FileKey | None
    status: Status
    reasons: str = ""
    detail: str = ""
    #: The verdict came from the cache, so the file was never probed.
    cached: bool = False


def _judge(
    path: str, index: list[LibraryItem], cache: SweepCache, dry_run: bool, run: str
) -> Judged:
    """Decide one file, on a worker thread.

    Reads the cache but never writes it; the sweep records verdicts as it
    books them, on one thread. Nothing here is allowed to raise: this runs
    inside a pool, where an exception would abandon every file after it.
    """
    try:
        matched = match_path(index, path)
        job = Job(path, matched.lang, matched.item_id, matched.arr) if matched else Job(path)
        key = cache_key(path, job.lang)
        verdict = cache.lookup(path, key)
        # A cached would-fix only stands in for the probe while reporting; an
        # applying sweep must rewrite the file.
        if verdict and (dry_run or verdict.status is not Status.WOULD_FIX):
            return Judged(job, key, verdict.status, verdict.reasons, cached=True)
        result = process(job, dry_run, source="sweep", run=run)
        reasons = describe(result.plan) if result.plan else ""
        return Judged(job, key, result.status, reasons, result.detail)
    except Exception as err:
        log.exception("unhandled error judging %s", path)
        return Judged(Job(path), None, Status.FAILED, detail=str(err))


def sweep(dry_run: bool) -> dict[str, int]:
    if config.DRY_RUN and not dry_run:
        log.info("DRY_RUN is set; the sweep reports only")
        dry_run = True
    policy = Policy.from_config()
    index = path_index(all_arrs())
    files = walk_library(policy)
    log.info("sweep starting: %d files, dry_run=%s", len(files), dry_run)

    started = time.monotonic()
    last_checkpoint = started
    # Every event this sweep writes carries its start time as the run id, so
    # a night's work groups without window arithmetic and a crashed sweep's
    # orphaned events still say when their run began.
    run = events.timestamp()
    counts = dict.fromkeys(Status, 0)
    cached_hits = 0
    library_bytes = 0
    os.makedirs(config.STATE_DIR, exist_ok=True)
    report = os.path.join(config.STATE_DIR, "pending.tsv")
    cache = SweepCache.load(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), policy.fingerprint()
    )

    judge = functools.partial(_judge, index=index, cache=cache, dry_run=dry_run, run=run)
    with (
        open(report, "w") as report_file,
        ThreadPoolExecutor(
            max_workers=config.MAX_CONCURRENT_REWRITES, thread_name_prefix="sweep"
        ) as pool,
    ):
        report_file.write("status\toriginal_lang\tpath\treasons\tdetail\n")
        # map, not as_completed: results arrive in walk order however many
        # workers there are, so raising the budget doesn't reshuffle
        # pending.tsv and a one-worker sweep behaves exactly as it always did.
        for i, judged in enumerate(pool.map(judge, files), 1):
            if judged.key:
                library_bytes += judged.key.size
            if judged.cached:
                cached_hits += 1
            # Recording here rather than in the worker keeps the cache
            # single-threaded, so it needs no lock of its own.
            if judged.cached or judged.status in CACHEABLE_STATUSES:
                cache.record(
                    judged.job.path, judged.key, Verdict(judged.status, judged.reasons)
                )

            counts[judged.status] += 1
            if judged.status in REPORTED_STATUSES:
                report_file.write(
                    f"{judged.status}\t{judged.job.lang or '-'}\t{judged.job.path}\t"
                    f"{_cell(judged.reasons)}\t{_cell(judged.detail)}\n"
                )
                report_file.flush()
            if i % 500 == 0:
                log.info("  %d/%d ... %s", i, len(files), counts)
            # Time-based, not per-N-files: an applying sweep can spend
            # minutes on one file, so a count gate would space checkpoints
            # hours apart and never fire at all on a small library.
            if time.monotonic() - last_checkpoint >= _CHECKPOINT_SECONDS:
                cache.checkpoint()
                last_checkpoint = time.monotonic()

    cache.save()
    events.record(
        "sweep",
        run=run,
        dry_run=dry_run,
        files=len(files),
        # The one place library size is known, so growth can be plotted; and
        # the settings the run judged with, so a churny night is explicable
        # years later (rule changes queue rewrites across the library).
        library_bytes=library_bytes,
        config=policy.fingerprint(),
        cached=cached_hits,
        counts=counts,
        seconds=round(time.monotonic() - started, 1),
    )
    log.info("sweep done: %s (%d verdicts from cache)", counts, cached_hits)
    log.info("report: %s", report)
    return counts


#: A target closer than this counts as just fired and rolls to tomorrow, so
#: the scheduler rescheduling right after a sweep never picks the same slot.
_MIN_LEAD_SECONDS = 30


def seconds_until(hhmm: str, now: float | None = None) -> float:
    """Seconds until the next occurrence of HH:MM local time."""
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    now = time.time() if now is None else now
    local = time.localtime(now)
    target = time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1)
    )
    delay = target - now
    return delay if delay > _MIN_LEAD_SECONDS else delay + 86400


def scheduler() -> None:
    while True:
        try:
            delay = seconds_until(config.SWEEP_AT)
        except ValueError:
            log.error("SWEEP_AT=%r is not HH:MM, scheduler stopping", config.SWEEP_AT)
            return
        log.info("next sweep in %.1f hours (apply=%s)", delay / 3600, config.SWEEP_APPLY)
        time.sleep(delay)
        try:
            sweep(dry_run=not config.SWEEP_APPLY)
        except Exception:
            log.exception("sweep failed")
