"""Titles and files somebody wants left alone for a while."""

import fcntl
import json
import os
import select
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

from conftest import read_events
from trackstarr import config, events, notify, pauses

MOVIES = "/data/media/movies"
DUNE = f"{MOVIES}/Dune (2024)"
DUNE_FILE = f"{DUNE}/Dune (2024).mkv"
ARRIVAL_FILE = f"{MOVIES}/Arrival (2016)/Arrival (2016).mkv"


def test_legacy_pause_metadata_is_read_and_written_with_explicit_names():
    path = Path(config.STATE_DIR) / pauses.PAUSES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({DUNE: {"title": "arr:radarr:1", "name": "Dune"}}))
    held = pauses.paused(DUNE_FILE)
    assert held.title_id == "arr:radarr:1"
    assert held.title_name == "Dune"
    pauses.place(ARRIVAL_FILE)
    stored = json.loads(path.read_text())[DUNE]
    assert stored["title_id"] == held.title_id
    assert stored["title_name"] == held.title_name
    assert not {"title", "name"} & stored.keys()


@pytest.fixture(autouse=True)
def _forget():
    """The store is memoised on the file's mark, and two tmp dirs can share
    one. Cleared both sides, as the registry's fixture is."""
    pauses.forget()
    yield
    pauses.forget()


def store_path() -> str:
    return os.path.join(config.STATE_DIR, pauses.PAUSES_FILE)


def test_a_pause_on_a_folder_covers_the_files_under_it():
    """Somebody pauses a title, not a path: the sheet knows the folder and the
    pipeline judges files."""
    pauses.place(DUNE, by="operator")
    assert pauses.paused(DUNE_FILE) is not None
    assert pauses.paused(DUNE) is not None
    assert pauses.paused(ARRIVAL_FILE) is None


def test_a_pause_lapses_on_its_own():
    """A pause for the evening must not become one for ever because nobody came
    back to resume it."""
    pauses.place(DUNE, seconds=3600)
    assert pauses.paused(DUNE_FILE) is not None
    assert pauses.paused(DUNE_FILE, now=_after(2 * 3600)) is None


def test_a_lapsed_inner_pause_does_not_hide_a_standing_outer_one():
    """The innermost match wins, so a two-hour pause on one episode must not
    answer for the series paused until further notice."""
    pauses.place(f"{MOVIES}", by="operator")
    pauses.place(DUNE, seconds=3600, by="operator")
    later = _after(2 * 3600)
    assert pauses.paused(DUNE_FILE, now=later) is not None
    assert pauses.paused(DUNE_FILE, now=later).path == MOVIES


def test_the_innermost_pause_is_the_one_reported():
    pauses.place(MOVIES)
    pauses.place(DUNE)
    assert pauses.paused(DUNE_FILE).path == DUNE


def test_expired_reads_do_not_write_and_next_mutation_collects(monkeypatch):
    pauses.place(DUNE, seconds=3600)
    before = Path(store_path()).read_bytes()
    later = _after(7200)
    assert pauses.current(now=later) == []
    assert Path(store_path()).read_bytes() == before
    monkeypatch.setattr(pauses.time, "time", lambda: later)
    pauses.place(ARRIVAL_FILE)
    assert list(json.loads(Path(store_path()).read_text())) == [ARRIVAL_FILE]


def test_pausing_the_same_thing_again_extends_it():
    """Pressing "another two hours" is the same call, not a second entry."""
    pauses.place(DUNE, seconds=3600)
    pauses.place(DUNE, seconds=7200)
    assert len(pauses.current()) == 1
    assert pauses.paused(DUNE_FILE).until > _after(3600)


def test_resuming_says_whether_there_was_anything_to_resume():
    pauses.place(DUNE)
    assert pauses.resume(DUNE, by="operator") is not None
    assert pauses.paused(DUNE_FILE) is None
    assert pauses.resume(DUNE) is None


def test_a_pause_survives_a_restart():
    """An evening's pause must not lapse because the container was updated in
    the middle of it."""
    pauses.place(DUNE, seconds=3600, by="operator")
    pauses.forget()
    found = pauses.paused(DUNE_FILE)
    assert found is not None
    assert found.by == "operator"


