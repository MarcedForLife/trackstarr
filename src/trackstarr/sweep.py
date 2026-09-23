"""The library sweep: plan every file, report, and rewrite when asked.

Catches files that arrived without a webhook. Actionable files get a row in
STATE_DIR/pending.tsv, verdicts go to the sweep cache, and a summary event goes
to the history.
"""

import contextlib
import functools
import logging
import os
import queue
import time
import zoneinfo
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime

from . import (
    config,
    cron,
    estimate,
    events,
    lifecycle,
    notify,
    pauses,
    rewrites,
    runs,
    sweep_cache,
    work,
)
from .arr import LibraryIndex, all_arrs, match_path, path_index
from .executor import Cancel, drop_staged, is_staged_file
from .policy import Policy
from .processing import (
    Job,
    ProcessResult,
    Rewritten,
    effective_dry_run,
    process,
    verdict_of,
)
from .status import Status
from .sweep_cache import FileKey, SweepCache, Verdict, cache_key, cache_path

log = logging.getLogger(__name__)

#: The statuses worth a row in pending.tsv.
REPORTED_STATUSES = frozenset({Status.PENDING, Status.MODIFIED, Status.DEFERRED, Status.FAILED})

#: Verdicts that describe the file as it stands. A rewrite changes the file, so
#: its entry would be keyed to a size and mtime that no longer exist; deferred
#: says nothing about the file. Failed is stored so the library can show it, but
#: not trusted; see :data:`RETRY_STATUSES`.
CACHEABLE_STATUSES = frozenset(
    {Status.SKIP, Status.UNSUPPORTED, Status.CONFORM, Status.PENDING, Status.FAILED}
)

#: Stored, but never a cache hit while an attempt is left. A failure records
#: one attempt (full disk, missing work dir, an ffmpeg crash), not a property
#: of the file.
RETRY_STATUSES = frozenset({Status.FAILED})

#: How many times a rewrite of one unchanged file may fail before the sweep
#: stops trying. An ffmpeg exit code does not say whether the failure was the
#: moment or the file; retrying covers the first, the bound covers the second.
#: A change on disk, or clearing the cache, starts the count over.
MAX_FAILURES = 3


def _walk(policy: Policy, roots: list[str], noun: str) -> list[str]:
    """Every video file under ``roots``, whether or not the rules act on it.

    Wider than ALLOWED_EXTS on purpose: a container the rules never rewrite
    still gets a verdict, so a title made of them is not a poster with no files
    under it. Judging one costs a stat, since the plan stops at the extension.

    ``noun`` names a missing root in the log: "media dir" for a sweep, "title
    folder" for a re-check.
    """
    found: list[str] = []
    for root in roots:
        if not os.path.isdir(root):
            # os.walk yields nothing here, so a wrong mount would look like an
            # empty library.
            log.warning("%s %s does not exist", noun, root)
            continue
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [child for child in dirnames if not child.startswith(".")]
            for name in names:
                if policy.is_video(name):
                    found.append(os.path.join(dirpath, name))
                elif is_staged_file(name):
                    # Cross-filesystem publishing stages beside the target, so
                    # a crash leaves these behind. Nothing else cleans them up.
                    drop_staged(os.path.join(dirpath, name))
    return found


def walk_library(policy: Policy) -> list[str]:
    return _walk(policy, config.current().MEDIA_DIRS, "media dir")


#: Longest pending.tsv cell; a 30-track plan's reasons get cut, not the row.
_CELL_MAX = 400

#: Fewest seconds between mid-sweep checkpoints. Each rewrites the whole cache,
#: but only when something new was recorded, so a warm sweep pays nothing.
_CHECKPOINT_SECONDS = 30

#: Fewest seconds between a walk publishing its verdicts to open pages. Not
#: shared with :data:`trackstarr.runs._MOVED_SECONDS`: same value, different
#: meaning.
_PUBLISH_SECONDS = 2.0


def _publisher(cache: SweepCache) -> Callable[[], None]:
    """A callable that announces the library moved, at most every
    :data:`_PUBLISH_SECONDS`.

    The view is built before the message goes out, so a page acting on it sees
    what it was about. A warm sweep has nothing new and says nothing.
    """
    # None rather than 0: time.monotonic() counts from boot, so a walk in a
    # container's first seconds would otherwise miss its first message.
    last: float | None = None

    def publish() -> None:
        nonlocal last
        now = time.monotonic()
        if last is not None and now - last < _PUBLISH_SECONDS:
            return
        if cache.publish_view():
            last = now
            notify.publish(notify.LIBRARY)

    return publish


