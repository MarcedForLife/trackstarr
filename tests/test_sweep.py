"""Sweep scheduling and the REWRITE_MODE latch. The walk and the cache live in
the integration and sweep-cache suites."""

import contextlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from conftest import read_events
from trackstarr import config, estimate, holds, library, notify, runs, sweep_cache
from trackstarr import sweep as sweep_mod
from trackstarr.arr import LibraryIndex
from trackstarr.policy import Policy
from trackstarr.processing import Job, ProcessResult
from trackstarr.status import Status
from trackstarr.sweep import Judged, seconds_until, sweep
from trackstarr.sweep_cache import FileKey, Verdict


def _library(tmp_path, monkeypatch, count: int) -> list[str]:
    root = tmp_path / "library"
    root.mkdir()
    names = [f"{i:03d}.mkv" for i in range(count)]
    for name in names:
        (root / name).write_text("not really a video")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    return names


def test_concurrent_sweep_reports_in_walk_order(monkeypatch, tmp_path):
    """Verdicts are booked in walk order however many workers produced them, so
    raising concurrency doesn't reshuffle pending.tsv."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 4)
    _library(tmp_path, monkeypatch, 40)
    # Fixed order of our own, since os.walk's is the filesystem's business.
    walked = sorted(str(path) for path in (tmp_path / "library").iterdir())
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: walked)

    # Uneven, order-scrambling delays: under as_completed the odd-numbered
    # files would all land after the even ones.
    def slow_judge(path, **kwargs):
        time.sleep(0.02 if int(os.path.basename(path)[:3]) % 2 else 0.001)
        return Judged(Job(path), None, Verdict(Status.PENDING, "reorder streams"))

    monkeypatch.setattr("trackstarr.sweep._judge", slow_judge)
    sweep(dry_run=True)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert [row.split("\t")[2] for row in rows] == walked


def test_a_path_cannot_break_its_own_report_row(monkeypatch, tmp_path):
    """Tabs and newlines are legal in filenames and would shift every column
    after the path, so the row would parse as a different verdict about a
    different file. This is the one column that cannot be truncated instead."""
    awkward = str(tmp_path / "lib" / "two\tcolumns\nand a row.mkv")
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: [awkward])
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(
            Job(path), None, Verdict(Status.PENDING, "reorder streams")
        ),
    )
    sweep(dry_run=True)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert len(rows) == 1
    assert rows[0].split("\t")[2] == awkward.replace("\t", " ").replace("\n", " ")


def test_a_worker_raising_does_not_abandon_the_sweep(monkeypatch, tmp_path):
    """One unforeseen error must not cost every file queued behind it."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 3)
    _library(tmp_path, monkeypatch, 10)

    def explode(job, dry_run, source="webhook"):
        if job.path.endswith("004.mkv"):
            raise RuntimeError("something nobody predicted")
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", explode)

    counts = sweep(dry_run=True)
    assert counts[Status.FAILED] == 1
    assert counts[Status.CONFORM] == 9


@pytest.mark.parametrize("budget", [1, 6])
def test_probing_is_not_sized_by_the_rewrite_budget(monkeypatch, tmp_path, budget):
    """The rewrite budget and the probe pool are separate settings: a disk that
    wants one rewrite at a time still takes several probes."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", budget)
    monkeypatch.setattr(config, "PROBE_WORKERS", 3)
    _library(tmp_path, monkeypatch, 1)
    sized: dict[str, int] = {}

    def spying_pool(max_workers, **kwargs):
        sized[kwargs["thread_name_prefix"]] = max_workers
        return ThreadPoolExecutor(max_workers=max_workers, **kwargs)

    monkeypatch.setattr("trackstarr.sweep.ThreadPoolExecutor", spying_pool)
    sweep(dry_run=True)
    assert sized == {"sweep": 3, "rewrite": budget}


def test_an_arr_outage_downgrades_an_applying_sweep(monkeypatch, tmp_path, caplog):
    """With original languages unknown, the languages rule would read a foreign
    film's own track as junk to drop. Report-only until the *arr answers."""
    _library(tmp_path, monkeypatch, 1)
    monkeypatch.setattr("trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, False))
    judged_dry = []

    def spy(job, dry_run, source="sweep"):
        judged_dry.append(dry_run)
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", spy)

    sweep(dry_run=False)
    assert judged_dry == [True]
    assert "report-only" in caplog.text
    (entry,) = read_events()
    assert entry["dry_run"] is True


def test_an_arr_outage_stops_nothing_a_policy_never_asked(monkeypatch, tmp_path, caplog):
    """No row names the original language, so the outage takes no verdict with
    it and an applying sweep goes on applying."""
    monkeypatch.setattr(config, "LANGUAGES", ("eng",))
    _library(tmp_path, monkeypatch, 1)
    monkeypatch.setattr("trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, False))
    judged_dry = []

    def spy(job, dry_run, source="sweep"):
        judged_dry.append(dry_run)
        return ProcessResult(Status.PENDING if dry_run else Status.MODIFIED)

    monkeypatch.setattr("trackstarr.sweep.process", spy)

    sweep(dry_run=False)
    # The walk reports and the rewrite that follows it applies, which is the
    # whole of an applying sweep still applying.
    assert judged_dry == [True, False]
    assert "report-only" not in caplog.text


def test_report_mode_overrides_an_applying_sweep(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])

    sweep(dry_run=False)

    (entry,) = read_events()
    assert entry["event"] == "sweep"
    assert entry["dry_run"] is True


def test_checkpoints_come_from_time_not_file_count(monkeypatch, tmp_path):
    """An applying sweep can spend minutes on one file, so what it learned must not
    wait on a count a small library never reaches."""
    _library(tmp_path, monkeypatch, 3)
    monkeypatch.setattr("trackstarr.sweep._CHECKPOINT_SECONDS", 0.0)
    checkpoints = []
    monkeypatch.setattr(
        "trackstarr.sweep_cache.SweepCache.checkpoint", lambda self: checkpoints.append(1)
    )

    sweep(dry_run=True)
    assert len(checkpoints) == 3


@pytest.mark.parametrize(
    ("hour", "second", "expected"),
    [
        (1, 0, 3 * 3600),
        (5, 0, 23 * 3600),
        # Rescheduling seconds after the sweep fired must land on the next
        # run, not the one still on the clock's current minute.
        (4, 5, 24 * 3600 - 5),
    ],
    ids=["later today", "rolls to tomorrow", "never the slot that just fired"],
)
def test_seconds_until_the_next_4am_sweep(hour, second, expected):
    now = time.mktime((2026, 8, 13, hour, 0, second, 0, 0, -1))
    assert seconds_until("0 4 * * *", now) == pytest.approx(expected)


def test_seconds_until_rejects_garbage():
    with pytest.raises(ValueError):
        seconds_until("not-a-schedule")


def test_the_next_scheduled_sweep_is_stamped_like_the_history():
    """The overview's idle line: a stamp with its offset, so the browser can
    say "at 4:00 am" in whatever clock it keeps."""
    now = time.mktime((2026, 8, 13, 1, 0, 0, 0, 0, -1))
    stamped = sweep_mod.next_scheduled("0 4 * * *", now)
    assert stamped.startswith("2026-08-13T04:00:00")
    assert stamped[-6] in "+-"


def test_no_schedule_and_a_broken_one_both_promise_no_sweep():
    assert sweep_mod.next_scheduled("") is None
    assert sweep_mod.next_scheduled("not-a-schedule") is None


def test_the_check_reads_a_schedule_in_the_zone_being_picked():
    """The page offers the zone it is holding, unsaved, so the times it shows
    are the ones the schedule would actually fire at once both are saved."""
    answer = sweep_mod.check("0 4 * * *", [], "America/New_York")
    assert answer.zone in ("EST", "EDT")
    assert all(run.endswith("T04:00:00") for run in answer.runs)


def test_the_check_falls_back_to_our_own_clock():
    """A half-typed zone must not cost the preview; the save is what refuses
    a name that means nothing."""
    ours = datetime.now().astimezone().tzname()
    assert sweep_mod.check("0 4 * * *", [], "Middle/Earth").zone == ours
    assert sweep_mod.check("0 4 * * *", []).zone == ours


def test_a_missing_media_dir_is_reported_not_walked_silently(tmp_path, monkeypatch, caplog):
    """os.walk yields nothing for a path that isn't there, so a wrong mount would
    look like an empty library."""
    real = tmp_path / "media"
    real.mkdir()
    (real / "f.mkv").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "gone"), str(real)])

    found = sweep_mod.walk_library(Policy.from_config())
    assert found == [str(real / "f.mkv")]
    assert "media dir" in caplog.text
    assert "does not exist" in caplog.text


