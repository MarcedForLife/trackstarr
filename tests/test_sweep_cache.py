"""The sweep verdict cache. Filesystem only, no media."""

import json
from pathlib import Path

import pytest

from trackstarr import __version__, config, policy
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict, cache_key

CONFORM = Verdict(Status.CONFORM)


def fingerprint() -> dict:
    """The current policy's fingerprint, as the sweep would compute it."""
    return Policy.from_config().fingerprint()


@pytest.fixture
def cache_path(tmp_path) -> str:
    return str(tmp_path / "sweep-cache.json")


@pytest.fixture
def media(tmp_path) -> str:
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x" * 10)
    return str(path)


def saved_cache(cache_path: str, *entries: tuple[str, FileKey, Verdict]) -> SweepCache:
    cache = SweepCache(cache_path, fingerprint())
    for path, key, verdict in entries:
        cache.record(path, key, verdict)
    cache.save()
    return SweepCache.load(cache_path, fingerprint())


def test_unchanged_file_hits(cache_path, media):
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    assert cache.lookup(media, key) == CONFORM


def test_would_fix_verdict_round_trips(cache_path, media):
    key = cache_key(media, "eng")
    verdict = Verdict(Status.WOULD_FIX, "add 2.0 downmix from stream 1 (6ch eng)")
    cache = saved_cache(cache_path, (media, key, verdict))
    assert cache.lookup(media, key) == verdict


def test_changed_file_misses(cache_path, media):
    cache = saved_cache(cache_path, (media, cache_key(media, "eng"), CONFORM))
    Path(media).write_bytes(b"y" * 20)
    assert cache.lookup(media, cache_key(media, "eng")) is None


def test_language_change_misses(cache_path, media):
    cache = saved_cache(cache_path, (media, cache_key(media, "eng"), CONFORM))
    assert cache.lookup(media, cache_key(media, "kor")) is None


def test_unknown_path_misses(cache_path):
    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.lookup("/nowhere.mkv", FileKey(1, 2, 1, None)) is None


def test_unreadable_file_is_never_cached(cache_path, tmp_path):
    missing = str(tmp_path / "missing.mkv")
    key = cache_key(missing, "eng")
    assert key is None

    cache = SweepCache(cache_path, fingerprint())
    cache.record(missing, key, CONFORM)
    assert cache.lookup(missing, key) is None


def test_config_change_drops_the_cache(cache_path, media, monkeypatch):
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))
    monkeypatch.setattr(config, "ALWAYS_KEEP", {"eng", "fre"})
    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) is None


def test_version_change_drops_the_cache(cache_path, media, monkeypatch):
    """Rule changes shipped in code must invalidate old verdicts too."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))
    monkeypatch.setattr(policy, "__version__", "0.0.0-test")
    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) is None


def test_corrupt_cache_is_ignored(cache_path):
    Path(cache_path).write_text("{not json")
    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.lookup("/x.mkv", FileKey(1, 2, 1, None)) is None


def test_unvisited_entries_are_pruned_on_save(cache_path, media, tmp_path):
    gone = tmp_path / "gone.mkv"
    gone.write_bytes(b"z" * 5)
    key = cache_key(media, "eng")
    gone_key = cache_key(str(gone), "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM), (str(gone), gone_key, CONFORM))

    # A sweep that only visits the surviving file, then persists.
    cache.record(media, key, CONFORM)
    cache.save()

    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) == CONFORM
    assert reloaded.lookup(str(gone), gone_key) is None


def test_checkpoint_keeps_unvisited_entries(cache_path, media, tmp_path):
    other = tmp_path / "other.mkv"
    other.write_bytes(b"z" * 5)
    key = cache_key(media, "eng")
    other_key = cache_key(str(other), "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM), (str(other), other_key, CONFORM))

    # Mid-sweep: only the first file visited so far.
    cache.record(media, key, CONFORM)
    cache.checkpoint()

    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(str(other), other_key) == CONFORM


def test_an_entry_with_a_status_we_no_longer_have_is_a_miss(cache_path, media):
    """The cache outlives upgrades. An entry naming a status this build does
    not know must re-probe, not crash the sweep that reads it."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))

    stored = json.loads(Path(cache_path).read_text())
    stored["files"][media]["status"] = "invented-status"
    Path(cache_path).write_text(json.dumps(stored))

    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) is None


def test_a_cache_that_cannot_be_written_does_not_fail_the_sweep(tmp_path, caplog, media):
    """The cache is an optimisation. Losing it costs a slow sweep next time,
    which is not worth failing a run that has already done its work."""
    cache = SweepCache(str(tmp_path / "no-such-dir" / "cache.json"), fingerprint())
    cache.record(media, cache_key(media, "eng"), CONFORM)
    cache.save()
    assert "could not write sweep cache" in caplog.text


def test_a_cache_whose_entries_are_the_wrong_shape_is_ignored(cache_path, media):
    """Valid JSON, wrong structure: a hand-edit or a half-written file. Every
    lookup must miss rather than the sweep failing on the first one."""
    Path(cache_path).write_text(
        json.dumps(
            {"config": fingerprint(), "version": __version__, "files": ["not", "a", "map"]}
        )
    )
    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.lookup(media, cache_key(media, "eng")) is None