#: Characters that would break a pending.tsv row, mapped to a space. All three
#: are legal in a filename.
_ROW_BREAKERS = str.maketrans({"\t": " ", "\n": " ", "\r": " "})


def _cell(text: str) -> str:
    """One pending.tsv cell: tabs and newlines collapsed, bounded."""
    return " ".join(text.split())[:_CELL_MAX]


@dataclass(frozen=True)
class Judged:
    """One file's verdict, and what the sweep needs to book it."""

    job: Job
    key: FileKey | None
    verdict: Verdict
    detail: str = ""
    #: The published file's own verdict after a rewrite. ``verdict`` above says
    #: this file was rewritten; this is what the library stores in its place.
    became: Rewritten | None = None
    #: The verdict came from the cache, so the file was never probed.
    cached: bool = False
    #: A queue control prevented observation. Count the displayed outcome, but
    #: carry any saved entry through pruning without publishing a verdict.
    unobserved: bool = False
    #: The sweep stopped before reaching this file. Not counted, reported or
    #: cached.
    skipped: bool = False


def _cancelled(status: Status, cancel: Cancel | None) -> bool:
    """Whether a skip is what stopped this rewrite. A mark that landed too late
    to prevent the publication is not one."""
    return status is Status.DEFERRED and cancel is not None and cancel.stopped()


def _judge(
    path: str,
    index: LibraryIndex,
    cache: SweepCache,
    dry_run: bool,
    run: str,
    force: bool = False,
    rewriting: bool | None = None,
    policy: Policy | None = None,
    expected: float = 0.0,
    cancel: Cancel | None = None,
    observation: sweep_cache.Observation | None = None,
) -> Judged:
    """Judge one file on a worker thread. Never raises, or the pool would
    abandon every file after it.

    The caller publishes before releasing the worker claim; booking only counts.

    ``rewriting`` is whether this sweep will rewrite the file, here or later on
    another thread; defaults to ``not dry_run``. They differ in an applying
    sweep's discovery pass, where a file out of attempts must be answered from
    the stored verdict rather than queued for a rewrite that would give up.

    ``force`` skips the stored verdict and the retry ceiling: a re-check would
    otherwise hand back the verdict being questioned.

    A pause is waited out here, before the probe, so resuming picks the walk
    up mid-library.

    ``expected`` is the seconds the rewrite was queued under, for the page's
    readout while ffmpeg has yet to report one.
    """
    if work.scheduler.skipped(run, path):
        return Judged(
            Job(path, run=run),
            None,
            Verdict(Status.DEFERRED),
            detail="skipped for this run",
            unobserved=True,
        )
    if not lifecycle.hold(run):
        return Judged(Job(path), None, Verdict(Status.SKIP), skipped=True)
    if rewriting is None:
        rewriting = not dry_run
    try:
        job = Job.from_match(path, match_path(index, path), run=run)
        key = cache_key(path, job.lang)
        if not force and (verdict := cache.lookup(path, key)) is not None:
            # A cached pending verdict only stands in while reporting; an
            # applying sweep must rewrite the file.
            if verdict.status not in RETRY_STATUSES:
                if dry_run or verdict.status is not Status.PENDING:
                    return Judged(job, key, verdict, cached=True)
            # A cached failure stands in only once the attempts are spent; the
            # file is still counted and reported. Reporting sweeps spend none,
            # so they keep retrying.
            elif rewriting and verdict.failures >= MAX_FAILURES:
                log.debug("%s has failed %d times, leaving it", path, verdict.failures)
                # The stored failure's words, so the report row says what went
                # wrong and that nothing is still trying.
                gave_up = f"failed {verdict.failures} times, not retried until the file changes"
                if stored := verdict.why.get("failed"):
                    gave_up = f"{stored} ({gave_up})"
                return Judged(job, key, verdict, gave_up, cached=True)
        runs.begin(run, path, expected)
        try:
            result = process(
                job,
                dry_run,
                source="sweep",
                policy=policy,
                cancel=cancel,
                observation=observation,
            )
        finally:
            runs.finish(run, path)
        # Counted only when a rewrite was attempted. A reporting sweep's failure
        # is a probe, which says nothing about whether a rewrite would work.
        failures = 0
        if result.status is Status.FAILED and not dry_run:
            failures = cache.failures(path, key) + 1
            if failures >= MAX_FAILURES:
                log.warning(
                    "rewrite of %s has now failed %d times; leaving it alone until it "
                    "changes on disk or the sweep cache is cleared",
                    path,
                    failures,
                )
        return Judged(
            job,
            key,
            verdict_of(result, failures),
            result.detail,
            result.became,
            # Nothing was written, so the file keeps the verdict it already has
            # rather than having it cleared.
            unobserved=_cancelled(result.status, cancel),
        )
    except Exception as err:
        log.exception("unhandled error judging %s", path)
        return Judged(Job(path), None, Verdict(Status.FAILED), detail=str(err))


