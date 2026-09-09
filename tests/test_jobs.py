"""The import queue: the worker's decisions per job, hardlink parking and its
stored set, and the pool that follows the rewrite budget.

The listener's own tests are in test_webhook.py; nothing here binds a socket.
"""

import os

import pytest

from conftest import configured_arr, needed_plan, read_events, set_rules
from trackstarr import config, jobs, processing, runs, sweep_cache
from trackstarr.executor import Outcome
from trackstarr.jobs import _resolve_lang
from trackstarr.media import ProbeError
from trackstarr.planner import Plan
from trackstarr.policy import Policy
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict, read


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
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    plan = needed_plan()
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: plan)
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: pytest.fail("report mode must not rewrite")
    )
    jobs.handle(Job("/x.mkv"))
    (entry,) = read_events()
    assert entry["event"] == "pending"


def test_the_worker_labels_history_with_the_delivery_run(monkeypatch):
    """In report mode the pending entry is the webhook's only record, so it
    has to carry the run the delivery minted."""
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: needed_plan())
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
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: needed_plan(imported))

    jobs.handle(Job(imported, "eng"))

    entry = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files[imported]
    assert entry["status"] == "pending"
    assert entry["lang"] == "eng"


def _stub_rewrite_then(monkeypatch, first: Plan, second: Plan) -> None:
    """A rewrite that publishes, with the plan it works from and the plan the
    file it wrote comes back with. The second call is the re-judge."""
    plans = iter([first, second])
    monkeypatch.setattr(processing, "build_plan", lambda path, lang: next(plans))
    monkeypatch.setattr(
        processing,
        "apply_plan",
        lambda plan, on_progress=None, on_encoded=None: (Outcome.APPLIED, ""),
    )


def test_a_delivery_books_the_file_its_rewrite_wrote(monkeypatch, imported):
    """The old entry described a file that no longer exists, so it goes. The
    new file must still get a verdict, or the library files a title it has just
    rewritten under "unchecked" until the next sweep."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    stale = Verdict(Status.PENDING, "add 2.0 downmix")
    cache.record(imported, FileKey(10, 1, 1, "eng"), stale)
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
    set_rules(monkeypatch, remux="always")
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    cache.record(source, FileKey(10, 1, 1, "eng"), Verdict(Status.PENDING, "remux to mkv"))
    cache.save()

    _stub_rewrite_then(monkeypatch, needed_plan(source, remuxing=True), Plan(path=remuxed))
    jobs.handle(Job(source, "eng"))

    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files
    assert stored[remuxed]["status"] == "conform"
    assert source not in stored


def test_a_rewrite_nothing_can_judge_leaves_the_library_saying_nothing(
    monkeypatch, stub_rewrite, imported
):
    """The rewrite verified its output, so a probe failing afterwards is about
    the probe. Nothing is stored; the next sweep settles it."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    cache.record(imported, FileKey(10, 1, 1, "eng"), Verdict(Status.PENDING, "add 2.0 downmix"))
    cache.save()

    plans = iter([needed_plan(imported)])

    def planning(path, lang):
        try:
            return next(plans)
        except StopIteration:
            raise ProbeError("ffprobe would not read it") from None

    monkeypatch.setattr(processing, "build_plan", planning)
    monkeypatch.setattr(
        processing,
        "apply_plan",
        lambda plan, on_progress=None, on_encoded=None: (Outcome.APPLIED, ""),
    )
    jobs.handle(Job(imported, "eng"))

    assert read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files == {}


def test_a_delivery_leaves_the_rest_of_the_library_alone(monkeypatch, imported, tmp_path):
    """It walked one file. Booking it must not prune a library it never saw,
    which is exactly what a sweep's own save() would do here."""
    neighbour = str(tmp_path / "Other.mkv")
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    cache.record(neighbour, FileKey(10, 1, 1, "eng"), Verdict(Status.CONFORM))
    cache.save()

    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: needed_plan(imported))
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
        processing, "build_plan", lambda p, lang: pytest.fail("a stopped run must not process")
    )
    runs.open_run("r#1", runs.IMPORT, filling=True)
    runs.add_file("r#1")
    runs.stop("r#1")

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
def parked(monkeypatch):
    """SKIP_HARDLINKS on, with a clean parked set before and after."""
    monkeypatch.setattr(config, "SKIP_HARDLINKS", True)
    jobs.reset()
    yield
    jobs.reset()


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
    monkeypatch.setattr(jobs, "process", lambda job, dry_run: processed.append(job))
    jobs.handle(Job(seeded_file))
    assert parked_paths() == [seeded_file]
    assert processed == []