def test_the_walk_clears_staged_files_scattered_through_the_library(tmp_path, monkeypatch):
    """Cross-filesystem publishing lands its copy beside the file it replaces, so
    a crash leaves these anywhere. This walk is the only thing that visits
    them, and the age gate is what makes dropping them safe."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT", 0)
    (tmp_path / "f.mkv").write_bytes(b"x")
    orphan = tmp_path / ".trackstarr-eeee.partial"
    orphan.write_text("orphaned by a crash")

    found = sweep_mod.walk_library(Policy.from_config())
    assert found == [str(tmp_path / "f.mkv")]
    assert not orphan.exists()


def test_the_walk_collects_containers_the_rules_will_never_rewrite(tmp_path, monkeypatch):
    """An AVI-only title was a poster with no files under it and nothing to say
    about why. The walk names them so the library can; artwork and sidecars are
    still none of its business."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    for name in ("f.mkv", "old.avi", "poster.jpg", "f.nfo", "f.eng.srt"):
        (tmp_path / name).write_bytes(b"x")

    assert sorted(sweep_mod.walk_library(Policy.from_config())) == [
        str(tmp_path / "f.mkv"),
        str(tmp_path / "old.avi"),
    ]


def test_an_unsupported_container_is_judged_without_being_opened(tmp_path, monkeypatch):
    """Its own verdict, carrying the container that earned it, and no probe: the
    plan stops at the extension."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    (tmp_path / "old.avi").write_bytes(b"not really an avi")
    monkeypatch.setattr(
        "trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, complete=True)
    )

    counts = sweep(dry_run=True)
    assert counts[Status.UNSUPPORTED] == 1

    stored = sweep_cache.read(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    entry = stored.files[str(tmp_path / "old.avi")]
    assert entry["status"] == "unsupported"
    assert ".avi" in entry["why"]["skip"]
    assert "ALLOWED_EXTS" in entry["why"]["skip"]
    # Never opened, so there is nothing to say about its streams.
    assert "tracks" not in entry


def test_an_unsupported_container_is_not_reported_as_work(tmp_path, monkeypatch):
    """pending.tsv is what a sweep would do next. Nothing here is ever going to
    happen to this file, so a row for it is a queue nobody can clear."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    (tmp_path / "old.avi").write_bytes(b"not really an avi")
    monkeypatch.setattr(
        "trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, complete=True)
    )

    sweep(dry_run=True)
    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()
    assert rows == ["status\toriginal_lang\tpath\treasons\tdetail"]


def test_hidden_directories_are_not_walked(tmp_path, monkeypatch):
    """@eaDir, .recycle and friends hold copies that must never be rewritten."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    (tmp_path / "f.mkv").write_bytes(b"x")
    hidden = tmp_path / ".recycle"
    hidden.mkdir()
    (hidden / "deleted.mkv").write_bytes(b"x")

    assert sweep_mod.walk_library(Policy.from_config()) == [str(tmp_path / "f.mkv")]


def test_a_long_sweep_logs_progress_as_it_goes(monkeypatch, caplog):
    """A library sweep runs for hours. Without a periodic line the log looks hung
    and there is nothing to judge the rate from."""
    caplog.set_level(logging.INFO, logger="trackstarr.sweep")
    walked = [f"/data/{i:04d}.mkv" for i in range(500)]
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: walked)
    monkeypatch.setattr("trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, True))
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(Job(path), None, Verdict(Status.CONFORM)),
    )

    sweep(dry_run=True)
    assert "500/500" in caplog.text


def test_the_page_check_reads_a_schedule_back_in_local_time(monkeypatch):
    """The one question a cron field raises. Answered by the parser the
    scheduler obeys rather than by a second one in the browser, which would
    also be reading the wrong clock."""
    answer = sweep_mod.check("0 4 * * *", [])
    assert answer.ok and answer.error == ""
    assert len(answer.runs) == sweep_mod.PREVIEW_RUNS
    assert all(run.endswith("T04:00:00") for run in answer.runs)
    # Consecutive days, and naive: an offset here would let a browser shift
    # 04:00 in the container into 04:00 somewhere else.
    assert answer.runs == sorted(answer.runs)


def test_the_page_check_names_the_field_a_bad_schedule_broke():
    answer = sweep_mod.check("0 99 * * *", [])
    assert not answer.ok
    assert "hour" in answer.error
    assert answer.runs == []


def test_no_schedule_is_a_choice_rather_than_an_error():
    """Empty is how the sweep is switched off, so it must not read as a typo."""
    answer = sweep_mod.check("", [])
    assert answer.ok and answer.runs == [] and answer.error == ""


def test_the_page_check_says_which_media_dirs_are_there(tmp_path):
    """Both answers startup logs once and nobody reads twice: a path that is
    only missing is legitimate, a path holding the separator cannot be saved."""
    (tmp_path / "movies").mkdir()
    answer = sweep_mod.check(
        "", [str(tmp_path / "movies"), str(tmp_path / "gone"), "/data/films:2024"]
    )
    assert [entry.state for entry in answer.dirs] == ["ok", "missing", "invalid"]
    assert "mounted" in answer.dirs[1].detail
    assert "colon" in answer.dirs[2].detail


@pytest.fixture
def clean_registry():
    """The activity registry is module state, and a sweep registers itself in
    it. A stopped run left behind would stop the next test's sweep too."""
    runs._runs.clear()
    runs._running.set()
    yield runs
    runs._runs.clear()
    runs._running.set()