@dataclass(frozen=True)
class Walk:
    """What differs between a sweep and a re-check. The loop is the same: list,
    judge on the probe pool, rewrite on the rewrite pool, book."""

    #: The run type; also the log name and the probe threads' prefix.
    type: str
    #: Lists the files. Called by the walk, not before it, since listing is
    #: slow and the run must already be on the record.
    find: Callable[[Policy], list[str]]
    #: Ignore stored verdicts and the failure ceiling. See :func:`_judge`.
    force: bool = False
    #: Where the report goes, or None to leave the last one alone.
    report: str | None = None
    #: Drop every entry the walk did not visit when it finishes. Only a walk
    #: that listed the whole of a folder can tell a departed file from an
    #: unvisited one.
    prunes: bool = False
    #: The folders the walk lists, when less than the library; pruning stays
    #: under them. Empty is the whole library.
    roots: tuple[str, ...] = ()


@dataclass
class _Totals:
    """What one walk accumulates, owned by the booking thread."""

    counts: dict[Status, int]
    #: How many files the listing found.
    walked: int = 0
    #: Verdicts answered from the cache. A file that was rewritten is never one,
    #: whatever answered its discovery.
    cached: int = 0
    library_bytes: int = 0
    #: Files the walk never reached because it was stopped.
    stopped: int = 0
    #: Report rows by path, written in walk order. A rewrite's verdict lands long
    #: after the walk passed the file, so rows cannot be appended as booked.
    rows: dict[str, str] = field(default_factory=dict)
    #: How long the walk took, listing excluded.
    seconds: float = 0.0


@dataclass(frozen=True)
class _Ready:
    """What a registered run hands its walk."""

    policy: Policy
    index: LibraryIndex
    dry_run: bool


@contextlib.contextmanager
def _registered(walk: Walk, run: str, dry_run: bool, label: str = "") -> Iterator[_Ready]:
    """Register the run, settle whether it may rewrite, and close it however
    it ends.

    Registered before anything slow, including listing the *arr libraries: a
    page that pressed "start now" is already polling for the run.
    """
    asked_for = dry_run
    dry_run = effective_dry_run(dry_run)
    if dry_run and not asked_for:
        log.info("REWRITE_MODE is report; the %s reports only", walk.type)
    # Taken before the run is opened and held past the last write, so a
    # shutdown waits for the cache and the report rather than for the queue.
    with lifecycle.producer() as allowed:
        if not allowed:
            raise ValueError("the process is stopping")
        record = lifecycle.open_run(run, walk.type, dry_run=dry_run, label=label)
        try:
            policy = Policy.from_config()
            index = path_index(all_arrs())
            if not index.complete and not dry_run and policy.needs_original_lang():
                # With original languages unknown, the languages rule would drop
                # a foreign film's own track. Only report-only is safe until the
                # *arr answers again.
                log.error(
                    "a *arr library could not be listed; this %s is report-only, "
                    "nothing rewritten",
                    walk.type,
                )
                # On the record too: the run card is already showing.
                dry_run = record.dry_run = True
            yield _Ready(policy, index, dry_run)
        finally:
            lifecycle.close_run(run)


def sweep(dry_run: bool, run: str | None = None) -> dict[Status, int]:
    """Walk the library, judging every file, and return the verdict counts.

    ``run`` is passed when the caller has already minted and answered with an
    id, as the UI's "sweep now" does.
    """
    # Every event this sweep writes carries one run id.
    run = run or events.run_id()
    walk = Walk(
        type=runs.SWEEP,
        find=walk_library,
        report=os.path.join(config.STATE_DIR, "pending.tsv"),
        prunes=True,
    )
    with _registered(walk, run, dry_run) as ready:
        totals = _walk_files(run, ready, walk)
        events.record(
            "sweep",
            run=run,
            dry_run=ready.dry_run,
            files=totals.walked - totals.stopped,
            # Only on a stopped sweep, so the history says why the counts do
            # not add up.
            stopped=totals.stopped or None,
            # Left out when stopped: a partial size reads as the whole.
            library_bytes=None if totals.stopped else totals.library_bytes,
            config=ready.policy.fingerprint(),
            config_id=ready.policy.digest(),
            cached=totals.cached,
            counts=totals.counts,
            seconds=round(totals.seconds, 1),
        )
        return totals.counts


