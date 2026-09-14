"""The import queue: the worker's decisions per job, hardlink parking and its
stored set, and the pool that follows the rewrite budget.

The listener's own tests are in test_server.py; nothing here binds a socket.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import (
    cache_verdict,
    claim,
    configured_arr,
    needed_plan,
    publish_verdict,
    read_events,
    registered,
    run_task,
    set_config,
    set_rules,
    step,
)
from trackstarr import (
    config,
    jobs,
    lifecycle,
    processing,
    runs,
    sweep_cache,
    verdict_store,
    work,
)
from trackstarr.executor import Cancel, Outcome
from trackstarr.jobs import _resolve_lang
from trackstarr.media import ProbeError
from trackstarr.planner import Plan
from trackstarr.policy import Policy
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.sweep_cache import SweepCache, Verdict, read


def test_worker_resolves_missing_language_from_the_arr():
    arr = configured_arr()
    arr.item = lambda item_id: {"originalLanguage": {"name": "Korean"}}
    job = _resolve_lang(Job("/x.mkv", None, 5, arr))
    assert job.lang == "kor"


def test_worker_keeps_a_language_the_webhook_already_carried():
    arr = configured_arr()
    arr.item = lambda item_id: pytest.fail("the API must not be queried")
    job = _resolve_lang(Job("/x.mkv", "eng", 5, arr))
    assert job.lang == "eng"


def test_report_mode_never_rewrites_a_webhook_import(monkeypatch):
    """The REWRITE_MODE latch lives inside process(), so the handler's plain
    dry_run=False still has to end as a pending verdict."""
    set_config(REWRITE_MODE="report")
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda p, lang, policy=None: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("report mode must not rewrite")
    )
    jobs.handle(Job("/x.mkv"))
    (entry,) = read_events()
    assert entry["event"] == "pending"


def test_the_worker_labels_history_with_the_delivery_run(monkeypatch):
    """In report mode the pending entry is the webhook's only record, so it
    has to carry the run the delivery minted."""
    set_config(REWRITE_MODE="report")
    monkeypatch.setattr(processing, "build_plan", lambda p, lang, policy=None: needed_plan())
    jobs.handle(Job("/x.mkv", run="r#1"))
    (entry,) = read_events()
    assert entry["run"] == "r#1"


@pytest.fixture
def imported(tmp_path) -> str:
    """A file where a delivery would have just put one."""
    path = tmp_path / "Show S01E01.mkv"
    path.write_bytes(b"x" * 10)
    return str(path)


def test_a_delivery_books_its_verdict_where_the_collection_reads_it(monkeypatch, imported):
    """A sweep books every file it walks; a delivery judges one, and used to
    book none. That left an import with a verdict in the history and none
    anywhere the library looks, until whenever the next sweep ran."""
    set_config(REWRITE_MODE="report")
    monkeypatch.setattr(
        processing, "build_plan", lambda p, lang, policy=None: needed_plan(imported)
    )

    jobs.handle(Job(imported, "eng"))

    entry = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files[imported]
    assert entry["status"] == "pending"
    assert entry["lang"] == "eng"


def _stub_rewrite_then(monkeypatch, first: Plan, second: Plan) -> None:
    """A rewrite that publishes, with the plan it works from and the plan the
    file it wrote comes back with. The second call is the re-judge."""
    plans = iter([first, second])

    def applied(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        assert claim is None or claim()
        return Outcome.APPLIED, ""

    monkeypatch.setattr(processing, "build_plan", lambda path, lang, policy=None: next(plans))
    monkeypatch.setattr(processing, "apply_plan", applied)


def test_a_delivery_books_the_file_its_rewrite_wrote(monkeypatch, imported):
    """The old entry described a file that no longer exists, so it goes. The
    new file must still get a verdict, or the library files a title it has just
    rewritten under "unchecked" until the next sweep."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    stale = Verdict(Status.PENDING, "add 2.0 downmix")
    cache_verdict(cache, imported, sweep_cache.cache_key(imported, "eng"), stale)
    cache.save()

    _stub_rewrite_then(monkeypatch, needed_plan(imported), Plan(path=imported))
    jobs.handle(Job(imported, "eng"))

    entry = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files[imported]
    assert entry["status"] == "conform"
    # The file as it now stands, not as the entry that was dropped described it.
    assert entry["size"] == os.path.getsize(imported)