def test_parking_requires_the_option(parked, seeded_file, monkeypatch):
    monkeypatch.setattr(config, "SKIP_HARDLINKS", False)
    processed = []
    monkeypatch.setattr(
        jobs,
        "process",
        lambda job, dry_run: processed.append(job) or ProcessResult(Status.CONFORM),
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
        monkeypatch.setattr(config, name, value)

    jobs.recheck_parked()
    assert jobs.parked_count() == 0
    assert [job.path for job in queued] == [seeded_file]


@pytest.fixture
def worker_pool(monkeypatch):
    """The pool's counters, with the thread body stubbed out: a real worker
    waits on the queue for ever, and these tests start several."""
    monkeypatch.setattr(jobs, "worker", lambda: None)
    monkeypatch.setattr(jobs, "_workers", 0)
    monkeypatch.setattr(jobs, "_worker_names", 0)


def test_the_worker_pool_grows_with_the_rewrite_budget(worker_pool, monkeypatch):
    """The point of topping it up after a save: serve() sized the pool once,
    so a budget raised in the UI bought nothing until a restart."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 2)
    jobs.start_workers()
    assert jobs.worker_count() == 2
    # Idempotent, since every save calls it and only one ever changes this.
    jobs.start_workers()
    assert jobs.worker_count() == 2

    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 4)
    jobs.start_workers()
    assert jobs.worker_count() == 4


def test_a_lowered_budget_retires_exactly_the_workers_over_it(worker_pool, monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    jobs.start_workers()
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    # Each worker asks for itself as it comes off the queue wait, so the
    # count has to be what decides, not how many happen to ask.
    assert [jobs.retire() for _ in range(3)] == [True, True, False]
    assert jobs.worker_count() == 1


def test_parking_survives_a_restart(parked, seeded_file):
    """Nothing re-fires an import and SWEEP_AT is unset by default, so a set
    lost to a restart is a file nothing comes back to."""
    jobs.park(Job(seeded_file, "kor", 12, configured_arr("sonarr"), "r#1"))

    jobs.reset()  # stand in for the process going away
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

    jobs.reset()
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

    jobs.reset()
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
    monkeypatch.setattr(jobs, "process", lambda job, dry_run: pytest.fail("must not rewrite"))
    # Still being handed files, so the delivery is here to read afterwards; a
    # sealed one with nothing left retires the moment this is booked.
    clean_registry.open_run("r#1", runs.IMPORT, label="radarr", filling=True)
    clean_registry.add_file("r#1")
    clean_registry.skip("r#1", "/data/f.mkv")

    jobs.handle(Job("/data/f.mkv", run="r#1"))
    (run,) = clean_registry.snapshot()["runs"]
    assert run["counts"] == {"deferred": 1}
    assert "skipped" in run["recent"][0]["detail"]


def test_a_parked_file_does_not_strand_its_delivery_on_the_page(
    parked, seeded_file, clean_registry
):
    """Nothing books a parked file, so an import waiting on a download client
    would sit on the activity page for ever."""
    runs.open_run("r#1", runs.IMPORT, label="radarr", filling=True)
    job = Job(seeded_file, run="r#1")
    jobs.enqueue(job)
    runs.seal("r#1")
    assert clean_registry.snapshot()["runs"]

    jobs.handle(job)
    assert parked_paths() == [seeded_file]
    assert clean_registry.snapshot()["runs"] == [], "booked as deferred and let go"


def test_a_released_parked_file_is_booked_against_its_own_delivery(
    parked, seeded_file, tmp_path, clean_registry
):
    """Days can pass between the import and the download client letting go; it
    is still that import finishing, not a new one."""
    runs.open_run("r#1", runs.IMPORT, label="radarr")
    jobs.park(Job(seeded_file, run="r#1", arr=configured_arr()))
    os.remove(tmp_path / "seed.mkv")

    jobs.recheck_parked()
    (run,) = clean_registry.snapshot()["runs"]
    assert (run["id"], run["total"], run["done"]) == ("r#1", 1, 0)