def _observed(
    path: str,
    cache: SweepCache,
    call: Callable[..., Judged],
    stopped: Callable[[], bool] | None = None,
) -> Judged:
    """Publish while the scheduler still owns the path, outside its lock."""
    policy = Policy.from_config()
    try:
        with sweep_cache.observing(
            path, cache.path, policy=policy, stopped=stopped
        ) as observation:
            judged = call(policy=policy, observation=observation)
            if not judged.skipped:
                _stash(judged, cache, observation)
            return judged
    except sweep_cache.ObservationStoppedError:
        cache.carry(path)
        return Judged(
            Job(path),
            None,
            Verdict(Status.DEFERRED),
            detail="skipped while waiting for another edit",
            unobserved=True,
        )


def _stash(judged: Judged, cache: SweepCache, observation: sweep_cache.Observation) -> None:
    if judged.cached or judged.unobserved:
        cache.carry(judged.job.path)
        return
    became = judged.became
    sweep_cache.publish(
        observation,
        judged.job.path,
        became.path if became else judged.job.path,
        became.key if became else judged.key,
        became.verdict
        if became
        else (judged.verdict if judged.verdict.status in CACHEABLE_STATUSES else None),
        observation.policy.fingerprint(),
        cache,
    )


def _book(judged: Judged, totals: _Totals, cache: SweepCache, run: str) -> None:
    """Tally one verdict on the run and add its report row.

    Called only from the walking thread, for discovery's own verdicts and for
    rewrites' alike. Publication has already completed in the worker.
    """
    totals.counts[judged.verdict.status] += 1
    lifecycle.tally(
        run,
        str(judged.verdict.status),
        path=judged.job.path,
        # The failure where there was one, otherwise the plan's reasons.
        detail=judged.detail or judged.verdict.reasons,
        cached=judged.cached,
    )
    if judged.verdict.status in REPORTED_STATUSES:
        totals.rows[judged.job.path] = _report_row(judged)


def remember(
    path: str,
    key: FileKey | None,
    result: ProcessResult,
    observation: sweep_cache.Observation,
) -> None:
    """Publish an outside-walk result observed before stat/probe/mutation."""
    became = result.became
    sweep_cache.publish(
        observation,
        path,
        became.path if became else path,
        became.key if became else key,
        became.verdict
        if became
        else (verdict_of(result) if result.status in CACHEABLE_STATUSES else None),
        result.plan.policy.fingerprint() if result.plan else observation.policy.fingerprint(),
    )


def recheck(
    folders: list[str],
    dry_run: bool,
    run: str,
    label: str = "",
    *,
    files: list[str] | None = None,
) -> dict[Status, int]:
    """Re-judge every file under ``folders``, ignoring stored verdicts.

    The library page's "look at this one now". The folders come from
    :func:`trackstarr.library.selected`; ``label`` is the run card's name for
    them. pending.tsv is left alone, since it is the last full sweep's answer.
    Entries under its folders that the walk did not find are dropped: it has
    seen the whole of each, and nothing else. Concurrent walks share fresh
    cache entries and the work queue. Explicit ``files`` replace folder
    discovery, never include siblings, and so prune nothing.
    """
    walk = Walk(
        type=runs.RECHECK,
        find=(
            functools.partial(_walk, roots=folders, noun="title folder")
            if files is None
            else lambda policy: list(
                dict.fromkeys(path for path in files if policy.is_video(path))
            )
        ),
        force=True,
        prunes=files is None,
        roots=tuple(folders),
    )
    with _registered(walk, run, dry_run, label=label) as ready:
        totals = _walk_files(run, ready, walk)
        events.record(
            "recheck",
            run=run,
            dry_run=ready.dry_run,
            titles=len(folders) if files is None else None,
            files=totals.walked - totals.stopped,
            stopped=totals.stopped or None,
            config_id=ready.policy.digest(),
            counts=totals.counts,
            seconds=round(totals.seconds, 1),
        )
        return totals.counts


def _no_waiting() -> bool:
    """A ``stopped`` that gives up at once. A webhook answers now; a rewrite
    can hold a file for an hour."""
    return True


@contextlib.contextmanager
def _fenced(*paths: str) -> Iterator[sweep_cache.Observation | None]:
    """An observation of ``paths`` for an edit of the store, or None where a
    rewrite holds one of them. The rewrite's own publication settles what it
    holds."""
    try:
        with sweep_cache.observing(paths[0], stopped=_no_waiting) as observation:
            if all(observation.watch(path, _no_waiting) for path in paths[1:]):
                yield observation
            else:
                yield None
    except sweep_cache.ObservationStoppedError:
        yield None


