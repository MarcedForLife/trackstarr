"""The rewrite records, and what does and does not take one away."""

import json
import os
from pathlib import Path

import pytest

from conftest import REWROTE
from trackstarr import config, rewrites, sweep_cache
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict, cache_key


@pytest.fixture
def media(tmp_path) -> str:
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x" * 10)
    return str(path)


def entry(path: str) -> dict:
    """The file as a sweep cache entry keys it, which is what a record is
    joined against."""
    found = os.stat(path)
    return {"size": found.st_size, "mtime_ns": found.st_mtime_ns}


def test_a_record_survives_the_rules_changing(media, tmp_path):
    """The whole point. A cache judged under other rules is dropped and every
    file re-probed; the record predates all of that and stays."""
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    cache_path = os.path.join(config.STATE_DIR, "sweep-cache.json")
    cache = SweepCache(cache_path, {"downmix": "2.0"})
    cache.record(media, cache_key(media, "eng"), Verdict(Status.CONFORM))
    cache.save()

    # What a rule change does: the cache loads empty and the sweep rebuilds it.
    rebuilt = SweepCache.load(cache_path, {"downmix": "5.1"})
    rebuilt.record(media, cache_key(media, "eng"), Verdict(Status.CONFORM))
    rebuilt.save()

    stored = sweep_cache.read(cache_path, {"downmix": "5.1"})
    assert rewrites.against(stored.files) == {media: REWROTE}


def test_a_verdict_reached_again_keeps_the_record(media):
    """A re-check re-probes a file we rewrote and finds it conforms, which is
    the right answer and says nothing about the rewrite."""
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    assert rewrites.against({media: entry(media)}) == {media: REWROTE}


def test_a_file_something_else_rewrote_loses_its_claim(media):
    """Whatever we made of the file, it is not what is on disk now."""
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    Path(media).write_bytes(b"x" * 20)

    assert rewrites.against({media: entry(media)}) == {}
    # Kept on disk, though: nothing here proves the file will not come back.
    assert media in rewrites.records()


def test_an_edit_of_ours_moves_the_record_onto_the_file(media):
    """mkvpropedit moves the size and mtime, so without this trackstarr's own
    tag edit would wipe trackstarr's own history."""
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    Path(media).write_bytes(b"x" * 20)
    rewrites.rekey(media, cache_key(media, "eng"))

    assert rewrites.against({media: entry(media)}) == {media: REWROTE}


def test_rekeying_a_file_with_no_record_invents_nothing(media):
    rewrites.rekey(media, cache_key(media, "eng"))
    assert rewrites.records() == {}


def test_a_record_with_no_key_is_not_booked(tmp_path):
    """Nothing could then tell the file from a later one."""
    rewrites.record(str(tmp_path / "gone.mkv"), None, REWROTE)
    assert rewrites.records() == {}


def test_a_remux_drops_the_record_of_the_name_it_published_from(media, tmp_path):
    mkv = str(tmp_path / "f.mkv")
    rewrites.record(mkv, cache_key(mkv, "eng"), REWROTE)
    rewrites.drop(mkv)
    assert rewrites.records() == {}


def test_a_deleted_file_is_collected_by_the_next_rewrite(media, tmp_path):
    """The only thing that ever collects them, and the store is only written
    by a rewrite."""
    gone = str(tmp_path / "gone.mkv")
    Path(gone).write_bytes(b"x")
    rewrites.record(gone, cache_key(gone, "eng"), REWROTE)
    os.remove(gone)

    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    assert list(rewrites.records()) == [media]


def test_a_library_that_is_not_mounted_is_not_a_library_of_deletions(media, tmp_path):
    """Every path under an unmounted /data reads as missing. Collecting on
    that would throw away the history of a whole library."""
    unmounted = str(tmp_path / "data" / "Film (2024)" / "film.mkv")
    rewrites.record(unmounted, FileKey(2_000, 1, 1, "eng"), REWROTE)

    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    assert sorted(rewrites.records()) == sorted([media, unmounted])


def test_a_record_missing_its_claim_is_dropped_on_the_way_in(media):
    """Hand-edited or half-written: without the size and mtime nothing could
    tell the file from a later one."""
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    path = rewrites.store_path()
    Path(path).write_text(json.dumps({media: {"made": REWROTE}, "": {"size": 1}}))
    rewrites.forget()

    assert rewrites.records() == {}


def test_a_record_round_trips(media):
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    rewrites.forget()
    (stored,) = rewrites.records().values()
    assert (stored.size, stored.made) == (10, REWROTE)


def test_an_unwritable_state_dir_loses_the_record_not_the_rewrite(media, monkeypatch):
    """A lost record is not worth failing the rewrite it describes."""
    monkeypatch.setattr(config, "STATE_DIR", "/proc/nowhere")
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    assert rewrites.records() == {}


def test_nothing_stored_is_no_join(media):
    assert rewrites.against({media: entry(media)}) == {}


def test_the_fingerprint_is_no_part_of_it(media):
    """It is history, not a verdict, so nothing about the rules reaches it."""
    rewrites.record(media, cache_key(media, "eng"), REWROTE)
    stored = json.loads(Path(rewrites.store_path()).read_text())
    assert set(stored[media]) == {"size", "mtime_ns", "made"}
    assert Policy.from_config().digest() not in json.dumps(stored)