def test_a_remux_books_the_mkv_and_forgets_the_file_it_replaced(monkeypatch, tmp_path):
    """A remux publishes under a new name and deletes the source, so one entry
    is written and one dropped."""
    source = str(tmp_path / "Show S01E02.mp4")
    remuxed = str(tmp_path / "Show S01E02.mkv")
    for path in (source, remuxed):
        with open(path, "wb") as made:
            made.write(b"x" * 10)
    # Before the cache is seeded: the rule is part of the fingerprint the
    # entries are filed under, and update() will not book into a cache judged
    # by rules that have since moved.
    set_rules(remux="always")
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    cache_verdict(
        cache,
        source,
        sweep_cache.cache_key(source, "eng"),
        Verdict(Status.PENDING, "remux to mkv"),
    )
    cache.save()

    commits = []
    write = verdict_store.write

    def commit(path, fingerprint, entries):
        commits.append(dict(entries))
        write(path, fingerprint, entries)

    monkeypatch.setattr(verdict_store, "write", commit)
    _stub_rewrite_then(monkeypatch, needed_plan(source, remuxing=True), Plan(path=remuxed))
    jobs.handle(Job(source, "eng"))
    sweep_cache.flush()

    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files
    assert stored[remuxed]["status"] == "conform"
    assert source not in stored
    assert commits == [stored]


def test_a_rewrite_nothing_can_judge_leaves_the_library_saying_nothing(
    monkeypatch, stub_rewrite, imported
):
    """The rewrite verified its output, so a probe failing afterwards is about
    the probe. Nothing is stored; the next sweep settles it."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    cache_verdict(
        cache,
        imported,
        sweep_cache.cache_key(imported, "eng"),
        Verdict(Status.PENDING, "add 2.0 downmix"),
    )
    cache.save()

    plans = iter([needed_plan(imported)])

    def planning(path, lang, policy=None):
        try:
            return next(plans)
        except StopIteration:
            raise ProbeError("ffprobe would not read it") from None

    monkeypatch.setattr(processing, "build_plan", planning)
    monkeypatch.setattr(
        processing,
        "apply_plan",
        lambda plan, on_progress=None, on_encoded=None, cancel=None, claim=None: (
            Outcome.APPLIED,
            "",
        ),
    )
    jobs.handle(Job(imported, "eng"))

    assert read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files == {}


def test_a_delivery_leaves_the_rest_of_the_library_alone(monkeypatch, imported, tmp_path):
    """It walked one file. Booking it must not prune a library it never saw,
    which is exactly what a sweep's own save() would do here."""
    neighbour = str(tmp_path / "Other.mkv")
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    with open(neighbour, "wb") as media:
        media.write(b"neighbour")
    cache_verdict(
        cache, neighbour, sweep_cache.cache_key(neighbour, "eng"), Verdict(Status.CONFORM)
    )
    cache.save()

    set_config(REWRITE_MODE="report")
    monkeypatch.setattr(
        processing, "build_plan", lambda p, lang, policy=None: needed_plan(imported)
    )
    jobs.handle(Job(imported, "eng"))

    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files
    assert stored[neighbour]["status"] == "conform"
    assert stored[imported]["status"] == "pending"