def _show(changed: list[str]) -> None:
    """Write the store now, not when the coalescing window closes, then tell
    open pages, so what they refetch has the change."""
    if changed:
        sweep_cache.flush()
        notify.publish(notify.LIBRARY)


def remove(paths: Iterable[str], folders: Iterable[str] = ()) -> list[str]:
    """Drop the stored verdicts of files an *arr says it removed, and return
    the paths dropped.

    ``folders`` adds everything stored under a title deleted with its files.
    A path still on disk keeps its verdict: the *arr spoke of its own copy.
    """
    fingerprint = Policy.from_config().fingerprint()
    stored = sweep_cache.read(cache_path(), fingerprint).files
    roots = tuple(folder.rstrip(os.sep) + os.sep for folder in folders if folder.strip(os.sep))
    wanted = dict.fromkeys(paths)
    if roots:
        wanted.update(dict.fromkeys(path for path in stored if path.startswith(roots)))
    dropped: list[str] = []
    for path in wanted:
        if path not in stored or os.path.exists(path):
            continue
        with _fenced(path) as observation:
            if observation is not None and sweep_cache.publish(
                observation, path, path, None, None, fingerprint
            ):
                dropped.append(path)
    _show(dropped)
    return dropped


def relocate(moves: Iterable[tuple[str, str]]) -> list[str]:
    """Put each renamed file's stored verdict and rewrite record under its new
    path, and return the new paths that took one.

    A rename changes no track and none of the file key, so the verdict stands.
    Skipped while the old path is still there or the new one is not.
    """
    fingerprint = Policy.from_config().fingerprint()
    stored = sweep_cache.read(cache_path(), fingerprint).files
    moved: list[str] = []
    for previous, path in moves:
        if previous not in stored or os.path.exists(previous) or not os.path.exists(path):
            continue
        with _fenced(previous, path) as observation:
            if observation is not None and sweep_cache.move(
                observation, previous, path, fingerprint
            ):
                rewrites.move(previous, path)
                moved.append(path)
    _show(moved)
    return moved


def _report_row(judged: Judged) -> str:
    return (
        f"{judged.verdict.status}\t{judged.job.lang or '-'}\t"
        f"{judged.job.path.translate(_ROW_BREAKERS)}\t"
        f"{_cell(judged.verdict.reasons)}\t{_cell(judged.detail)}\n"
    )


def _write_report(walk: Walk, files: list[str], rows: dict[str, str]) -> None:
    """Write the report: one row per actionable file, in walk order.

    Rewritten whole at every checkpoint, since verdicts no longer arrive in
    walk order. A failed write is logged, not raised.
    """
    if not walk.report:
        return
    try:
        with open(walk.report, "w") as report_file:
            report_file.write("status\toriginal_lang\tpath\treasons\tdetail\n")
            report_file.writelines(rows[name] for name in files if name in rows)
    except OSError as err:
        log.warning("could not write %s: %s", walk.report, err)


def _rewrite(
    found: Judged,
    index: LibraryIndex,
    cache: SweepCache,
    run: str,
    force: bool,
    expected: float = 0.0,
    cancel: Cancel | None = None,
) -> Judged:
    """Rewrite one file discovery found work for, on a rewrite thread.

    Judged again from scratch: discovery's plan can be hours old by the time a
    slot frees, and a probe is cheap beside an encode. ``force`` must come from
    the walk, or a re-check would meet the failure ceiling here. A run stopped
    or a file skipped keeps discovery's pending verdict, whether the skip
    arrived before the slot or mid-encode.
    """
    if work.scheduler.skipped(run, found.job.path):
        return replace(found, detail="skipped for this run")
    if not lifecycle.hold(run):
        return found
    judged = _observed(
        found.job.path,
        cache,
        functools.partial(
            _judge,
            found.job.path,
            index,
            cache,
            False,
            run,
            force,
            expected=expected,
            cancel=cancel,
        ),
        stopped=cancel.stopped if cancel else None,
    )
    if _cancelled(judged.verdict.status, cancel):
        return replace(found, detail="skipped for this run")
    return judged


#: How long the booking thread waits on a finished rewrite once the walk is
#: over, so checkpoints keep moving through a rewrite phase of hours.
_DRAIN_TICK = 5.0