def test_a_sweep_shows_its_progress_while_it_walks(monkeypatch, tmp_path, clean_registry):
    """The whole point of the activity page: a three-hour walk has to be
    watchable, not a wait for the summary event."""
    _library(tmp_path, monkeypatch, 3)
    seen: list[dict] = []

    def judge(path, **kwargs):
        seen.append(clean_registry.snapshot()["runs"][0])
        return Judged(Job(path), None, Verdict(Status.CONFORM))

    monkeypatch.setattr("trackstarr.sweep._judge", judge)
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)
    sweep(dry_run=True, run="r#1")

    # The total is known before the first file is judged, so the bar has a
    # length from the start rather than filling in retrospectively.
    assert [snap["total"] for snap in seen] == [3, 3, 3]
    # Booked as the results are consumed, which the pool runs ahead of, so
    # the count climbs without promising to be one behind the walk.
    assert seen[0]["done"] == 0
    assert seen[-1]["done"] > 0
    assert [snap["done"] for snap in seen] == sorted(snap["done"] for snap in seen)
    assert seen[0]["kind"] == "sweep" and seen[0]["dry_run"] is True
    # And it lets go of itself afterwards, however the walk ended.
    assert clean_registry.snapshot()["runs"] == []


def test_a_sweep_puts_each_file_it_worked_on_up_with_its_verdict(
    monkeypatch, tmp_path, clean_registry
):
    """A row appears when a worker picks the file up and gains its verdict when
    booked. A cached verdict gets no row: nothing was probed."""
    _library(tmp_path, monkeypatch, 2)
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)

    def judge(path, **kwargs):
        # One is worked on, the other answered from the cache.
        cached = path.endswith("001.mkv")
        if not cached:
            clean_registry.begin("r#1", path)
            clean_registry.finish("r#1", path)
        return Judged(
            Job(path), None, Verdict(Status.PENDING, "add 2.0 downmix"), cached=cached
        )

    monkeypatch.setattr("trackstarr.sweep._judge", judge)
    rows: list[list[dict]] = []
    monkeypatch.setattr(
        "trackstarr.sweep.events.record",
        lambda *args, **kwargs: rows.append(clean_registry.snapshot()["runs"][0]["recent"]),
    )
    sweep(dry_run=True, run="r#1")

    (recent,) = rows
    assert [
        (os.path.basename(row["path"]), row["status"], row["detail"]) for row in recent
    ] == [("000.mkv", "pending", "add 2.0 downmix")]


def test_a_stopped_sweep_leaves_the_rest_of_the_library_unjudged(
    monkeypatch, tmp_path, clean_registry
):
    _library(tmp_path, monkeypatch, 20)
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)
    judged: list[str] = []

    def judge(job, dry_run, source="webhook"):
        judged.append(job.path)
        if len(judged) == 3:
            clean_registry.stop("r#1")
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", judge)
    counts = sweep(dry_run=True, run="r#1")

    assert len(judged) == 3, "the walk stopped where it was told to"
    assert counts[Status.CONFORM] == 3
    entry = read_events()[-1]
    # The history has to say the counts are a part of a library, not a whole
    # one, or a reader would take 3 conforming files as the answer.
    assert entry["files"] == 3
    assert entry["stopped"] == 17
    assert "library_bytes" not in entry


def test_a_stopped_sweep_keeps_the_verdicts_it_never_revisited(
    monkeypatch, tmp_path, clean_registry
):
    """save() prunes files the walk never reached, which is right for a
    completed sweep and would cost a stopped one a cold re-probe of most of
    the library."""
    names = _library(tmp_path, monkeypatch, 6)
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="webhook": ProcessResult(Status.CONFORM),
    )
    sweep(dry_run=True, run="r#1")
    cached = json.loads((Path(config.STATE_DIR) / "sweep-cache.json").read_text())
    assert len(cached["files"]) == len(names)

    calls = []

    def judge(job, dry_run, source="webhook"):
        calls.append(job.path)
        clean_registry.stop("r#2")
        return ProcessResult(Status.CONFORM)

    # A changed mtime forces the first file back through the probe, so the
    # sweep has something fresh to record before it is stopped.
    (tmp_path / "library" / names[0]).write_text("changed")
    monkeypatch.setattr("trackstarr.sweep.process", judge)
    sweep(dry_run=True, run="r#2")

    kept = json.loads((Path(config.STATE_DIR) / "sweep-cache.json").read_text())
    assert len(kept["files"]) == len(names), "the untouched verdicts survived"


def test_a_paused_service_holds_the_walk_where_it_stands(monkeypatch, tmp_path, clean_registry):
    _library(tmp_path, monkeypatch, 5)
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)
    judged: list[str] = []

    def judge(job, dry_run, source="webhook"):
        judged.append(job.path)
        if len(judged) == 2:
            clean_registry.pause("marc")
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", judge)
    done = threading.Event()
    threading.Thread(
        target=lambda: (sweep(dry_run=True, run="r#1"), done.set()), daemon=True
    ).start()

    # Held, not finished: the pool's thread is asleep on the gate mid-library.
    assert not done.wait(0.5)
    assert len(judged) == 2
    clean_registry.resume("marc")
    assert done.wait(10)
    assert len(judged) == 5


def test_the_scheduler_gives_up_its_slot_while_paused(monkeypatch, caplog, clean_registry):
    """Starting the walk anyway would leave the pool asleep on the gate with
    the library held open all night."""
    clean_registry.pause("marc")
    monkeypatch.setattr("trackstarr.sweep.sweep", lambda **kwargs: pytest.fail("swept"))
    with caplog.at_level(logging.INFO):
        sweep_mod.run_scheduled()
    assert "paused" in caplog.text


