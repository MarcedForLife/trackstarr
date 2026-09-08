"""Titles and files somebody wants left alone for a while."""

import json
import os
import time

import pytest

from conftest import read_events
from trackstarr import config, holds

MOVIES = "/data/media/movies"
DUNE = f"{MOVIES}/Dune (2024)"
DUNE_FILE = f"{DUNE}/Dune (2024).mkv"
ARRIVAL_FILE = f"{MOVIES}/Arrival (2016)/Arrival (2016).mkv"


@pytest.fixture(autouse=True)
def _forget():
    """The store is memoised on the file's mark, and two tmp dirs can share
    one. Cleared both sides, as the registry's fixture is."""
    holds.forget()
    yield
    holds.forget()


def store_path() -> str:
    return os.path.join(config.STATE_DIR, holds.HOLDS_FILE)


def test_a_hold_on_a_folder_covers_the_files_under_it():
    """Somebody holds a title, not a path: the sheet knows the folder and the
    pipeline judges files."""
    holds.place(DUNE, by="marc")
    assert holds.held(DUNE_FILE) is not None
    assert holds.held(DUNE) is not None
    assert holds.held(ARRIVAL_FILE) is None


def test_a_hold_lapses_on_its_own():
    """A hold for the evening must not become one for ever because nobody came
    back to lift it."""
    holds.place(DUNE, seconds=3600)
    assert holds.held(DUNE_FILE) is not None
    assert holds.held(DUNE_FILE, now=_after(2 * 3600)) is None


def test_a_lapsed_inner_hold_does_not_hide_a_standing_outer_one():
    """The innermost match wins, so a two-hour hold on one episode must not
    answer for the series held until further notice."""
    holds.place(f"{MOVIES}", by="marc")
    holds.place(DUNE, seconds=3600, by="marc")
    later = _after(2 * 3600)
    assert holds.held(DUNE_FILE, now=later) is not None
    assert holds.held(DUNE_FILE, now=later).path == MOVIES


def test_the_innermost_hold_is_the_one_reported():
    holds.place(MOVIES, reason="everything")
    holds.place(DUNE, reason="this one")
    assert holds.held(DUNE_FILE).reason == "this one"


def test_lapsed_holds_are_dropped_from_the_file_as_they_are_found():
    """Nothing else collects them, and a store that only grows would eventually
    be read on every file of every sweep."""
    holds.place(DUNE, seconds=3600)
    holds.place(ARRIVAL_FILE)
    assert len(holds.current(now=_after(2 * 3600))) == 1
    with open(store_path()) as store:
        assert list(json.load(store)) == [ARRIVAL_FILE]


def test_a_lapsed_hold_reads_as_gone_even_where_it_cannot_be_dropped(monkeypatch, caplog):
    """A read-only /config must not leave a lapsed hold answering for a title
    nothing is allowed to rewrite."""
    holds.place(DUNE, seconds=3600)

    def refuse(found):
        raise OSError("read-only file system")

    monkeypatch.setattr(holds, "_save", refuse)
    assert holds.current(now=_after(2 * 3600)) == []
    assert "lapsed" in caplog.text


def test_holding_the_same_thing_again_extends_it():
    """Pressing "another two hours" is the same call, not a second entry."""
    holds.place(DUNE, seconds=3600, reason="first")
    holds.place(DUNE, seconds=7200, reason="second")
    assert len(holds.current()) == 1
    assert holds.held(DUNE_FILE).reason == "second"


def test_lifting_says_whether_there_was_anything_to_lift():
    holds.place(DUNE)
    assert holds.lift(DUNE, by="marc") is not None
    assert holds.held(DUNE_FILE) is None
    assert holds.lift(DUNE) is None


def test_a_hold_survives_a_restart():
    """An evening's hold must not lapse because the container was updated in
    the middle of it."""
    holds.place(DUNE, seconds=3600, by="marc", reason="watching it")
    holds.forget()
    found = holds.held(DUNE_FILE)
    assert found is not None
    assert (found.by, found.reason) == ("marc", "watching it")


def test_a_hold_cannot_outlive_the_ceiling():
    """A hold nobody remembers placing reads as the tool being broken."""
    placed = holds.place(DUNE, seconds=10 * 365 * 86400)
    assert placed.until <= _after(holds.MAX_SECONDS)


def test_a_damaged_record_still_holds_the_file():
    """Half a hold is still somebody saying "not this one". Losing it would
    rewrite the file they were watching."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store:
        json.dump({DUNE: {"until": "tomorrow", "reason": ["a", "list"]}}, store)
    found = holds.held(DUNE_FILE)
    assert found is not None
    assert found.until == 0.0, "an unreadable end reads as no end, not as lapsed"


def test_an_unreadable_store_holds_nothing():
    """The alternative is an install that cannot rewrite anything with nothing
    able to say why."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store:
        store.write("{ not json")
    assert holds.held(DUNE_FILE) is None


def test_holding_and_lifting_are_both_in_the_history():
    """Two people share an install; "why did this not get rewritten" has to be
    answerable."""
    holds.place(DUNE, seconds=3600, by="marc", reason="watching it")
    holds.lift(DUNE, by="marc")
    kinds = [(entry["event"], entry.get("by")) for entry in read_events()]
    assert kinds == [("held", "marc"), ("lifted", "marc")]


def test_nothing_is_held_by_default():
    assert holds.held(DUNE_FILE) is None
    assert holds.current() == []
    assert not holds.full()


def _after(seconds: float) -> float:
    return time.time() + seconds