def test_a_pause_cannot_outlive_the_ceiling():
    """A pause nobody remembers placing reads as the tool being broken."""
    placed = pauses.place(DUNE, seconds=10 * 365 * 86400)
    assert placed.until <= _after(pauses.MAX_SECONDS)


def test_a_damaged_record_still_holds_the_file():
    """Half a pause is still somebody saying "not this one". Losing it would
    rewrite the file they were watching."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store:
        json.dump({DUNE: {"until": "tomorrow", "by": ["a", "list"]}}, store)
    found = pauses.paused(DUNE_FILE)
    assert found is not None
    assert found.until == 0.0, "an unreadable end reads as no end, not as lapsed"


def test_an_unreadable_store_holds_nothing():
    """The alternative is an install that cannot rewrite anything with nothing
    able to say why."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store:
        store.write("{ not json")
    assert pauses.paused(DUNE_FILE) is None


def test_pausing_and_resuming_are_both_in_the_history():
    """Two people share an install; "why did this not get rewritten" has to be
    answerable."""
    pauses.place(DUNE, seconds=3600, by="operator")
    pauses.resume(DUNE, by="operator")
    lines = read_events()
    kinds = [(entry["event"], entry.get("by")) for entry in lines]
    assert kinds == [("item_paused", "operator"), ("item_resumed", "operator")]
    # Whole seconds, stamped just before the line itself.
    began = datetime.fromisoformat(lines[1]["paused_at"])
    assert 0 <= (datetime.fromisoformat(lines[0]["ts"]) - began).total_seconds() <= 1


def test_nothing_is_paused_by_default():
    assert pauses.paused(DUNE_FILE) is None
    assert pauses.current() == []
    assert not pauses.full()


def _after(seconds: float) -> float:
    return time.time() + seconds


def test_existing_hold_store_migrates_without_losing_a_pause():
    pauses.place(DUNE, 3600, by="operator")
    current = Path(config.STATE_DIR) / pauses.PAUSES_FILE
    legacy = current.with_name("holds.json")
    current.rename(legacy)
    pauses.forget()

    found = pauses.paused(DUNE_FILE)
    assert found is not None
    assert found.by == "operator"
    assert current.exists()
    assert not legacy.exists()
    pauses.resume(DUNE)
    pauses.forget()
    assert pauses.paused(DUNE_FILE) is None


def test_batch_capacity_duplicates_and_extension(monkeypatch):
    monkeypatch.setattr(pauses, "MAX_PAUSES", 2)
    placed = pauses.place_many([(DUNE, "", "old"), (DUNE, "", "new"), (ARRIVAL_FILE, "", "")])
    assert len(placed) == 2
    assert placed[0].title_name == "new"
    assert pauses.full()
    pauses.place(DUNE, seconds=3600)
    before = Path(store_path()).read_bytes()
    with pytest.raises(pauses.CapacityError):
        pauses.place_many([(DUNE, "", "changed"), ("/third", "", "")])
    assert Path(store_path()).read_bytes() == before
    assert len(pauses.resume_many([DUNE, DUNE, "/missing", ARRIVAL_FILE])) == 2
    assert pauses.current() == []
    assert pauses.place(DUNE, seconds=-1).until == 0


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), True, "bad"])
def test_invalid_duration_does_not_commit(seconds):
    with pytest.raises(ValueError):
        pauses.place_many([(DUNE, "", "")], seconds)
    assert not Path(store_path()).exists()


def test_complete_batch_validated_before_commit():
    with pytest.raises(ValueError):
        pauses.place_many([(DUNE, "", ""), ("", "", "")])
    with pytest.raises(ValueError):
        pauses.resume_many([DUNE, ""])
    assert pauses.place_many([]) == []
    assert not Path(store_path()).exists()


@pytest.mark.parametrize(
    "spelling",
    [
        f"{MOVIES}/./Dune (2024)",
        f"{MOVIES}//Dune (2024)",
        f"{MOVIES}/Dune (2024)/",
        f"{MOVIES}/Arrival (2016)/../Dune (2024)",
        f"/{DUNE}",  # Leading double separator, which POSIX leaves special.
    ],
)
def test_a_pause_placed_by_another_spelling_covers_the_file(spelling):
    """The path a page sends and the path the sweep walked are the same file;
    a pause stored under either has to stop the rewrite."""
    placed = pauses.place(spelling)
    assert placed.path == DUNE
    assert pauses.paused(DUNE_FILE) is not None
    assert list(json.loads(Path(store_path()).read_text())) == [DUNE]