def test_the_scheduler_skips_a_slot_a_sweep_is_still_filling(
    monkeypatch, caplog, clean_registry
):
    """Two walks would fight over the cache and over pending.tsv, which is one
    file both would be writing."""
    clean_registry.open_run("r#1", clean_registry.SWEEP)
    monkeypatch.setattr("trackstarr.sweep.sweep", lambda **kwargs: pytest.fail("swept"))
    sweep_mod.run_scheduled()
    assert "still running" in caplog.text


def test_a_free_slot_sweeps_on_the_configured_rung(monkeypatch, clean_registry):
    """The scheduler is the one caller with nobody to ask, so it reads
    REWRITE_MODE itself: only "all" puts it on the writing rung."""
    monkeypatch.setattr(config, "REWRITE_MODE", "all")
    seen: list[bool] = []
    monkeypatch.setattr("trackstarr.sweep.sweep", lambda dry_run: seen.append(dry_run))
    sweep_mod.run_scheduled()
    assert seen == [False]


def _one_file(tmp_path, monkeypatch) -> str:
    """One real file in a library of its own, so cache_key can stat it."""
    root = tmp_path / "library"
    root.mkdir()
    path = root / "one.mkv"
    path.write_text("not really a video")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    monkeypatch.setattr("trackstarr.sweep.walk_library", lambda policy: [str(path)])
    return str(path)


def test_a_failure_is_written_down_but_never_stands_in_for_the_work(monkeypatch, tmp_path):
    """Cached, so the library can show that a rewrite broke, but never a hit,
    since a failure records one attempt rather than a property of the file."""
    path = _one_file(tmp_path, monkeypatch)
    tried: list[str] = []

    def failing(job, dry_run, source=""):
        tried.append(job.path)
        return ProcessResult(Status.FAILED, None, "no space left on device")

    monkeypatch.setattr("trackstarr.sweep.process", failing)
    monkeypatch.setattr("trackstarr.sweep.match_path", lambda index, path: None)

    sweep(dry_run=False)
    stored = json.loads((Path(config.STATE_DIR) / "sweep-cache.json").read_text())
    assert stored["files"][path]["status"] == "failed"
    assert stored["files"][path]["why"] == {"failed": "no space left on device"}

    # The file has not moved, so anything else cached would be a hit here.
    sweep(dry_run=False)
    assert tried == [path, path]


def test_a_conforming_verdict_still_stands_in_for_the_work(monkeypatch, tmp_path):
    """The other side of the same guard: only a failure is re-run, or the
    cache would have stopped saving anything at all."""
    path = _one_file(tmp_path, monkeypatch)
    tried: list[str] = []

    def passing(job, dry_run, source=""):
        tried.append(job.path)
        return ProcessResult(Status.CONFORM, None)

    monkeypatch.setattr("trackstarr.sweep.process", passing)
    monkeypatch.setattr("trackstarr.sweep.match_path", lambda index, path: None)

    sweep(dry_run=False)
    sweep(dry_run=False)
    assert tried == [path]


def test_the_run_is_registered_before_the_arrs_are_listed(monkeypatch, tmp_path):
    """The page asks what is running the moment the start call answers, and
    listing the *arrs comes first, so a run registered after it was invisible
    for a whole idle poll interval."""
    _one_file(tmp_path, monkeypatch)
    seen: list[bool] = []

    def listing(*args, **kwargs):
        seen.append(bool(runs.running(runs.SWEEP)))
        return LibraryIndex({}, complete=True)

    monkeypatch.setattr("trackstarr.sweep.path_index", listing)
    monkeypatch.setattr("trackstarr.sweep.all_arrs", list)
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.CONFORM, None),
    )
    sweep(dry_run=True)
    assert seen == [True]


def test_an_arr_outage_marks_the_run_it_has_already_shown(monkeypatch, tmp_path):
    """The downgrade to report-only happens after the run is on screen, so it
    has to reach the record too: a card reading "Sweep" for a walk that has
    just been forced to report only is the wrong half of the answer."""
    _one_file(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "REWRITE_MODE", "all")
    monkeypatch.setattr("trackstarr.sweep.all_arrs", list)
    monkeypatch.setattr(
        "trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, complete=False)
    )
    monkeypatch.setattr(Policy, "needs_original_lang", lambda self: True)
    marked: list[bool] = []
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": (
            marked.append(runs.running(runs.SWEEP).dry_run),
            ProcessResult(Status.CONFORM, None),
        )[1],
    )
    sweep(dry_run=False)
    assert marked == [True]


def _failing_sweeps(monkeypatch, tmp_path, count: int, detail: str = "ffmpeg failed (1): boom"):
    """Run `count` applying sweeps over one file whose rewrite always fails;
    which of them spent a rewrite on it."""
    _library(tmp_path, monkeypatch, 1)
    attempts: list[str] = []

    def failing(job, dry_run, source="sweep"):
        if dry_run:
            return ProcessResult(Status.PENDING, None)
        attempts.append(job.path)
        return ProcessResult(Status.FAILED, None, detail)

    monkeypatch.setattr("trackstarr.sweep.process", failing)
    for _ in range(count):
        sweep(dry_run=False)
    return attempts


def test_a_file_that_keeps_failing_is_eventually_left_alone(monkeypatch, tmp_path):
    """A failure is retried, but an unrewritable file would otherwise cost a
    full attempt every sweep for ever."""
    attempts = _failing_sweeps(monkeypatch, tmp_path, sweep_mod.MAX_FAILURES + 3)

    assert len(attempts) == sweep_mod.MAX_FAILURES


def test_a_given_up_file_is_still_counted_and_reported(monkeypatch, tmp_path):
    """Giving up on the rewrite is not the same as forgetting the file: it
    still reads "failed", still lands in pending.tsv, and the row says why
    nothing is trying any more."""
    _failing_sweeps(monkeypatch, tmp_path, sweep_mod.MAX_FAILURES + 1)

    counts = read_events()[-1]["counts"]
    assert counts[str(Status.FAILED)] == 1
    (row,) = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert row.split("\t")[0] == str(Status.FAILED)
    detail = row.split("\t")[4]
    assert "boom" in detail
    assert "not retried until the file changes" in detail


def test_a_failure_with_no_words_on_it_still_says_nothing_is_trying(monkeypatch, tmp_path):
    """A stored failure that never carried a detail. The row still has to say
    the file has been left alone, or it reads as an attempt this sweep made
    and lost silently."""
    _failing_sweeps(monkeypatch, tmp_path, sweep_mod.MAX_FAILURES + 1, detail="")

    (row,) = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert row.split("\t")[4] == (
        f"failed {sweep_mod.MAX_FAILURES} times, not retried until the file changes"
    )


