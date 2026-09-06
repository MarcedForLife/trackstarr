"""IMDb's ratings dataset: fetching it, keeping the rows we hold, and
serving them back. No network."""

import contextlib
import gzip
import io
import os
import time
import urllib.error

import pytest

from trackstarr import config, ratings, state

#: The dataset's own shape: a header, then tconst, average and vote count.
ROWS = [
    "tconst\taverageRating\tnumVotes",
    "tt0000001\t5.7\t2230",
    "tt15239678\t6.4\t712000",
    "tt7366338\t9.3\t900123",
    "tt9999999\t8.8\t14",
]


@pytest.fixture(autouse=True)
def _cold_table():
    """The table is memoised for the life of the process, and a leaked one
    would be another test's scores."""
    ratings.forget()
    ratings._retry_at = 0.0
    yield
    ratings.forget()
    ratings._retry_at = 0.0


def serving(asked: list[str], served: list[list[str]]):
    """A stand-in for client.stream that hands back the last rows in
    ``served``, gzipped the way IMDb publishes them."""

    @contextlib.contextmanager
    def fake_stream(url, timeout=30):
        asked.append(url)
        yield io.BytesIO(gzip.compress(("\n".join(served[-1]) + "\n").encode()))

    return fake_stream


@pytest.fixture
def dataset(monkeypatch):
    """The dataset as IMDb serves it, and the record of every fetch. Append
    to ``served`` to change what the next one answers."""
    asked: list[str] = []
    served = [ROWS]
    monkeypatch.setattr(ratings, "stream", serving(asked, served))
    monkeypatch.setattr(config, "IMDB_RATINGS", True)
    return asked, served


def test_only_the_ids_the_library_holds_are_kept(dataset):
    """The file is 1.7 million rows and a library is a few hundred of them,
    so the rest go past rather than into the store."""
    asked, _ = dataset

    kept = ratings.refresh({"tt15239678", "tt7366338"})

    assert kept == 2
    assert ratings.scores() == {"tt15239678": 6.4, "tt7366338": 9.3}
    assert asked == [ratings.DATASET_URL]


def test_a_score_is_read_back_by_id(dataset):
    ratings.refresh({"tt15239678"})

    assert ratings.of("tt15239678") == 6.4
    assert ratings.of("tt0000001") is None, "held by IMDb, not asked for here"
    assert ratings.of("") is None


def test_an_unreadable_row_costs_that_title_and_nothing_else(dataset):
    """The dataset is a foreign file; a row we cannot parse is one title
    without a score rather than a refresh that failed."""
    _, served = dataset
    served.append([ROWS[0], "tt15239678\t\t712000", "tt7366338\t9.3\t900123"])

    ratings.refresh({"tt15239678", "tt7366338"})

    assert ratings.scores() == {"tt7366338": 9.3}


def test_the_table_is_reread_only_when_the_file_moves(dataset, monkeypatch):
    """A shelf build asks for this and a library is hundreds of titles, so
    the parse is memoised on the file rather than repeated."""
    ratings.refresh({"tt15239678"})
    reads: list[str] = []
    original = state.read_json_records

    def counted(store):
        reads.append(store)
        return original(store)

    monkeypatch.setattr(state, "read_json_records", counted)
    ratings.scores()
    assert len(reads) == 1, "the first read after a fetch"

    assert ratings.scores() == {"tt15239678": 6.4}
    assert len(reads) == 1, "and no read at all after that"

    # A fetch replaces the file, and the next ask reads the new one.
    ratings.refresh({"tt7366338"})
    assert ratings.scores() == {"tt7366338": 9.3}


def test_a_damaged_store_reads_as_no_scores(tmp_path, monkeypatch):
    """Which is what every shelf looked like before the first fetch, so it
    costs the scores rather than the page."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    with open(ratings.path(), "w") as broken:
        broken.write("{not json")

    assert ratings.scores() == {}


def test_a_store_holding_something_other_than_scores_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    state.write_json(ratings.path(), {"scores": {"tt1": "eight", "tt2": True, "tt3": 7.1}})

    assert ratings.scores() == {"tt3": 7.1}

    state.write_json(ratings.path(), {"scores": []})
    ratings.forget()
    assert ratings.scores() == {}


def test_a_fetch_is_due_until_one_lands_and_then_not_for_a_day(dataset):
    assert ratings.due(), "nothing stored yet"

    ratings.refresh({"tt15239678"})

    assert not ratings.due()
    assert ratings.due(time.time() + ratings.REFRESH_EVERY + 1)


def test_nothing_is_fetched_with_the_setting_off(dataset, monkeypatch):
    """The one outbound request trackstarr makes to something nobody
    configured, so it has to be refusable."""
    monkeypatch.setattr(config, "IMDB_RATINGS", False)

    assert not ratings.due()
    assert not ratings.refresh_now({"tt15239678"})


def test_a_library_with_no_ids_is_a_pass_skipped(dataset):
    """None is an *arr that could not be listed; empty is a library with
    nothing in it. Neither is worth rebuilding a table from."""
    asked, _ = dataset

    assert not ratings.refresh_now(None)
    assert not ratings.refresh_now(set())
    assert asked == []


def test_a_fetch_that_fails_leaves_yesterdays_scores_alone(dataset, monkeypatch):
    """Yesterday's scores are the right answer for today; no scores at all
    is not."""
    ratings.refresh({"tt15239678"})

    def refuse(url, timeout=30):
        raise urllib.error.URLError("imdb is down")

    monkeypatch.setattr(ratings, "stream", refuse)

    assert not ratings.refresh_now({"tt15239678", "tt7366338"})
    assert ratings.scores() == {"tt15239678": 6.4}


def test_imdb_is_left_alone_for_an_hour_after_a_failure(dataset, monkeypatch):
    """The tick is five minutes and the scores are a day old either way, so
    an outage is not asked about twelve times an hour."""
    asked, served = dataset

    def refuse(url, timeout=30):
        raise urllib.error.URLError("imdb is down")

    monkeypatch.setattr(ratings, "stream", refuse)
    assert not ratings.refresh_now({"tt15239678"})
    assert ratings._retry_at > time.time()

    # IMDb is back, and is still not asked until the hour is up.
    monkeypatch.setattr(ratings, "stream", serving(asked, served))
    assert not ratings.refresh_now({"tt15239678"})
    assert asked == []

    ratings._retry_at = time.time() - 1
    assert ratings.refresh_now({"tt15239678"})
    assert ratings._retry_at == 0.0


def test_a_landed_fetch_says_so_to_whoever_memoised_the_old_table(dataset):
    """Asking for the ids is what builds the shelf, and the fetch replaces
    the table it was built from. Unsaid, every score arrives one memo behind."""
    told = []

    assert ratings.refresh_pass(lambda: {"tt15239678"}, lambda: told.append("stale"))
    assert told == ["stale"]

    assert not ratings.refresh_pass(lambda: {"tt15239678"}, told.clear), "not due again"
    assert told == ["stale"]


def test_the_shelf_is_not_built_for_a_fetch_that_is_not_due(dataset):
    """A switched-off install lists no library to answer with."""
    ratings.refresh({"tt15239678"})

    def unreachable() -> set[str]:
        raise AssertionError("the shelf was built for a fetch nobody was making")

    assert not ratings.refresh_pass(unreachable, lambda: None)


def test_the_store_lands_in_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))

    assert ratings.path() == os.path.join(str(tmp_path), "imdb-ratings.json")
    assert ratings.fetched_at() == 0.0, "nothing written yet"
