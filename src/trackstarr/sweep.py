"""The library sweep: plan every file, report, and rewrite when asked.

It exists to catch files that arrived without a webhook. One row per
actionable file goes to STATE_DIR/pending.tsv, untouched verdicts go to the
sweep cache, and a summary event goes to the history.
"""

import functools
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from . import config, cron, events
from .arr import LibraryIndex, all_arrs, match_path, path_index
from .executor import drop_staged, is_staged_file
from .planner import describe
from .policy import Policy
from .processing import Job, effective_dry_run, process
from .status import Status
from .sweep_cache import FileKey, SweepCache, Verdict, cache_key

log = logging.getLogger(__name__)

#: The statuses worth a row in pending.tsv.
REPORTED_STATUSES = frozenset({Status.WOULD_FIX, Status.FIXED, Status.DEFERRED, Status.FAILED})

#: Verdicts safe to cache: the file is untouched. Fixed changes it, and a
#: failure may be transient.
CACHEABLE_STATUSES = frozenset({Status.SKIP, Status.CONFORM, Status.WOULD_FIX})


def walk_library(policy: Policy) -> list[str]:
    found: list[str] = []
    for media_dir in config.MEDIA_DIRS:
        if not os.path.isdir(media_dir):
            # os.walk yields nothing here, so a wrong mount would look
            # exactly like an empty library.
            log.warning("media dir %s does not exist", media_dir)
            continue
        for dirpath, dirnames, names in os.walk(media_dir):
            dirnames[:] = [child for child in dirnames if not child.startswith(".")]
            for name in names:
                if policy.allowed_container(name):
                    found.append(os.path.join(dirpath, name))
                elif is_staged_file(name):
                    # Cross-filesystem publishing stages beside the file it
                    # replaces, so a crash scatters these. Nothing else looks.
                    drop_staged(os.path.join(dirpath, name))
    return found


#: Longest pending.tsv cell; a 30-track plan's reasons get cut, not the row.
_CELL_MAX = 400

#: Fewest judging threads, whatever the rewrite budget: the slots don't gate
#: probes, and a budget of 1 must not make a cold sweep probe thousands of
#: files one at a time.
_MIN_PROBE_WORKERS = 4

#: Fewest seconds between mid-sweep checkpoints. Each rewrites the whole
#: cache, so anything more eager would out-cost the sweep on a cached library.
_CHECKPOINT_SECONDS = 60


#: What would break a pending.tsv row, mapped to a space. All three are
#: legal in a filename, and a truncated path is worse than a long row.
_ROW_BREAKERS = str.maketrans({"\t": " ", "\n": " ", "\r": " "})


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
    path: str, index: LibraryIndex, cache: SweepCache, dry_run: bool, run: str
) -> Judged:
    """Decide one file, on a worker thread.

    Reads the cache, never writes it; the sweep books verdicts on one thread.
    Nothing here may raise: an exception inside the pool would abandon every
    file after it.
    """
    try:
        job = Job.from_match(path, match_path(index, path))
        key = cache_key(path, job.lang)
        verdict = cache.lookup(path, key)
        # A cached would-fix stands in for the probe only while reporting;
        # an applying sweep has to rewrite the file.
        if verdict and (dry_run or verdict.status is not Status.WOULD_FIX):
            return Judged(job, key, verdict.status, verdict.reasons, cached=True)
        result = process(job, dry_run, source="sweep", run=run)
        reasons = describe(result.plan) if result.plan else ""
        return Judged(job, key, result.status, reasons, result.detail)
    except Exception as err:
        log.exception("unhandled error judging %s", path)
        return Judged(Job(path), None, Status.FAILED, detail=str(err))