def test_a_report_that_cannot_be_written_does_not_cost_the_sweep(monkeypatch, tmp_path, caplog):
    """The report is the sweep's answer written down, and a full or read-only
    volume must not throw away the hours that produced it."""
    _library(tmp_path, monkeypatch, 1)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    # A directory where the report goes: open(..., "w") refuses it the same
    # way a read-only mount would.
    os.mkdir(os.path.join(config.STATE_DIR, "pending.tsv"))
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.PENDING),
    )

    counts = sweep(dry_run=True)
    assert counts[Status.PENDING] == 1
    assert "could not write" in caplog.text


def test_a_file_edited_after_giving_up_is_tried_again(monkeypatch, tmp_path):
    """Whatever the edit was, it may well be the thing that fixes it."""
    _failing_sweeps(monkeypatch, tmp_path, sweep_mod.MAX_FAILURES + 1)
    library = tmp_path / "library"
    (library / "000.mkv").write_text("a different file entirely")

    attempts: list[str] = []
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": (
            attempts.append(job.path) or ProcessResult(Status.CONFORM, None)
        ),
    )
    sweep(dry_run=False)

    assert len(attempts) == 1


def test_reporting_sweeps_neither_spend_the_budget_nor_stop_retrying(monkeypatch, tmp_path):
    """A report-only failure is a probe that would not read the file. It costs
    milliseconds to repeat and says nothing about whether the rewrite would
    work, so it must not use up the attempts an applying sweep is owed."""
    _library(tmp_path, monkeypatch, 1)
    probed: list[bool] = []

    def failing(job, dry_run, source="sweep"):
        probed.append(dry_run)
        return ProcessResult(Status.FAILED, None, "probe failed")

    monkeypatch.setattr("trackstarr.sweep.process", failing)
    for _ in range(sweep_mod.MAX_FAILURES + 2):
        sweep(dry_run=True)

    # Every reporting sweep probed it again rather than answering from the
    # stored failure, and not one of them was a rewrite.
    assert probed == [True] * (sweep_mod.MAX_FAILURES + 2)

    # So an applying sweep still has every attempt at the rewrite to spend.
    attempts: list[str] = []

    def rewriting(job, dry_run, source="sweep"):
        if dry_run:
            return ProcessResult(Status.PENDING, None)
        attempts.append(job.path)
        return ProcessResult(Status.FAILED, None, "ffmpeg failed (1): boom")

    monkeypatch.setattr("trackstarr.sweep.process", rewriting)
    for _ in range(sweep_mod.MAX_FAILURES + 1):
        sweep(dry_run=False)
    assert len(attempts) == sweep_mod.MAX_FAILURES


# Finding the work while doing it. An applying sweep walks on one pool and
# rewrites on another; on one, the walk stopped at its first rewrite.

#: Longest any of the threaded tests below waits for the sweep to get
#: somewhere. Generous on purpose: what they guard against is a stall, and a
#: loaded machine is slow rather than wrong.
_PATIENCE = 10.0


def _until(settled, complaint: str) -> None:
    """Block until the sweep has got somewhere, or say what it never did."""
    deadline = time.monotonic() + _PATIENCE
    while not settled():
        assert time.monotonic() < deadline, complaint
        time.sleep(0.01)


class _Sweeping:
    """One applying sweep mid-flight with every rewrite blocked. ``walked`` is
    what the walk found, ``rewriting`` what a worker picked up, ``release``
    lets them finish."""

    def __init__(self, run: str):
        self.run = run
        self.walked: list[str] = []
        self.rewriting: list[str] = []
        self._held = threading.Event()

    def process(self, job, dry_run, source="sweep"):
        if dry_run:
            self.walked.append(job.path)
            return ProcessResult(Status.PENDING, None)
        self.rewriting.append(job.path)
        self._held.wait(_PATIENCE)
        return ProcessResult(Status.MODIFIED, None)

    def release(self) -> None:
        self._held.set()

    def snapshot(self) -> dict:
        (run,) = runs.snapshot()["runs"]
        return run


@contextlib.contextmanager
def _mid_sweep(monkeypatch, tmp_path, count: int, run: str = "r#1"):
    """An applying sweep held at the moment worth looking at: the walk all the
    way round, one rewrite being worked on, and none of them finished."""
    _library(tmp_path, monkeypatch, count)
    monkeypatch.setattr(config, "PROBE_WORKERS", 2)
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    sweeping = _Sweeping(run)
    monkeypatch.setattr("trackstarr.sweep.process", sweeping.process)
    walking = threading.Thread(target=sweep, kwargs={"dry_run": False, "run": run})
    walking.start()
    try:
        _until(
            lambda: len(sweeping.walked) == count and sweeping.rewriting,
            "the walk stalled behind the rewrite it found",
        )
        yield sweeping
    finally:
        sweeping.release()
        walking.join(_PATIENCE)
        assert not walking.is_alive(), "the sweep never finished"


def test_the_walk_keeps_finding_work_while_a_rewrite_holds_a_slot(monkeypatch, tmp_path):
    """The whole reason the two are separate pools. With one budget-sized pool
    doing both, the walk gets no further than its first pending file."""
    with _mid_sweep(monkeypatch, tmp_path, 6) as sweeping:
        assert len(sweeping.walked) == 6
        assert len(sweeping.rewriting) == 1, "the rewrite budget was not the walk's"


def test_a_sweep_says_what_it_has_left_to_do_before_it_has_done_any_of_it(
    monkeypatch, tmp_path, clean_registry
):
    """What the walk is for, from the page's side: the work is known while it
    is still waiting, rather than only once it is finished."""
    with _mid_sweep(monkeypatch, tmp_path, 6) as sweeping:
        _until(lambda: not sweeping.snapshot()["walking"], "the walk never finished")
        # Six found, one being worked on, five still waiting on the one worker.
        assert sweeping.snapshot()["queued"] == 5


def test_a_sweep_is_still_walking_until_it_has_seen_every_file(monkeypatch, tmp_path):
    """A queue under a walk that is still going is the work found so far, and
    a page that read it as the whole would promise an end that keeps moving."""
    _library(tmp_path, monkeypatch, 4)
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)
    seen: list[bool] = []

    def watching(job, dry_run, source="sweep"):
        (run,) = runs.snapshot()["runs"]
        seen.append(run["walking"])
        return ProcessResult(Status.CONFORM)

    monkeypatch.setattr("trackstarr.sweep.process", watching)
    sweep(dry_run=True, run="r#1")

    assert seen == [True] * 4