def test_either_spelling_reads_and_resumes_the_one_pause():
    pauses.place(f"{MOVIES}/./Dune (2024)", by="operator")
    pauses.forget()  # As a restart reads it.
    assert pauses.paused(f"{DUNE}//Dune (2024).mkv").by == "operator"
    assert pauses.resume(f"{MOVIES}/Dune (2024)/") is not None
    pauses.forget()
    assert pauses.current() == []


def test_relative_paths_are_refused_rather_than_read_against_the_working_dir():
    with pytest.raises(ValueError):
        pauses.place("Dune (2024)")
    with pytest.raises(ValueError):
        pauses.resume_many([DUNE, "../movies"])
    assert not Path(store_path()).exists()


def test_a_sibling_folder_with_the_same_prefix_is_not_covered():
    pauses.place(DUNE)
    assert pauses.paused(f"{MOVIES}/Dune (2024) Extended/film.mkv") is None


def test_spellings_of_one_file_count_once_against_capacity(monkeypatch):
    monkeypatch.setattr(pauses, "MAX_PAUSES", 1)
    placed = pauses.place_many([(DUNE, "", "first"), (f"{MOVIES}/./Dune (2024)", "", "second")])
    assert [pause.title_name for pause in placed] == ["second"]
    assert pauses.full()