def test_a_stopped_delivery_drops_the_files_still_in_its_queue(monkeypatch):
    """Or "stop" would mean "stop after the twenty files already queued". No
    verdict, since nothing opened the file; it comes off the total instead, so
    the run can retire."""
    runs.reset()
    monkeypatch.setattr(
        processing,
        "build_plan",
        lambda p, lang, policy=None: pytest.fail("a stopped run must not process"),
    )
    lifecycle.open_run("r#1", runs.IMPORT, filling=True)
    runs.add_file("r#1")
    lifecycle.stop("r#1")

    jobs.handle(Job("/x.mkv", run="r#1"))
    assert read_events() == []
    assert runs.workload() == (0, 0)
    runs.reset()


def test_a_path_already_in_flight_is_not_queued_twice(tmp_path):
    """A sweep and a webhook can name the same file; the second must not
    queue a rewrite behind the first for a file that is already correct."""
    job = Job(str(tmp_path / "f.mkv"))
    assert jobs.enqueue(job) is True
    assert jobs.enqueue(job) is False
    assert jobs.queued_count() == 1


@pytest.fixture
def parked():
    """SKIP_HARDLINKS on, with a clean parked set before and after."""
    set_config(SKIP_HARDLINKS=True)
    jobs.forget()
    yield
    jobs.forget()


def parked_paths() -> list[str]:
    return [job.path for job in jobs.parked_jobs()]


@pytest.fixture
def seeded_file(tmp_path) -> str:
    """A library file the download client still hard-links."""
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    os.link(path, tmp_path / "seed.mkv")
    return str(path)


def test_seeded_import_is_parked_not_processed(parked, seeded_file, monkeypatch):
    processed = []
    monkeypatch.setattr(
        jobs,
        "process",
        lambda job, dry_run, policy=None, cancel=None, observation=None: processed.append(job),
    )
    jobs.handle(Job(seeded_file))
    assert parked_paths() == [seeded_file]
    assert processed == []


def test_parking_requires_the_option(parked, seeded_file, monkeypatch):
    set_config(SKIP_HARDLINKS=False)
    processed = []
    monkeypatch.setattr(
        jobs,
        "process",
        lambda job, dry_run, policy=None, cancel=None, observation=None: (
            processed.append(job) or ProcessResult(Status.CONFORM)
        ),
    )
    jobs.handle(Job(seeded_file))
    assert jobs.parked_count() == 0
    assert [job.path for job in processed] == [seeded_file]


def test_release_queues_the_parked_job(parked, seeded_file, tmp_path, monkeypatch):
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda job: queued.append(job) is None)
    jobs.park(Job(seeded_file))

    jobs.recheck_parked()
    assert parked_paths() == [seeded_file]
    assert queued == []

    os.unlink(tmp_path / "seed.mkv")
    jobs.recheck_parked()
    assert jobs.parked_count() == 0
    assert [job.path for job in queued] == [seeded_file]


def test_a_release_the_shutdown_refuses_leaves_the_file_parked(parked, monkeypatch, tmp_path):
    """The parked set is the only record of a delivery nobody will send again,
    and it is dropped before the handoff, so a refusal has to put it back."""
    released = tmp_path / "f.mkv"
    released.write_text("not really a video")
    jobs.park(Job(str(released)))

    def refuse(job):
        raise ValueError("scheduler is closed")

    monkeypatch.setattr(jobs, "enqueue", refuse)
    jobs.recheck_parked()

    assert parked_paths() == [str(released)]
    # And on disk too, which is what the next process reads.
    jobs.forget()
    jobs.load_parked()
    assert parked_paths() == [str(released)]


def test_nothing_is_unparked_once_the_process_is_stopping(parked, seeded_file, monkeypatch):
    """Handing a file to a queue that is draining loses it either way."""
    monkeypatch.setattr(jobs, "enqueue", lambda job: pytest.fail("admitted during shutdown"))
    jobs.park(Job(seeded_file))
    assert lifecycle.shutdown(0)

    jobs.recheck_parked()

    assert parked_paths() == [seeded_file]