def test_a_stopped_sweep_says_what_it_found_rather_than_forgetting_it(
    monkeypatch, tmp_path, clean_registry
):
    """A file the walk judged and no rewrite reached was still looked at, and
    a pending verdict is what was found. Dropping it would lose the answer as well as
    the rewrite."""
    with _mid_sweep(monkeypatch, tmp_path, 6) as sweeping:
        _until(lambda: not sweeping.snapshot()["walking"], "the walk never finished")
        clean_registry.stop("r#1")

    counts = read_events()[-1]["counts"]
    assert counts[str(Status.MODIFIED)] == 1, "the rewrite already going still finished"
    assert counts[str(Status.PENDING)] == 5
    # Every file was looked at, so none of them is unjudged.
    assert "stopped" not in read_events()[-1]


def test_a_sweep_queues_its_work_with_how_long_it_will_take(
    monkeypatch, tmp_path, clean_registry
):
    """The estimate is made where the work is found, so the page can say how
    long the backlog will take before any of it has been done."""
    _library(tmp_path, monkeypatch, 3)
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    # An hour of film each, at a hundred times realtime: 36 seconds apiece.
    monkeypatch.setattr(estimate, "measured", lambda: estimate.Speeds({("2.0",): 100.0}))
    planned = [{"codec": "aac", "title": "2.0", "flags": ["generated"]}]
    held = threading.Event()

    def judge(path, index=None, cache=None, dry_run=False, run="", force=False, rewriting=None):
        if not dry_run:
            held.wait(_PATIENCE)
            return Judged(Job(path, run=run), None, Verdict(Status.CONFORM))
        return Judged(
            Job(path, run=run),
            None,
            Verdict(Status.PENDING, "add a 2.0", planned=planned, duration=3600.0),
        )

    monkeypatch.setattr("trackstarr.sweep._judge", judge)
    walking = threading.Thread(target=sweep, kwargs={"dry_run": False, "run": "r#1"})
    walking.start()
    try:
        _until(
            lambda: runs.snapshot()["runs"][0]["queued"] == 3,
            "the walk never queued everything it found",
        )
        (run,) = runs.snapshot()["runs"]
        assert run["walking"] is False, "and it had finished looking for more"
        # Three hours of film, at a hundred times realtime, through one slot.
        assert run["rewrite_seconds"] == 108.0
    finally:
        held.set()
        walking.join(_PATIENCE)
        assert not walking.is_alive()


def test_the_report_gives_a_file_its_final_verdict_and_only_that(monkeypatch, tmp_path):
    """A pending verdict is written down while it waits for a slot, so the report has
    to be the file's answer rather than both of them one after the other."""
    names = _library(tmp_path, monkeypatch, 4)
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="sweep": ProcessResult(
            Status.PENDING if dry_run else Status.MODIFIED, None
        ),
    )
    sweep(dry_run=False)

    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert [row.split("\t")[0] for row in rows] == [str(Status.MODIFIED)] * 4
    # One row each, and the one the rewrite reached rather than the one the
    # walk queued. Their order is the walk's; see the test above.
    assert sorted(Path(row.split("\t")[2]).name for row in rows) == sorted(names)


def test_an_applying_sweep_leaves_a_held_title_where_the_walk_found_it(monkeypatch, tmp_path):
    """A held file is booked as discovery judged it rather than queued: the
    rewrite would latch to report anyway, having spent a slot and a second
    probe getting there."""
    _library(tmp_path, monkeypatch, 3)
    holds.place(str(tmp_path / "library"), by="marc", reason="watching one of them")
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="sweep": ProcessResult(
            Status.PENDING if dry_run else Status.MODIFIED, None
        ),
    )
    counts = sweep(dry_run=False)
    assert counts[Status.PENDING] == 3
    assert counts[Status.MODIFIED] == 0, "nothing reached the rewrite pool"
    # The reason on every row, including the ones a warm cache answered, which
    # never pass through process() to say it themselves.
    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert all("held by marc" in row for row in rows)
    assert all("watching one of them" in row for row in rows)


def test_a_file_skipped_mid_sweep_keeps_the_verdict_the_walk_gave_it(monkeypatch, tmp_path):
    """Skipping is "not in this pass": the file keeps its pending verdict, so the
    library still shows the work and the next sweep picks it up."""
    _library(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="sweep": ProcessResult(
            Status.PENDING if dry_run else Status.MODIFIED, None
        ),
    )
    monkeypatch.setattr(runs, "skipped", lambda run, path: True)
    counts = sweep(dry_run=False, run="r#1")
    assert counts[Status.PENDING] == 1
    assert counts[Status.MODIFIED] == 0
    rows = (Path(config.STATE_DIR) / "pending.tsv").read_text().splitlines()[1:]
    assert "skipped for this run" in rows[0]


def test_a_queued_file_reads_as_pending_while_it_waits_for_its_rewrite(monkeypatch, tmp_path):
    """The library shows what the sweep is going to do to a file while it is
    still waiting for a slot, rather than the verdict it had before the sweep
    started or nothing at all."""
    # Every pass, so the wait the test is about is one it can see inside.
    monkeypatch.setattr(sweep_mod, "_CHECKPOINT_SECONDS", 0)
    stored = Path(config.STATE_DIR) / "sweep-cache.json"

    def pending() -> set[str]:
        if not stored.exists():
            return set()
        return {entry["status"] for entry in json.loads(stored.read_text())["files"].values()}

    with _mid_sweep(monkeypatch, tmp_path, 3):
        _until(lambda: pending() == {str(Status.PENDING)}, "the work found was never written")

    # And gone again once the rewrites answered them: a file that has just been
    # rewritten is not the file the entry described.
    assert pending() == set()


# Re-checking chosen titles: the same walk pointed at a folder, ignoring the
# stored verdict, which is usually what is doubted.


def _folder(tmp_path, monkeypatch, name: str, *files: str) -> str:
    """One title folder under MEDIA_DIRS, holding these files."""
    root = tmp_path / "media"
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for file_name in files:
        (folder / file_name).write_text("not really a video")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    return str(folder)