def sweep(dry_run: bool) -> dict[Status, int]:
    asked_for = dry_run
    dry_run = effective_dry_run(dry_run)
    if dry_run and not asked_for:
        log.info("DRY_RUN is set; the sweep reports only")
    policy = Policy.from_config()
    index = path_index(all_arrs())
    if not index.complete and not dry_run:
        # With original languages unknown, the languages rule would read a
        # foreign film's own track as junk to drop. Report-only is the only
        # sweep safe to run until the *arr answers again.
        log.error("a *arr library could not be listed; sweeping report-only, nothing rewritten")
        dry_run = True
    files = walk_library(policy)
    log.info("sweep starting: %d files, dry_run=%s", len(files), dry_run)

    started = time.monotonic()
    last_checkpoint = started
    # Every event this sweep writes carries its start time as the run id, so
    # a night's work groups without window arithmetic.
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
            max_workers=max(_MIN_PROBE_WORKERS, config.MAX_CONCURRENT_REWRITES),
            thread_name_prefix="sweep",
        ) as pool,
    ):
        report_file.write("status\toriginal_lang\tpath\treasons\tdetail\n")
        # map, not as_completed: results arrive in walk order whatever the
        # pool size, so it never reshuffles pending.tsv.
        for i, judged in enumerate(pool.map(judge, files), 1):
            if judged.key:
                library_bytes += judged.key.size
            if judged.cached:
                cached_hits += 1
            # Here rather than in the worker, which keeps the cache
            # single-threaded and needing no lock of its own.
            if judged.status in CACHEABLE_STATUSES:
                cache.record(
                    judged.job.path, judged.key, Verdict(judged.status, judged.reasons)
                )

            counts[judged.status] += 1
            if judged.status in REPORTED_STATUSES:
                report_file.write(
                    f"{judged.status}\t{judged.job.lang or '-'}\t"
                    f"{judged.job.path.translate(_ROW_BREAKERS)}\t"
                    f"{_cell(judged.reasons)}\t{_cell(judged.detail)}\n"
                )
                report_file.flush()
            if i % 500 == 0:
                log.info("  %d/%d ... %s", i, len(files), counts)
            # Time-based: an applying sweep can spend minutes on one file,
            # so a count gate would fire hours apart, or never.
            if time.monotonic() - last_checkpoint >= _CHECKPOINT_SECONDS:
                cache.checkpoint()
                last_checkpoint = time.monotonic()

    cache.save()
    events.record(
        "sweep",
        run=run,
        dry_run=dry_run,
        files=len(files),
        # The one place library size is known, and the full fingerprint every
        # other event's config_id resolves against.
        library_bytes=library_bytes,
        config=policy.fingerprint(),
        config_id=policy.digest(),
        cached=cached_hits,
        counts=counts,
        seconds=round(time.monotonic() - started, 1),
    )
    log.info("sweep done: %s (%d verdicts from cache)", counts, cached_hits)
    log.info("report: %s", report)
    return counts


def seconds_until(schedule: str, now: float | None = None) -> float:
    """Seconds until the cron schedule's next run, in local time.

    Strictly after ``now``, so rescheduling right after a sweep cannot pick
    the slot that just fired.
    """
    now = time.time() if now is None else now
    # DTZ006 suppressed because local time is the point: 03:00 means 03:00
    # in the container's TZ, across DST. Naive on purpose.
    target = cron.next_run(cron.parse(schedule), datetime.fromtimestamp(now))  # noqa: DTZ006
    return target.timestamp() - now


# No cover: a thread body. seconds_until decides when and is covered; this
# sleeps until then and calls sweep.
def scheduler() -> None:  # pragma: no cover
    while True:
        try:
            delay = seconds_until(config.SWEEP_AT)
        except ValueError as err:
            log.error("SWEEP_AT=%r: %s, scheduler stopping", config.SWEEP_AT, err)
            return
        log.info("next sweep in %.1f hours (apply=%s)", delay / 3600, config.SWEEP_APPLY)
        time.sleep(delay)
        try:
            sweep(dry_run=not config.SWEEP_APPLY)
        except Exception:
            log.exception("sweep failed")