def _hurry(run: str, cache: SweepCache, waiting: dict[str, Judged], force: bool) -> None:
    """Pre-check cache hits without probing or holding the scheduler lock."""
    if force or cache.fingerprint != Policy.from_config().fingerprint():
        return
    cleared = set()
    for path in work.scheduler.waiting_work(run):
        found = waiting[path]
        key = cache_key(path, found.job.lang)
        verdict = cache.lookup(path, key)
        if verdict is not None and verdict.status not in RETRY_STATUSES | {Status.PENDING}:
            cleared.add((run, path))
    work.scheduler.hurry(cleared)


def _returning(finished: queue.Queue[Judged], found: Judged):
    """A done-callback that hands a finished rewrite to the booking thread.

    A queue rather than polling the futures, which would cost the square of
    the backlog. Every rewrite must come back with exactly one verdict or the
    drain waits for ever; the fallback covers what :func:`_judge` could not.
    """

    def landed(future: Future[Judged]) -> None:
        try:
            finished.put(future.result())
        except Exception as err:  # pragma: no cover - _judge does not raise
            log.exception("unhandled error rewriting %s", found.job.path)
            finished.put(Judged(found.job, None, Verdict(Status.FAILED), detail=str(err)))

    return landed


def _settle(
    finished: queue.Queue[Judged],
    outstanding: int,
    totals: _Totals,
    cache: SweepCache,
    run: str,
    waiting: dict[str, Judged],
    tick: float = 0.0,
) -> int:
    """Book every finished rewrite and return how many are still out.

    ``tick`` is how long to wait for the first: nothing during the walk,
    :data:`_DRAIN_TICK` after it.
    """
    while outstanding:
        try:
            judged = finished.get(timeout=tick) if tick else finished.get_nowait()
        except queue.Empty:
            break
        waiting.pop(judged.job.path, None)
        if judged.cached:
            totals.cached += 1
        _book(judged, totals, cache, run)
        outstanding -= 1
        # Only the first is waited for.
        tick = 0.0
    return outstanding


def _walk_files(run: str, ready: _Ready, walk: Walk) -> _Totals:
    """Register pruning's observation before the potentially slow listing.

    Imports accepted while listing must survive even if this enumeration did
    not see their paths. Registering only after listing would prune them.
    """
    runs.walking(run, True)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache.load(cache_path(), ready.policy.fingerprint())
    with sweep_cache.live(cache):
        return _walk_cached(run, ready, walk, cache)