def test_a_recheck_reprobes_a_file_the_cache_has_already_judged(
    monkeypatch, tmp_path, clean_registry
):
    """The whole reason it exists. A sweep of a settled library answers from
    the cache, so pressing this on a title whose verdict looks wrong would
    otherwise hand back that same verdict without opening the file."""
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    probed: list[str] = []
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": (
            probed.append(job.path) or ProcessResult(Status.CONFORM, None)
        ),
    )
    sweep(dry_run=True)
    assert len(probed) == 1, "the sweep judged it once"

    # A second sweep would read the cache and never call process again.
    sweep(dry_run=True)
    assert len(probed) == 1

    sweep_mod.recheck([folder], dry_run=True, run="r#2")
    assert len(probed) == 2, "asked for by name, so it is read again"


def test_a_recheck_leaves_the_rest_of_the_librarys_verdicts_alone(
    monkeypatch, tmp_path, clean_registry
):
    """save() drops every unvisited entry, which for a walk of one folder is
    the rest of the library."""
    dune = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    _folder(tmp_path, monkeypatch, "Arrival (2016)", "Arrival.mkv")
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.CONFORM, None),
    )
    sweep(dry_run=True)

    sweep_mod.recheck([dune], dry_run=True, run="r#2")

    stored = json.loads((Path(config.STATE_DIR) / "sweep-cache.json").read_text())
    assert sorted(os.path.basename(path) for path in stored["files"]) == [
        "Arrival.mkv",
        "Dune.mkv",
    ]


def test_a_recheck_replaces_the_verdict_it_re_judged(monkeypatch, tmp_path, clean_registry):
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    verdicts = iter([Status.PENDING, Status.CONFORM])
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(next(verdicts), None),
    )
    sweep(dry_run=True)
    sweep_mod.recheck([folder], dry_run=True, run="r#2")

    stored = json.loads((Path(config.STATE_DIR) / "sweep-cache.json").read_text())
    assert [entry["status"] for entry in stored["files"].values()] == ["conform"]


def test_a_recheck_does_not_overwrite_the_last_sweeps_report(
    monkeypatch, tmp_path, clean_registry
):
    """pending.tsv is the last sweep's answer about a whole library, written
    open-and-truncate. Replacing it with the two rows a selection produced
    would destroy that answer without replacing it."""
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    _folder(tmp_path, monkeypatch, "Arrival (2016)", "Arrival.mkv")
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.PENDING, None),
    )
    sweep(dry_run=True)
    before = (Path(config.STATE_DIR) / "pending.tsv").read_text()

    sweep_mod.recheck([folder], dry_run=True, run="r#2")

    assert (Path(config.STATE_DIR) / "pending.tsv").read_text() == before


def test_a_recheck_records_what_it_looked_at(monkeypatch, tmp_path, clean_registry):
    """Counted in titles as well as files: that is what somebody picked, and
    it is the half that says this was a shelf rather than the library."""
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv", "Dune-extras.mkv")
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.CONFORM, None),
    )
    sweep_mod.recheck([folder], dry_run=True, run="r#2")

    summary = [entry for entry in read_events() if entry["event"] == "recheck"]
    assert len(summary) == 1
    assert (summary[0]["titles"], summary[0]["files"]) == (1, 2)
    assert summary[0]["counts"]["conform"] == 2
    assert summary[0]["run"] == "r#2"


def test_a_recheck_registers_itself_so_the_page_can_watch_it(
    monkeypatch, tmp_path, clean_registry
):
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    seen: list[dict] = []

    def judge(path, **kwargs):
        seen.append(clean_registry.snapshot()["runs"][0])
        return Judged(Job(path), None, Verdict(Status.CONFORM))

    monkeypatch.setattr("trackstarr.sweep._judge", judge)
    sweep_mod.recheck([folder], dry_run=True, run="r#2", label="Dune")

    assert (seen[0]["kind"], seen[0]["label"]) == ("recheck", "Dune")
    assert seen[0]["dry_run"] is True
    # Closed when it ends, or the next one would be refused for ever.
    assert clean_registry.snapshot()["runs"] == []


def test_a_stopped_recheck_counts_only_what_it_reached(monkeypatch, tmp_path, clean_registry):
    folder = _folder(tmp_path, monkeypatch, "Show", "a.mkv", "b.mkv", "c.mkv")
    monkeypatch.setattr(config, "PROBE_WORKERS", 1)

    def judged_then_stopped(job, dry_run, source=""):
        # The stop lands after the first file, so the two behind it are never
        # looked at rather than being judged and thrown away.
        clean_registry.stop("r#2")
        return ProcessResult(Status.CONFORM, None)

    monkeypatch.setattr("trackstarr.sweep.process", judged_then_stopped)
    counts = sweep_mod.recheck([folder], dry_run=True, run="r#2")

    assert sum(counts.values()) == 1
    summary = next(entry for entry in read_events() if entry["event"] == "recheck")
    assert (summary["files"], summary["stopped"]) == (1, 2)


def test_a_recheck_reports_only_when_the_install_is_latched_to_report(
    monkeypatch, tmp_path, clean_registry
):
    """REWRITE_MODE latches over every caller. A new entry point must not be
    the one that gets talked into rewriting a library its owner is watching."""
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    asked: list[bool] = []
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": (
            asked.append(dry_run) or ProcessResult(Status.CONFORM, None)
        ),
    )
    sweep_mod.recheck([folder], dry_run=False, run="r#2")

    assert asked == [True]


def test_a_recheck_reports_only_when_a_arr_cannot_be_listed(
    monkeypatch, tmp_path, clean_registry
):
    """With original languages unknown the languages rule would read a foreign
    film's own track as junk to drop. The same trade the sweep makes, and the
    run's own record has to say so or the page shows a rewrite that is not."""
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    monkeypatch.setattr("trackstarr.sweep.all_arrs", list)
    monkeypatch.setattr(
        "trackstarr.sweep.path_index", lambda arrs: LibraryIndex({}, complete=False)
    )
    monkeypatch.setattr(Policy, "needs_original_lang", lambda self: True)
    marked: list[bool] = []
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": (
            marked.append(runs.running(runs.RECHECK).dry_run),
            ProcessResult(Status.CONFORM, None),
        )[1],
    )
    sweep_mod.recheck([folder], dry_run=False, run="r#2")

    assert marked == [True]