def test_vanished_parked_file_is_dropped(parked, monkeypatch):
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda job: queued.append(job) is None)
    jobs.park(Job("/nowhere/f.mkv"))

    jobs.recheck_parked()
    assert jobs.parked_count() == 0
    assert queued == []


@pytest.mark.parametrize("off", [{"SKIP_HARDLINKS": False}, {"HARDLINK_RECHECK": 0}])
def test_parking_switched_off_releases_the_whole_set(parked, seeded_file, monkeypatch, off):
    """The set outlives the setting that filled it: with parking off, nothing
    else revisits a parked file, so all are released and the planner decides."""
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda job: queued.append(job) is None)
    jobs.park(Job(seeded_file))
    for name, value in off.items():
        set_config(**{name: value})

    jobs.recheck_parked()
    assert jobs.parked_count() == 0
    assert [job.path for job in queued] == [seeded_file]


def test_parking_survives_a_restart(parked, seeded_file):
    """Nothing re-fires an import and SWEEP_AT is unset by default, so a set
    lost to a restart is a file nothing comes back to."""
    jobs.park(Job(seeded_file, "kor", 12, configured_arr("sonarr"), "r#1"))

    jobs.forget()  # stand in for the process going away
    jobs.load_parked()

    (job,) = jobs.parked_jobs()
    assert job.path == seeded_file
    assert job.lang == "kor"
    assert job.item_id == 12
    # Stored by name and rebuilt from current config, so a job restored after
    # its *arr was reconfigured carries the new settings, not the old ones.
    assert job.arr.name == "sonarr"
    # The delivery's run rides along, so a rewrite finished days after its
    # import still groups with it.
    assert job.run == "r#1"


def test_a_parked_job_with_no_arr_round_trips(parked, seeded_file):
    """`fix` and a hand-rolled client both queue jobs matched to nothing."""
    jobs.park(Job(seeded_file))

    jobs.forget()
    jobs.load_parked()

    (job,) = jobs.parked_jobs()
    assert (job.lang, job.item_id, job.arr) == (None, None, None)


def test_releasing_the_last_parked_file_clears_the_stored_set(
    parked, seeded_file, tmp_path, monkeypatch
):
    """Otherwise the next restart restores a file that was long since done."""
    monkeypatch.setattr(jobs, "enqueue", lambda job: True)
    jobs.park(Job(seeded_file))
    os.unlink(tmp_path / "seed.mkv")

    jobs.recheck_parked()

    jobs.forget()
    jobs.load_parked()
    assert jobs.parked_count() == 0


def test_no_stored_set_restores_nothing(parked):
    jobs.load_parked()
    assert jobs.parked_count() == 0