def _walk_cached(run: str, ready: _Ready, walk: Walk, cache: SweepCache) -> _Totals:
    """Discover on probe workers, queue rewrites, and account on this thread."""
    policy, dry_run = ready.policy, ready.dry_run
    files = list(dict.fromkeys(walk.find(policy)))
    # Taken with the listing: a root that was not there listed nothing, and
    # pruning under it would read an unmounted folder as an emptied one.
    present = tuple(root for root in walk.roots if os.path.isdir(root))
    runs.set_total(run, len(files))
    log.info("%s starting: %d files, dry_run=%s", walk.type, len(files), dry_run)

    started = time.monotonic()
    last_checkpoint = started
    totals = _Totals(dict.fromkeys(Status, 0), walked=len(files))
    # Read once per walk: recent rewrite speeds, for the page's estimates.
    speeds = estimate.measured()

    def checkpoint() -> None:
        nonlocal last_checkpoint
        # Time-based: an applying walk can spend an hour between verdicts.
        if time.monotonic() - last_checkpoint >= _CHECKPOINT_SECONDS:
            cache.checkpoint()
            _write_report(walk, files, totals.rows)
            last_checkpoint = time.monotonic()

    # Discovery only reports, but says whether the walk rewrites so a file out
    # of attempts is answered from the cache rather than queued.
    judge = functools.partial(
        _judge,
        index=ready.index,
        cache=cache,
        dry_run=True,
        run=run,
        force=walk.force,
        rewriting=not dry_run,
    )
    publish = _publisher(cache)
    finished: queue.Queue[Judged] = queue.Queue()
    outstanding = 0
    waiting: dict[str, Judged] = {}
    work.scheduler.start()
    with lifecycle.group(run, priority=walk.force) as submit:
        probes = []
        for path in files:
            cancel = Cancel(path)
            probes.append(
                submit(
                    path,
                    "probe",
                    functools.partial(
                        _observed,
                        path,
                        cache,
                        functools.partial(judge, path),
                        stopped=cancel.stopped,
                    ),
                    cancel=cancel,
                )
            )
        handles: dict[Future[Judged], work.Handle] = {phase: phase.handle for phase in probes}
        # Book as results land, so a promoted file can reach the rewrite queue
        # without waiting behind an earlier, slower discovery result.
        for i, future in enumerate(as_completed(probes), 1):
            judged = future.result()
            if judged.skipped:
                # Stopped: the pool still returns every task, so the rest
                # arrive at once.
                totals.stopped += 1
                work.scheduler.complete_file(handles[future])
                continue
            if judged.key:
                totals.library_bytes += judged.key.size
            wanted = not dry_run and judged.verdict.status is Status.PENDING
            # A paused file is booked as discovery judged it rather than queued:
            # the rewrite would latch to report anyway, having spent a slot and
            # a second probe getting there.
            pause = pauses.paused(judged.job.path) if wanted else None
            if wanted and pause is None:
                # Queued under this estimate and worked under it: the rewrite
                # carries it to the page's readout itself.
                expected = speeds.seconds(judged.verdict.duration, judged.verdict.planned)
                # One switch for the phase, held by the queue and carried into
                # ffmpeg, so a skip reaches this rewrite alone.
                cancel = Cancel(judged.job.path)
                # Already published by discovery; counted once the rewrite answers.
                waiting[judged.job.path] = judged
                work.scheduler.continue_file(
                    handles[future],
                    functools.partial(
                        _rewrite, judged, ready.index, cache, run, walk.force, expected, cancel
                    ),
                    expected,
                    cancel,
                ).add_done_callback(_returning(finished, judged))
                outstanding += 1
            else:
                work.scheduler.complete_file(handles[future])
                if judged.cached:
                    totals.cached += 1
                # Said here as well as in process(), which a verdict answered
                # from the cache never reached, so the row and the report give
                # the reason either way.
                if pause is not None:
                    judged = replace(judged, detail=pause.describe())
                _book(judged, totals, cache, run)
            outstanding = _settle(finished, outstanding, totals, cache, run, waiting=waiting)
            if i % 500 == 0:
                log.info("  %d/%d ... %s", i, len(files), totals.counts)
            checkpoint()
            publish()
        runs.walking(run, False)
        if outstanding:
            log.info("walk done; %d file(s) still to rewrite", outstanding)
        last_hurry = started - _DRAIN_TICK
        while outstanding:
            if time.monotonic() - last_hurry >= _DRAIN_TICK:
                _hurry(run, cache, waiting, walk.force)
                last_hurry = time.monotonic()
            outstanding = _settle(
                finished, outstanding, totals, cache, run, waiting, _DRAIN_TICK
            )
            checkpoint()
            publish()
        # A stopped or partial walk keeps the whole cache: save() cannot tell
        # an unvisited file from one that left the library. See :class:`Walk`.
        # Still inside live(), so the view or the file always holds what this
        # walk reached.
        if not walk.prunes or totals.stopped:
            cache.keep()
        elif walk.roots:
            cache.save(within=present)
        else:
            cache.save()

    totals.seconds = time.monotonic() - started
    _write_report(walk, files, totals.rows)
    if totals.stopped:
        log.warning(
            "%s stopped after %d file(s), %d not looked at",
            walk.type,
            sum(totals.counts.values()),
            totals.stopped,
        )
    log.info("%s done: %s (%d verdicts from cache)", walk.type, totals.counts, totals.cached)
    if walk.report:
        log.info("report: %s", walk.report)
    return totals


def seconds_until(schedule: str, now: float | None = None) -> float:
    """Seconds until the schedule's next run, strictly after ``now``, so a
    reschedule cannot pick the slot that just fired."""
    now = time.time() if now is None else now
    # Naive local time on purpose: 03:00 means 03:00 in the container's TZ,
    # across DST.
    target = cron.next_run(cron.parse(schedule), datetime.fromtimestamp(now))  # noqa: DTZ006
    return target.timestamp() - now


def next_scheduled(schedule: str = "", now: float | None = None) -> str | None:
    """When the scheduled sweep next fires, stamped like a history ``ts``, or
    None with no schedule or an unparseable one."""
    schedule = (schedule or config.current().SWEEP_AT).strip()
    if not schedule:
        return None
    now = time.time() if now is None else now
    try:
        return runs.stamp(now + seconds_until(schedule, now))
    except ValueError:
        return None


def next_runs(schedule: str, count: int, start: datetime | None = None) -> list[datetime]:
    """The next ``count`` times a schedule fires after ``start`` (default: local
    now).

    Naive throughout, ``start`` included: a cron expression names a wall clock.
    Raises ValueError for an expression :func:`trackstarr.cron.parse` refuses
    or one matching no real date.
    """
    parsed = cron.parse(schedule)
    at = datetime.now() if start is None else start  # noqa: DTZ005
    runs = []
    for _ in range(count):
        at = cron.next_run(parsed, at)
        runs.append(at)
    return runs