def test_a_recheck_keeps_walking_while_its_rewrite_holds_a_slot(monkeypatch, tmp_path):
    """The same split the sweep walks on, which a re-check used not to have: it
    rewrote on the probe threads, so a selection with work in it got no further
    than its first encode before it stopped looking at the rest."""
    folder = _folder(tmp_path, monkeypatch, "Show", *[f"{n}.mkv" for n in range(6)])
    monkeypatch.setattr(config, "PROBE_WORKERS", 2)
    monkeypatch.setattr(config, "MAX_CONCURRENT_REWRITES", 1)
    rechecking = _Sweeping("r#2")
    monkeypatch.setattr("trackstarr.sweep.process", rechecking.process)
    walking = threading.Thread(
        target=sweep_mod.recheck, args=([folder],), kwargs={"dry_run": False, "run": "r#2"}
    )
    walking.start()
    try:
        _until(
            lambda: len(rechecking.walked) == 6 and rechecking.rewriting,
            "the re-check stalled behind the rewrite it found",
        )
        assert len(rechecking.rewriting) == 1, "the rewrite budget was not the walk's"
    finally:
        rechecking.release()
        walking.join(_PATIENCE)
        assert not walking.is_alive(), "the re-check never finished"


def test_a_recheck_writes_down_what_it_learns_as_it_goes(monkeypatch, tmp_path, clean_registry):
    """It used to write once, at the end. A selection is minutes of probing
    that a restart threw away, and it is the walk somebody is watching."""
    folder = _folder(tmp_path, monkeypatch, "Show", "a.mkv", "b.mkv", "c.mkv")
    monkeypatch.setattr("trackstarr.sweep._CHECKPOINT_SECONDS", 0.0)
    checkpoints: list[int] = []
    monkeypatch.setattr(
        "trackstarr.sweep_cache.SweepCache.checkpoint", lambda self: checkpoints.append(1)
    )
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.CONFORM, None),
    )

    sweep_mod.recheck([folder], dry_run=True, run="r#2")

    assert len(checkpoints) == 3


def test_a_recheck_tries_a_file_the_sweeps_gave_up_on(monkeypatch, tmp_path, clean_registry):
    """A re-check asks for a file by name, which the failure ceiling gives way
    to. The attempts are spent on the rewrite thread, so `force` must reach
    it."""
    _failing_sweeps(monkeypatch, tmp_path, sweep_mod.MAX_FAILURES + 1)
    attempts: list[str] = []

    def failing(job, dry_run, source="sweep"):
        if dry_run:
            return ProcessResult(Status.PENDING, None)
        attempts.append(job.path)
        return ProcessResult(Status.FAILED, None, "ffmpeg failed (1): boom")

    monkeypatch.setattr("trackstarr.sweep.process", failing)
    sweep(dry_run=False)
    assert attempts == [], "the sweep has given up on it"

    sweep_mod.recheck([str(tmp_path / "library")], dry_run=False, run="r#2")

    assert len(attempts) == 1


def _judged(path: str, **kwargs) -> Judged:
    """One file judged from scratch, with a key, so the cache stores it."""
    return Judged(Job(path), FileKey(10, 1, 1, None), Verdict(Status.CONFORM))


def _bells(monkeypatch) -> list[str]:
    """Every kind published while the test runs, in order."""
    kinds: list[str] = []
    monkeypatch.setattr(notify, "publish", kinds.append)
    return kinds


def test_a_sweep_says_the_library_moved_before_its_first_checkpoint(
    monkeypatch, tmp_path, clean_registry
):
    """The cache file only moves every _CHECKPOINT_SECONDS, so a page with
    nothing to fetch until then reads a cold walk as an untouched library."""
    _library(tmp_path, monkeypatch, 3)
    monkeypatch.setattr("trackstarr.sweep._judge", _judged)
    kinds = _bells(monkeypatch)
    sweep(dry_run=True)
    assert kinds.count(notify.LIBRARY) == 1


def test_the_floor_is_what_holds_a_walks_bell_down(monkeypatch, tmp_path, clean_registry):
    """Three files in the same instant is one thing worth looking at; without
    the floor it is a message and a shelf fetched per file per tab."""
    _library(tmp_path, monkeypatch, 3)
    monkeypatch.setattr("trackstarr.sweep._judge", _judged)
    monkeypatch.setattr(sweep_mod, "_PUBLISH_SECONDS", 0.0)
    kinds = _bells(monkeypatch)
    sweep(dry_run=True)
    assert kinds.count(notify.LIBRARY) == 3


def test_the_view_stands_before_the_bell_is_rung(monkeypatch, tmp_path, clean_registry):
    """A page that fetches the moment it is told must not find the answer it
    was told about still on its way."""
    _library(tmp_path, monkeypatch, 1)
    monkeypatch.setattr("trackstarr.sweep._judge", _judged)
    offered: list[int] = []

    def watch(kind: str) -> None:
        if kind != notify.LIBRARY:
            return
        view = sweep_cache.live_view(Policy.from_config().fingerprint())
        offered.append(len(view[1]) if view else 0)

    monkeypatch.setattr(notify, "publish", watch)
    sweep(dry_run=True)
    assert offered == [1]


def test_a_warm_sweep_says_nothing_about_the_library(monkeypatch, tmp_path, clean_registry):
    """carry() brings an unchanged file's entry forward byte for byte, so a
    settled library's nightly sweep has nothing to tell anyone and nothing to
    build a view out of."""
    _library(tmp_path, monkeypatch, 3)
    monkeypatch.setattr(
        "trackstarr.sweep._judge",
        lambda path, **kwargs: Judged(
            Job(path), FileKey(10, 1, 1, None), Verdict(Status.CONFORM), cached=True
        ),
    )
    kinds = _bells(monkeypatch)
    sweep(dry_run=True)
    assert notify.LIBRARY not in kinds


def test_a_recheck_says_the_library_moved_as_it_goes(monkeypatch, tmp_path, clean_registry):
    """It writes once, at the end, and is always a walk somebody is watching."""
    folder = _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    monkeypatch.setattr("trackstarr.sweep._judge", _judged)
    kinds = _bells(monkeypatch)
    sweep_mod.recheck([folder], dry_run=True, run="r#2")
    assert kinds.count(notify.LIBRARY) == 1


def test_the_grid_reads_a_walk_before_the_cache_file_exists(
    monkeypatch, tmp_path, clean_registry
):
    """End to end, which is the whole point of the machinery: the walk offers
    what it has judged, the bell goes, and the library builds a card out of it
    with nothing yet written anywhere."""
    _folder(tmp_path, monkeypatch, "Dune (2024)", "Dune.mkv")
    monkeypatch.setattr(
        "trackstarr.sweep.process",
        lambda job, dry_run, source="": ProcessResult(Status.PENDING),
    )
    library.forget()
    seen: list[tuple[str, bool]] = []

    def watch(kind: str) -> None:
        if kind != notify.LIBRARY:
            return
        card = library.shelf()["titles"][0]
        seen.append((card["state"], os.path.exists(sweep_cache.cache_path())))

    monkeypatch.setattr(notify, "publish", watch)
    sweep(dry_run=True)
    assert seen == [("pending", False)]