@pytest.mark.parametrize(
    "content",
    ["not json", '{"path": "/x.mkv"}', '[["/x.mkv"], {}, {"lang": "eng"}]'],
    ids=["unparseable", "not a list", "entries with no path"],
)
def test_a_damaged_stored_set_restores_nothing(parked, content):
    """Advisory state: a mangled file costs the parked entries, not the
    listener that was about to start."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(os.path.join(config.STATE_DIR, jobs.PARKED_FILE), "w") as parked_file:
        parked_file.write(content)

    jobs.load_parked()
    assert jobs.parked_count() == 0


def test_an_unwritable_state_dir_does_not_fail_an_import(
    parked, seeded_file, tmp_path, monkeypatch, caplog
):
    """Persisting is a convenience; the in-memory set still works without it."""
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker / "under-a-file"))

    jobs.park(Job(seeded_file))

    assert parked_paths() == [seeded_file]
    assert "could not persist the parked set" in caplog.text


def test_a_released_file_already_in_flight_is_not_queued_twice(parked, seeded_file, tmp_path):
    """The recheck loop and a fresh webhook can free the same file at once."""
    jobs.park(Job(seeded_file))
    os.remove(tmp_path / "seed.mkv")
    assert jobs.enqueue(Job(seeded_file)) is True

    jobs.recheck_parked()
    assert jobs.queued_count() == 1, "the release queued a second copy"
    assert seeded_file not in parked_paths()


def test_a_skipped_import_is_booked_rather_than_rewritten(clean_registry, monkeypatch):
    """The delivery's row has to say what became of the file, or a run whose
    total never comes in stays on the page for ever."""
    monkeypatch.setattr(
        jobs,
        "process",
        lambda job, dry_run, policy=None, cancel=None, observation=None: pytest.fail(
            "must not rewrite"
        ),
    )
    # Still being handed files, so the delivery is here to read afterwards; a
    # sealed one with nothing left retires the moment this is booked.
    lifecycle.open_run("r#1", runs.IMPORT, label="radarr", filling=True)
    assert jobs.enqueue(Job("/data/f.mkv", run="r#1"))
    assert lifecycle.skip_file("r#1", "/data/f.mkv") == ("waiting", 0)

    step(work.scheduler)
    (run,) = clean_registry.snapshot()["runs"]
    assert run["counts"] == {"deferred": 1}
    assert "skipped" in run["recent"][0]["detail"]


def test_a_parked_file_does_not_strand_its_delivery_on_the_page(
    parked, seeded_file, clean_registry
):
    """Nothing books a parked file, so an import waiting on a download client
    would sit on the activity page for ever."""
    lifecycle.open_run("r#1", runs.IMPORT, label="radarr", filling=True)
    job = Job(seeded_file, run="r#1")
    jobs.enqueue(job)
    lifecycle.seal("r#1")
    assert clean_registry.snapshot()["runs"]

    task = claim(work.scheduler)
    run_task(work.scheduler, task)
    task.future.result(timeout=3)
    assert parked_paths() == [seeded_file]
    assert clean_registry.snapshot()["runs"] == [], "booked as deferred and let go"


def test_a_released_parked_file_is_booked_against_its_own_delivery(
    parked, seeded_file, tmp_path, clean_registry
):
    """Days can pass between the import and the download client letting go; it
    is still that import finishing, not a new one."""
    lifecycle.open_run("r#1", runs.IMPORT, label="radarr")
    jobs.park(Job(seeded_file, run="r#1", arr=configured_arr()))
    os.remove(tmp_path / "seed.mkv")

    jobs.recheck_parked()
    (run,) = clean_registry.snapshot()["runs"]
    assert (run["id"], run["total"], run["done"]) == ("r#1", 1, 0)


def test_a_late_webhook_verdict_cannot_overwrite_a_newer_observation(monkeypatch, imported):
    entered, release = threading.Event(), threading.Event()

    def process(job, dry_run, policy=None, cancel=None, observation=None):
        entered.set()
        assert release.wait(3)
        return ProcessResult(Status.PENDING)

    monkeypatch.setattr(jobs, "process", process)
    with ThreadPoolExecutor() as workers:
        old = workers.submit(jobs.handle, Job(imported, "eng"))
        try:
            assert entered.wait(3)
            publish_verdict(
                imported,
                sweep_cache.cache_key(imported, "eng"),
                Verdict(Status.CONFORM),
                Policy.from_config().fingerprint(),
            )
        finally:
            release.set()
        old.result(timeout=3)
    assert (
        read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files[imported][
            "status"
        ]
        == "conform"
    )
    assert not sweep_cache._revisions


def test_remux_captures_destination_before_mutating_it(monkeypatch, tmp_path):
    source = str(tmp_path / "film.mp4")
    output = str(tmp_path / "film.mkv")
    Path(source).write_bytes(b"source")
    Path(output).write_bytes(b"output")
    set_rules(remux="always")
    _stub_rewrite_then(monkeypatch, needed_plan(source, remuxing=True), Plan(path=output))
    entered, release = threading.Event(), threading.Event()

    def apply(plan, on_progress=None, on_encoded=None, cancel=None, claim=None):
        assert claim is not None
        entered.set()
        assert release.wait(3)
        return Outcome.APPLIED, ""

    monkeypatch.setattr(processing, "apply_plan", apply)
    with ThreadPoolExecutor() as workers:
        old = workers.submit(jobs.handle, Job(source, "eng"))
        try:
            assert entered.wait(3)
            publish_verdict(
                output,
                sweep_cache.cache_key(output, "eng"),
                Verdict(Status.PENDING, "newer"),
                Policy.from_config().fingerprint(),
            )
        finally:
            release.set()
        old.result(timeout=3)
    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files
    assert stored[output]["reasons"] == "newer"
    assert not sweep_cache._revisions


@pytest.mark.parametrize("rejection", ["duplicate", "closed"])
def test_rejected_admission_keeps_totals_and_releases_dedup(clean_registry, rejection):
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    job = Job("/file.mkv", run="delivery")
    if rejection == "duplicate":
        phase = work.scheduler.submit(job.run, job.path, "work", lambda: None)
        assert not phase.done()
    else:
        work.scheduler.shutdown()
    before = work.scheduler.snapshot()
    with pytest.raises(ValueError, match=r"already queued|scheduler is closed"):
        jobs.enqueue(job)
    assert (record.total, record.done) == (0, 0)
    assert work.scheduler.snapshot() == before
    # Replace only the scheduler: an admission rejection must have released
    # jobs' dedup claim itself, so the retry can succeed without jobs.forget().
    work.reset()
    lifecycle.open_run(job.run, runs.IMPORT, filling=True)
    assert jobs.enqueue(job)
    assert (record.total, record.done) == (1, 0)
    assert not jobs.enqueue(job)
    assert record.total == 1


@pytest.mark.parametrize("stage", ["language", "parking", "policy", "key", "process", "cache"])
def test_import_failure_books_once_and_releases_telemetry(
    clean_registry, monkeypatch, stage, caplog
):
    def fail(*args, **kwargs):
        raise RuntimeError(f"failure in {stage}")

    monkeypatch.setattr(jobs, "process", lambda *args, **kwargs: ProcessResult(Status.CONFORM))
    if stage == "language":
        monkeypatch.setattr(jobs, "_resolve_lang", fail)
    elif stage == "parking":
        monkeypatch.setattr(jobs, "parking_enabled", lambda: True)
        monkeypatch.setattr(jobs, "hardlinked", lambda path: True)
        monkeypatch.setattr(jobs, "park", fail)
    elif stage == "policy":
        monkeypatch.setattr(jobs.Policy, "from_config", fail)
    elif stage == "key":
        monkeypatch.setattr(jobs, "cache_key", fail)
    elif stage == "process":
        monkeypatch.setattr(jobs, "process", fail)
    else:
        monkeypatch.setattr(jobs.sweep, "remember", fail)

    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    job = Job("/file.mkv", run="delivery")
    assert jobs.enqueue(job)
    task = claim(work.scheduler)
    run_task(work.scheduler, task)
    assert task.future.result(timeout=3) is None
    assert task.future.settled.is_set()
    assert record.active == {}
    assert (record.total, record.done) == (1, 1)
    # A failed cache write does not replace the completed processing verdict.
    assert record.counts == {"conform" if stage == "cache" else "failed": 1}
    assert len(record.recent) == 1
    assert f"failure in {stage}" in caplog.text
    assert not work.scheduler.tasks
    lifecycle.seal("delivery")
    assert runs.snapshot()["runs"] == []
    lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    assert jobs.enqueue(job), "failure must release the import's dedup claim"


def test_publication_finishes_before_import_accounting_retires_run(clean_registry, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(jobs, "process", lambda *args, **kwargs: ProcessResult(Status.CONFORM))

    def publish(*args):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(jobs.sweep, "remember", publish)
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    jobs.enqueue(Job("/file.mkv", run="delivery"))
    lifecycle.seal("delivery")
    task = claim(work.scheduler)
    with ThreadPoolExecutor() as workers:
        executing = workers.submit(run_task, work.scheduler, task)
        try:
            assert entered.wait(3)
            assert record.done == 0
            assert registered("delivery")
        finally:
            release.set()
        executing.result(timeout=3)
    assert record.done == 1
    assert runs.snapshot()["runs"] == []


@pytest.mark.parametrize("cancelled", [False, True])
def test_duplicate_delivery_stays_guarded_until_scheduler_releases_task(
    clean_registry, monkeypatch, cancelled
):
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    job = Job("/file.mkv", run="delivery")
    monkeypatch.setattr(jobs, "_handle", lambda *a, **kw: None)
    assert jobs.enqueue(job)
    task = claim(work.scheduler)
    if cancelled:
        assert task.future.cancel()
        assert not jobs.enqueue(job)

    returned, release = threading.Event(), threading.Event()
    original = task.call

    def after_wrapper():
        original()
        returned.set()
        assert release.wait(3)

    task.call = after_wrapper
    with ThreadPoolExecutor() as workers:
        execution = workers.submit(run_task, work.scheduler, task)
        try:
            assert returned.wait(3)
            assert not jobs.enqueue(job)
            assert record.total == 1
        finally:
            release.set()
        execution.result(timeout=3)
    assert task.future.settled.is_set()
    assert not work.scheduler.tasks
    assert jobs.enqueue(job)
    assert record.total == 2
    assert not jobs.enqueue(job)


def test_a_skipped_delivery_waiting_for_an_edit_settles_without_reading(imported, monkeypatch):
    cancel = Cancel(imported)
    cancel.ask()
    monkeypatch.setattr(jobs, "process", lambda *a, **kw: pytest.fail("probed during edit"))
    accounting = lifecycle.ImportResult()
    with sweep_cache.observing(imported) as owner:
        assert owner.changing(imported)
        jobs._handle(Job(imported, "eng"), accounting, cancel)
    assert accounting.status is Status.DEFERRED
    assert "waiting for another edit" in accounting.detail


def test_shutdown_waits_for_the_unparked_set_to_be_persisted(parked, tmp_path, monkeypatch):
    path = tmp_path / "gone.mkv"
    jobs.park(Job(str(path)))
    entered, release = threading.Event(), threading.Event()
    original = jobs._save_parked

    def save():
        entered.set()
        assert release.wait(5)
        original()

    monkeypatch.setattr(jobs, "_save_parked", save)
    with ThreadPoolExecutor() as pool:
        releasing = pool.submit(jobs.recheck_parked)
        try:
            assert entered.wait(3)
            assert not lifecycle.shutdown(0)
        finally:
            release.set()
        releasing.result(timeout=3)
    assert lifecycle.shutdown(0)
    jobs.forget()
    jobs.load_parked()
    assert not parked_paths()


@pytest.mark.parametrize("status", [Status.CONFORM, Status.SKIP, Status.FAILED, Status.PENDING])
@pytest.mark.parametrize("curation", ["none", "reordered", "undone"])
def test_import_checks_bypass_sweeps_and_rewrites_respect_manual_order(
    imported, monkeypatch, status, curation
):
    set_config(MAX_CONCURRENT_REWRITES=1, PROBE_WORKERS=1)
    lifecycle.open_run("sweep", runs.SWEEP)
    scheduler = work.scheduler
    scheduler.submit("sweep", "/busy.mkv", "work", lambda: None)
    busy = claim(scheduler)
    scheduler.submit("sweep", "/waiting.mkv", "work", lambda: None)
    scheduler.submit("sweep", "/probe.mkv", "probe", lambda: None)
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    job = Job(imported, "eng", run="delivery")
    calls = []

    def process(job, dry_run, **kwargs):
        calls.append(dry_run)
        return ProcessResult(status if dry_run else Status.MODIFIED, needed_plan(imported))

    monkeypatch.setattr(jobs, "process", process)
    assert jobs.enqueue(job)
    original_rank = scheduler.tasks[("delivery", imported)].rank
    token = None
    if curation != "none":
        _, token = scheduler.move_top({("sweep", "/waiting.mkv")})
        if curation == "undone":
            assert scheduler.restore(token)
    undo = scheduler.undo
    lifecycle.seal("delivery")
    probe = claim(scheduler)
    assert probe.path == imported and probe.lane == "probe"
    run_task(scheduler, probe)
    assert calls == [True]
    assert scheduler.occupied["work"] == 1
    assert record.total == 1
    assert scheduler.undo is undo
    if status is Status.PENDING:
        assert record.done == 0
        assert record.counts == {}
        assert not jobs.enqueue(job), "dedup must span the phase handoff"
        rewrite = scheduler.tasks[("delivery", imported)]
        assert rewrite.lane == "work" and not rewrite.priority
        assert (rewrite.rank < 0) == (curation != "reordered")
        if curation == "reordered":
            assert rewrite.rank == original_rank
        assert not [event for event in read_events() if event["event"] == "pending"]
        # The old sweep probe also completes without spending a rewrite slot.
        old_probe = claim(scheduler)
        run_task(scheduler, old_probe)
        scheduler.complete_file(old_probe.future.handle)
        run_task(scheduler, busy)
        next_task = claim(scheduler)
        assert next_task.path == ("/waiting.mkv" if curation == "reordered" else imported)
        run_task(scheduler, next_task)
        if curation == "reordered":
            step(scheduler)
        assert calls == [True, False]
        assert record.counts == {"modified": 1}
    else:
        assert record.counts == {str(status): 1}
        run_task(scheduler, busy)
    assert record.done == 1
    assert len(record.recent) == 1
    assert not registered("delivery")


@pytest.mark.parametrize("control", ["stop", "skip", "report", "failure", "pause"])
def test_import_rewrite_handoff_obeys_controls_and_books_once(imported, monkeypatch, control):
    plan = needed_plan(imported)
    monkeypatch.setattr(processing, "build_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda *a, **kw: pytest.fail("must not encode")
    )
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    assert jobs.enqueue(Job(imported, "eng", run="delivery"))
    step(work.scheduler)
    assert record.done == 0
    assert work.scheduler.tasks[("delivery", imported)].lane == "work"
    if control == "stop":
        lifecycle.stop("delivery")
    elif control == "skip":
        work.scheduler.skip({("delivery", imported)})
    elif control == "report":
        set_config(REWRITE_MODE="report")
    elif control == "pause":
        work.scheduler.set_paused(True)
        assert claim(work.scheduler) is None
        assert record.done == 0
        work.scheduler.set_paused(False)
        work.scheduler.skip({("delivery", imported)})
    else:

        def fail(*a, **kw):
            raise RuntimeError("rewrite failed before probing")

        monkeypatch.setattr(jobs, "_resolve_lang", fail)
    step(work.scheduler)
    assert not work.scheduler.tasks
    if control == "stop":
        assert record.total == record.done == 0
    else:
        assert record.total == record.done == 1
        expected = {"report": "pending", "failure": "failed"}.get(control, "deferred")
        assert record.counts == {expected: 1}


def test_report_only_import_finishes_on_probe_lane(imported, monkeypatch):
    set_config(REWRITE_MODE="report")
    monkeypatch.setattr(processing, "build_plan", lambda *a, **kw: needed_plan(imported))
    record = lifecycle.open_run("delivery", runs.IMPORT, filling=True)
    jobs.enqueue(Job(imported, "eng", run="delivery"))
    step(work.scheduler)
    assert not work.scheduler.tasks
    assert record.counts == {"pending": 1}
    assert [event["event"] for event in read_events()] == ["pending"]