#: How many upcoming runs :func:`check` returns: enough to show that "*/15"
#: means every quarter hour, not every 15 days.
PREVIEW_RUNS = 3


@dataclass(frozen=True)
class DirCheck:
    """One media dir as the settings page should show it."""

    path: str
    #: ok, missing (nothing mounted, which the sweep survives) or invalid (a
    #: path the setting cannot hold, which the save refuses).
    state: str
    detail: str


@dataclass(frozen=True)
class ScheduleCheck:
    """What the sweep page asks before saving, and what startup would log."""

    ok: bool
    #: Local-time stamps with no offset, so a browser in another zone cannot
    #: shift them into its own.
    runs: list[str]
    #: The zone's name, since the stamps cannot say.
    zone: str
    error: str
    dirs: list[DirCheck]


def _check_dir(path: str) -> DirCheck:
    separator = ":"
    if separator in path:
        return DirCheck(
            path,
            "invalid",
            "A colon separates the entries, so a path cannot contain one.",
        )
    if not os.path.isdir(path):
        # A warning, not a refusal, as at startup: a library may be mounted
        # later.
        return DirCheck(
            path, "missing", "Nothing is mounted there yet, so nothing would be swept."
        )
    return DirCheck(path, "ok", "")


def _wall_clock(zone: str) -> datetime:
    """Now in ``zone`` when this system knows it, otherwise in the service's
    own zone. An unknown zone is left to the save to refuse."""
    if zone:
        try:
            return datetime.now(zoneinfo.ZoneInfo(zone))
        except KeyError, ValueError, OSError:
            pass
    return datetime.now().astimezone()


def check(schedule: str, dirs: list[str], zone: str = "") -> ScheduleCheck:
    """Check a schedule, media dirs and zone before they are saved.

    Startup answers the same questions into a log nobody reads. Answered by
    the same :mod:`trackstarr.cron` the scheduler obeys.
    """
    checked = [_check_dir(path) for path in dirs]
    at = _wall_clock(zone)
    name = at.tzname() or ""
    if not schedule.strip():
        return ScheduleCheck(True, [], name, "", checked)
    try:
        runs = next_runs(schedule, PREVIEW_RUNS, at.replace(tzinfo=None))
    except ValueError as err:
        return ScheduleCheck(False, [], name, str(err), checked)
    return ScheduleCheck(True, [run.isoformat() for run in runs], name, "", checked)


def run_scheduled() -> None:
    """Run the sweep a slot is owed, or log why not. Split from the loop so
    both refusals are testable without a thread."""
    if lifecycle.paused():
        # Skipped, not queued: starting anyway would hold the library open all
        # night with the pool asleep on the gate.
        log.info("processing is paused, skipping this scheduled sweep")
    elif existing := runs.cache_holder():
        # A manual sweep, an overrunning scheduled one or a re-check. Avoid
        # starting another full-library walk while any of them is active.
        log.warning(
            "%s %s is still running, skipping this scheduled sweep", existing.type, existing.id
        )
    else:
        # Only REWRITE_MODE=all lets the scheduled sweep write.
        sweep(dry_run=config.current().REWRITE_MODE != "all")


#: The scheduler sleeps in slices this long so a SWEEP_AT saved at 03:59 can
#: fire at 04:00.
_TICK = 30.0


# No cover: a thread body. seconds_until and run_scheduled are covered.
def scheduler() -> None:  # pragma: no cover
    """Sweep on SWEEP_AT, re-reading it as it goes.

    Started whether or not a schedule is set, so one saved from the UI works
    without a restart. An empty or unparseable schedule waits here.
    """
    announced: str | None = None
    while lifecycle.producing():
        schedule = config.current().SWEEP_AT
        try:
            delay = seconds_until(schedule) if schedule else None
        except ValueError as err:
            # Only reachable from the environment; a saved schedule is validated.
            if schedule != announced:
                log.error("SWEEP_AT=%r: %s, so no sweep is scheduled", schedule, err)
            delay = None
        if delay is None:
            announced = schedule
            time.sleep(_TICK)
            continue
        if schedule != announced:
            mode = config.current().REWRITE_MODE
            log.info("next sweep in %.1f hours (mode=%s)", delay / 3600, mode)
            announced = schedule
        if delay > _TICK:
            time.sleep(_TICK)
            continue
        time.sleep(delay)
        if not lifecycle.producing():
            break
        try:
            run_scheduled()
        except Exception:
            log.exception("sweep failed")
        # Forgotten so the next run is announced.
        announced = None