@pytest.mark.parametrize("resume", [False, True])
def test_batch_commits_once_then_notifies_and_audits(monkeypatch, resume):
    targets = [(DUNE, "", ""), (ARRIVAL_FILE, "", "")]
    if resume:
        pauses.place_many(targets)
    saved = []
    emitted = []
    save = pauses._save

    def saving(found):
        save(found)
        saved.append(set(found))

    def published(*args, **kwargs):
        emitted.append(args[0])
        assert len(saved) == 1
        assert pauses._transaction_lock.acquire(blocking=False)
        pauses._transaction_lock.release()
        with open(store_path() + ".lock") as sidecar:
            fcntl.flock(sidecar, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(sidecar, fcntl.LOCK_UN)
        assert set(json.loads(Path(store_path()).read_text())) == saved[0]

    monkeypatch.setattr(pauses, "_save", saving)
    monkeypatch.setattr(notify, "publish", published)
    monkeypatch.setattr(events, "record", published)
    if resume:
        pauses.resume_many([DUNE, ARRIVAL_FILE])
    else:
        pauses.place_many(targets)
    assert len(saved) == 1
    assert emitted == [notify.RUNS] + ["item_resumed" if resume else "item_paused"] * 2


@pytest.mark.parametrize("resume", [False, True])
def test_write_failure_preserves_disk_and_memo(monkeypatch, resume):
    pauses.place(DUNE)
    assert pauses.current()
    before = Path(store_path()).read_bytes()
    memo = pauses._cached

    def failed(*args):
        raise OSError("read-only")

    monkeypatch.setattr(pauses.os, "replace", failed)
    with pytest.raises(OSError):
        if resume:
            pauses.resume_many([DUNE])
        else:
            pauses.place_many([(DUNE, "", "changed"), (ARRIVAL_FILE, "", "")])
    assert pauses._cached is memo
    assert Path(store_path()).read_bytes() == before
    assert pauses.paused(DUNE) is not None


def test_audit_failure_does_not_refuse_committed_pause(monkeypatch, caplog):
    monkeypatch.setattr(events, "path", lambda: config.STATE_DIR)  # Opening a directory fails.
    assert pauses.place(DUNE).path == DUNE
    assert pauses.resume(DUNE).path == DUNE
    assert "could not record item_paused" in caplog.text
    assert "could not record item_resumed" in caplog.text


@pytest.mark.parametrize("resume", [False, True])
def test_concurrent_mutations_have_a_serial_outcome(monkeypatch, resume):
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    save = pauses._save
    transaction = pauses._transaction

    def saving(found):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        save(found)

    @contextmanager
    def trying():
        if entered.is_set():
            attempted.set()
        with transaction():
            yield

    monkeypatch.setattr(pauses, "_save", saving)
    monkeypatch.setattr(pauses, "_transaction", trying)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(pauses.place, DUNE)
        try:
            assert entered.wait(5)
            second = pool.submit(
                pauses.resume if resume else pauses.place, DUNE if resume else ARRIVAL_FILE
            )
            assert attempted.wait(5)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert {p.path for p in pauses.current()} == (set() if resume else {DUNE, ARRIVAL_FILE})


def test_expired_read_cannot_remove_a_concurrent_placement(monkeypatch):
    pauses.place(DUNE, seconds=1)
    read, release = threading.Event(), threading.Event()
    original = pauses._read

    def delayed():
        found = original()
        read.set()
        assert release.wait(5)
        return found

    monkeypatch.setattr(pauses, "_read", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(pauses.current, _after(10))
        try:
            assert read.wait(5)
            pauses.place(ARRIVAL_FILE)
        finally:
            release.set()
        assert reader.result(timeout=5) == []
    assert pauses.paused(ARRIVAL_FILE) is not None


def test_cooperating_process_waits_for_stable_sidecar():
    # The child first proves contention using LOCK_NB, then uses the real
    # transaction. Pipes coordinate the race without scheduling sleeps.
    script = """
import fcntl, sys
from trackstarr import config, pauses
config.STATE_DIR = sys.argv[1]
original = fcntl.flock
def checked(fd, operation):
    if operation == fcntl.LOCK_EX:
        try:
            original(fd, operation | fcntl.LOCK_NB)
        except BlockingIOError:
            print('blocked', flush=True)
        else:
            raise AssertionError('transaction did not hold the sidecar')
    original(fd, operation)
fcntl.flock = checked
pauses.place(sys.argv[2])
"""
    with pauses._transaction():
        child = subprocess.Popen(
            [sys.executable, "-c", script, config.STATE_DIR, ARRIVAL_FILE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert select.select([child.stdout], [], [], 5)[0]
            assert child.stdout.readline().strip() == "blocked"
            # Replace the JSON inode while holding the sidecar.
            pauses._save({DUNE: pauses.Pause(DUNE)})
        except BaseException:
            child.kill()
            child.communicate()
            raise
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, (stdout, stderr)
    assert {p.path for p in pauses.current()} == {DUNE, ARRIVAL_FILE}


def test_migration_during_placement_preserves_legacy_and_current_precedence():
    legacy = Path(config.STATE_DIR) / "holds.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({DUNE: {"by": "legacy"}}))
    pauses.place(ARRIVAL_FILE)
    assert pauses.paused(DUNE).by == "legacy"
    assert not legacy.exists()
    legacy.write_text(json.dumps({DUNE: {"by": "stale legacy"}}))
    pauses.place(DUNE, by="current")
    assert pauses.paused(DUNE).by == "current"


def test_expiration_frees_capacity_and_empty_resume_collects(monkeypatch):
    monkeypatch.setattr(pauses, "MAX_PAUSES", 1)
    pauses.place(DUNE, seconds=1)
    later = _after(10)
    monkeypatch.setattr(pauses.time, "time", lambda: later)
    pauses.place(ARRIVAL_FILE, seconds=1)
    assert pauses.paused(DUNE) is None
    monkeypatch.setattr(pauses.time, "time", lambda: later + 10)
    assert pauses.resume_many(["/missing"]) == []
    assert json.loads(Path(store_path()).read_text()) == {}


def test_legacy_read_rechecks_after_concurrent_placement(monkeypatch):
    legacy = Path(config.STATE_DIR) / "holds.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({DUNE: {}}))
    entered, release = threading.Event(), threading.Event()
    transaction = pauses._transaction

    @contextmanager
    def delayed():
        if threading.current_thread().name.startswith("legacy-reader"):
            entered.set()
            assert release.wait(5)
        with transaction():
            yield

    monkeypatch.setattr(pauses, "_transaction", delayed)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="legacy-reader") as pool:
        reader = pool.submit(pauses.current)
        try:
            assert entered.wait(5)
            pauses.place(ARRIVAL_FILE)
        finally:
            release.set()
        assert {p.path for p in reader.result(timeout=5)} == {DUNE, ARRIVAL_FILE}
    assert not legacy.exists()
