"""The sweep verdict cache. Filesystem only, no media."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import cache_verdict, publish_verdict, set_config, set_rules
from trackstarr import __version__, config, library, policy, sweep_cache, verdict_store
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict, cache_key, read
from trackstarr.verdict_store import FORMAT

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
        cache_verdict(cache, path, key, verdict)
    cache.save()
    return SweepCache.load(cache_path, fingerprint())


def test_unchanged_file_hits(cache_path, media):
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    assert cache.lookup(media, key) == CONFORM


def test_pending_verdict_round_trips(cache_path, media):
    key = cache_key(media, "eng")
    verdict = Verdict(Status.PENDING, "add 2.0 downmix from stream 1 (6ch eng)")
    cache = saved_cache(cache_path, (media, key, verdict))
    assert cache.lookup(media, key) == verdict


def test_tracks_are_stored_and_carried_without_decoding(cache_path, media):
    """The cache doubles as the library index, so what the probe saw has to
    survive the round trip. A hit's tracks are never read back into a
    Verdict; carry() moves the stored entry forward whole."""
    key = cache_key(media, "eng")
    tracks = [{"index": 0, "kind": "video", "codec": "h264"}]
    cache = saved_cache(cache_path, (media, key, Verdict(Status.CONFORM, "", tracks)))
    assert cache.lookup(media, key) == Verdict(Status.CONFORM)

    cache.carry(media)
    cache.save()
    stored = json.loads(Path(cache_path).read_text())
    assert stored["files"][media]["tracks"] == tracks


def test_carrying_a_path_the_cache_never_saw_is_a_no_op(cache_path, media):
    """A miss has nothing to bring forward, so carry() must leave the next
    cache empty rather than invent an entry for it."""
    cache = SweepCache.load(cache_path, fingerprint())
    cache.carry(media)
    cache.save()
    stored = json.loads(Path(cache_path).read_text())
    assert stored["files"] == {}


def test_a_checkpoint_with_nothing_fresh_writes_nothing(cache_path, media):
    """Carried entries are already on disk, and entries now hold track
    summaries, so a warm sweep must not rewrite the whole cache unchanged
    every interval."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    Path(cache_path).unlink()

    cache.carry(media)
    cache.checkpoint()
    assert not Path(cache_path).exists()

    cache_verdict(cache, media, key, CONFORM)
    cache.checkpoint()
    assert Path(cache_path).exists()
    # Written once; the next checkpoint has nothing fresh again.
    Path(cache_path).unlink()
    cache.checkpoint()
    assert not Path(cache_path).exists()


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
    cache_verdict(cache, missing, key, CONFORM)
    assert cache.lookup(missing, key) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda monkeypatch: set_config(LANGUAGES=("eng", "fre")),
        lambda monkeypatch: monkeypatch.setattr(policy, "__version__", "0.0.0-test"),
    ],
    ids=["a rule setting", "the package version"],
)
def test_a_changed_fingerprint_drops_the_cache(cache_path, media, monkeypatch, change):
    """Rule changes shipped in code have to invalidate old verdicts too."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))
    change(monkeypatch)
    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) is None


def test_corrupt_cache_is_ignored(cache_path):
    Path(cache_path).write_text("{not json")
    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.lookup("/x.mkv", FileKey(1, 2, 1, None)) is None


def _one_of_two_visited(cache_path, media, tmp_path):
    """A cache holding two files, of which a sweep has visited one. Yields the
    cache and the unvisited file's path and key, which is what the two persist
    paths differ about."""
    other = tmp_path / "other.mkv"
    other.write_bytes(b"z" * 5)
    key = cache_key(media, "eng")
    other_key = cache_key(str(other), "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM), (str(other), other_key, CONFORM))
    cache_verdict(cache, media, key, CONFORM)
    return cache, key, str(other), other_key


def test_save_prunes_entries_the_sweep_never_visited(cache_path, media, tmp_path):
    """A save ends a sweep, so a file it never saw has been moved or deleted."""
    cache, key, other, other_key = _one_of_two_visited(cache_path, media, tmp_path)
    cache.save()

    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) == CONFORM
    assert reloaded.lookup(other, other_key) is None


def test_save_within_folders_prunes_only_under_them(cache_path, media, tmp_path):
    """A walk of one folder has seen the whole of it and none of the rest, so
    the rest's unvisited entries are not departures."""
    folder = tmp_path / "title"
    folder.mkdir()
    gone = folder / "gone.mkv"
    gone.write_bytes(b"g" * 4)
    key, gone_key = cache_key(media, "eng"), cache_key(str(gone), "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM), (str(gone), gone_key, CONFORM))
    gone.unlink()

    cache.save(within=[str(folder)])

    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) == CONFORM
    assert reloaded.lookup(str(gone), gone_key) is None


def test_a_checkpoint_keeps_them(cache_path, media, tmp_path):
    """Mid-sweep, where the rest of the library is simply still ahead of the
    walk rather than gone."""
    cache, key, other, other_key = _one_of_two_visited(cache_path, media, tmp_path)
    cache.checkpoint()

    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) == CONFORM
    assert reloaded.lookup(other, other_key) == CONFORM


def test_a_rewritten_files_old_verdict_goes_at_the_next_write(cache_path, media):
    """A checkpoint used to carry it forward, so a rewritten file read Pending
    for the whole walk."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, Verdict(Status.PENDING)))

    cache_verdict(cache, media, None, None)
    cache.checkpoint()
    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) is None


def test_a_dropped_file_that_gets_a_verdict_after_all_keeps_it(cache_path, media):
    """A re-check judges the same file twice inside one run when somebody
    picks a title, runs it, and picks it again."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, Verdict(Status.PENDING)))

    cache_verdict(cache, media, None, None)
    cache_verdict(cache, media, key, CONFORM)
    cache.keep()
    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) == CONFORM


def test_an_entry_with_a_status_we_no_longer_have_is_a_miss(cache_path, media):
    """The cache outlives upgrades, so an entry naming an unknown status re-probes
    rather than crashing the sweep."""
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
    cache_verdict(cache, media, cache_key(media, "eng"), CONFORM)
    cache.save()
    assert "could not write sweep cache" in caplog.text


def test_a_cache_whose_entries_are_the_wrong_shape_is_ignored(cache_path, media):
    """Valid JSON, wrong structure: a hand-edit or a half-written file. Every
    lookup misses rather than the sweep failing."""
    Path(cache_path).write_text(
        json.dumps(
            {
                "format": FORMAT,
                "config": fingerprint(),
                "version": __version__,
                "files": ["not", "a", "map"],
            }
        )
    )
    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.lookup(media, cache_key(media, "eng")) is None


def test_the_format_is_stamped_on_what_is_written(cache_path, media):
    saved_cache(cache_path, (media, cache_key(media, "eng"), CONFORM))
    assert json.loads(Path(cache_path).read_text())["format"] == FORMAT


def test_an_older_format_is_dropped_rather_than_carried(cache_path, media):
    """carry() moves entries forward byte for byte, so an entry written before a
    field was added keeps its old shape for ever. Every one must miss."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))
    stored = json.loads(Path(cache_path).read_text())
    del stored["format"]
    Path(cache_path).write_text(json.dumps(stored))

    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) is None
    # And the library view reads nothing rather than entries whose shape is
    # another build's.
    assert read(cache_path, fingerprint()).files == {}


def test_an_older_format_outranks_a_matching_fingerprint(cache_path, media):
    """Same rules, older entries: the rules being unchanged says nothing about
    what fields an entry carries."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))
    stored = json.loads(Path(cache_path).read_text())
    stored["format"] = FORMAT - 1
    Path(cache_path).write_text(json.dumps(stored))

    assert SweepCache.load(cache_path, fingerprint()).lookup(media, key) is None


def test_an_entry_is_stamped_with_when_the_verdict_was_reached(cache_path, media, monkeypatch):
    monkeypatch.setattr(sweep_cache.time, "time", lambda: 1_700_000_000.5)
    saved_cache(cache_path, (media, cache_key(media, "eng"), CONFORM))
    stored = json.loads(Path(cache_path).read_text())
    assert stored["files"][media]["judged"] == 1_700_000_000


def test_a_carried_entry_keeps_the_stamp_it_arrived_with(cache_path, media, monkeypatch):
    """The library sorts on when the file was last looked at, not when the cache
    was written."""
    key = cache_key(media, "eng")
    monkeypatch.setattr(sweep_cache.time, "time", lambda: 1_700_000_000.0)
    cache = saved_cache(cache_path, (media, key, CONFORM))
    monkeypatch.setattr(sweep_cache.time, "time", lambda: 1_800_000_000.0)
    cache.carry(media)
    cache.save()
    stored = json.loads(Path(cache_path).read_text())
    assert stored["files"][media]["judged"] == 1_700_000_000


def test_a_failure_count_round_trips(cache_path, media):
    """The sweep's give-up bound is only as good as the count behind it, and
    the count is only ever read back from the cache."""
    key = cache_key(media, "eng")
    verdict = Verdict(Status.FAILED, "remux to mkv", failures=2)
    cache = saved_cache(cache_path, (media, key, verdict))

    assert cache.lookup(media, key).failures == 2
    assert cache.failures(media, key) == 2


def test_a_changed_file_starts_its_failures_over(cache_path, media):
    """The count is about one file in one state; an edit is a new question."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, Verdict(Status.FAILED, failures=3)))
    Path(media).write_bytes(b"x" * 20)

    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.failures(media, cache_key(media, "eng")) == 0


def test_an_unfailed_file_has_no_count(cache_path, media):
    """Nothing but a failure carries one, so the field stays off the entries
    that make up almost all of a settled library's cache."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, CONFORM))

    assert "failures" not in json.loads(Path(cache_path).read_text())["files"][media]
    assert SweepCache(cache_path, fingerprint()).failures(media, key) == 0


def test_a_damaged_failure_count_reads_as_none(cache_path, media):
    """A hand-edited entry must not make the bound fire early or never."""
    key = cache_key(media, "eng")
    saved_cache(cache_path, (media, key, Verdict(Status.FAILED, failures=2)))
    stored = json.loads(Path(cache_path).read_text())
    stored["files"][media]["failures"] = "lots"
    Path(cache_path).write_text(json.dumps(stored))

    cache = SweepCache.load(cache_path, fingerprint())
    assert cache.failures(media, key) == 0
    assert cache.lookup(media, key).failures == 0


# update(): booking one file into the cache without touching the rest, which
# is what a webhook delivery does. Everything above walks a library; a delivery
# has judged exactly one file.


@pytest.fixture
def state_cache(monkeypatch, tmp_path) -> Path:
    """The default cache used by standalone observation publication."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    return tmp_path / "sweep-cache.json"


def test_update_books_one_verdict_without_disturbing_the_others(state_cache, media, tmp_path):
    neighbour = str(tmp_path / "other.mkv")
    Path(neighbour).write_bytes(b"y" * 10)
    saved_cache(str(state_cache), (neighbour, cache_key(neighbour, "eng"), CONFORM))

    verdict = Verdict(Status.PENDING, "drop subtitle 3 (spa, Spanish)")
    publish_verdict(media, cache_key(media, "eng"), verdict, fingerprint())

    stored = read(str(state_cache), fingerprint())
    assert stored.files[media]["status"] == "pending"
    assert stored.files[media]["reasons"] == "drop subtitle 3 (spa, Spanish)"
    # The whole point: the rest of the library is still there.
    assert stored.files[neighbour]["status"] == "conform"


def test_update_writes_the_same_entry_a_sweep_would(state_cache, media):
    """A file judged by a delivery and the same file judged by a sweep have to
    land identically, or the library would show two answers for one file
    depending on which looked at it last."""
    key = cache_key(media, "eng")
    verdict = Verdict(
        Status.PENDING,
        "drop subtitle 3 (spa)",
        [{"index": 0, "kind": "video"}],
        [{"index": 0, "kind": "video", "src": 0}],
        {"reasons": ["drop subtitle 3 (spa)"], "rules": ["languages"]},
    )
    publish_verdict(media, key, verdict, fingerprint())
    sweep_cache.flush()
    from_delivery = json.loads(state_cache.read_text())["files"][media]

    saved_cache(str(state_cache), (media, key, verdict))
    assert json.loads(state_cache.read_text())["files"][media] == from_delivery


def test_update_creates_the_cache_when_nothing_has_swept(state_cache, media):
    """One verdict is still a verdict. The library shows it against its
    title and everything else as unchecked, which is what is known."""
    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())
    assert read(str(state_cache), fingerprint()).files[media]["status"] == "conform"


def test_update_with_no_verdict_drops_the_stored_entry(state_cache, media):
    """What a rewritten file leaves behind: the old entry describes a file
    that no longer exists at that size and mtime."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    publish_verdict(media, None, None, fingerprint())
    assert read(str(state_cache), fingerprint()).files == {}


def test_update_leaves_a_cache_judged_under_other_rules_alone(state_cache, media):
    """Those verdicts are already flagged as not current, and `current` is one
    flag for the whole file: there is no honest way to file a fresh verdict
    among them. The next sweep drops the lot."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    before = state_cache.read_text()

    stale = fingerprint() | {"languages": ["fre:add"]}
    publish_verdict(media, cache_key(media, "eng"), Verdict(Status.PENDING), stale)
    assert state_cache.read_text() == before


def test_update_refuses_a_verdict_it_cannot_key(state_cache, tmp_path):
    """An unreadable file has nothing to key an entry by, so nothing could
    later tell whether it had changed. record() refuses these too."""
    gone = str(tmp_path / "vanished.mkv")
    publish_verdict(gone, cache_key(gone, "eng"), CONFORM, fingerprint())
    assert not state_cache.exists()


def test_update_dropping_nothing_does_not_rewrite_the_cache(state_cache, media, tmp_path):
    """A library's worth of JSON is not rewritten to say an absent entry is
    still absent."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    before = state_cache.stat().st_mtime_ns
    publish_verdict(str(tmp_path / "never-seen.mkv"), None, None, fingerprint())
    assert state_cache.stat().st_mtime_ns == before


def test_update_survives_a_cache_it_cannot_read(state_cache, media, caplog):
    state_cache.write_text("{not json")
    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())
    assert state_cache.read_text() == "{not json"
    assert "ignoring unreadable verdict store" in caplog.text


def test_update_leaves_a_cache_whose_files_are_not_an_object_alone(state_cache, media):
    """Parseable JSON in the right build's shape, with the wrong thing where
    the library goes. There is nowhere to file a verdict, and the next sweep
    replaces the file whole."""
    damaged = json.dumps({"format": FORMAT, "config": fingerprint(), "files": ["a list"]})
    state_cache.write_text(damaged)

    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())
    assert state_cache.read_text() == damaged


def test_update_survives_a_write_it_cannot_make(state_cache, media, monkeypatch, caplog):
    """A full volume costs the window's verdicts until the next sweep looks at
    those files, not the delivery that was in the middle of booking one. The
    batch goes with the refusal: retrying a full volume every window would fill
    the log and change nothing."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))

    def full(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(verdict_store, "write_json", full)
    publish_verdict(media, cache_key(media, "eng"), Verdict(Status.PENDING), fingerprint())
    sweep_cache.flush()

    assert read(str(state_cache), fingerprint()).files[media]["status"] == "conform"
    assert "could not write the sweep cache" in caplog.text


def published(cache_file: str, path: str, fingerprint: dict) -> None:
    """One delivery's verdict into a named store, outside any walk."""
    with sweep_cache.observing(path, cache_file) as observation:
        sweep_cache.publish(
            observation, path, path, cache_key(path, "eng"), CONFORM, fingerprint
        )


def test_a_burst_of_deliveries_rewrites_the_library_once(state_cache, tmp_path, monkeypatch):
    """What the window is for. A season pack arrives with no rewrite between
    its files, and a rewrite of the whole library for each one is the cost the
    batch removes."""
    parses, writes = [], []
    parse, write = verdict_store.load, verdict_store.write
    monkeypatch.setattr(verdict_store, "load", lambda path: parses.append(path) or parse(path))
    monkeypatch.setattr(
        verdict_store,
        "write",
        lambda path, mark, entries: writes.append(path) or write(path, mark, entries),
    )

    episodes = []
    for index in range(5):
        episode = tmp_path / f"Show S01E0{index}.mkv"
        episode.write_bytes(b"x" * 10)
        episodes.append(str(episode))
        publish_verdict(episodes[-1], cache_key(episodes[-1], "eng"), CONFORM, fingerprint())
    assert writes == []

    sweep_cache.flush()
    assert len(parses) == 1
    assert writes == [str(state_cache)]
    assert sorted(read(str(state_cache), fingerprint()).files) == sorted(episodes)


def test_an_unwritten_verdict_is_what_the_library_reads(state_cache, media):
    """The window is invisible in this process: a page asking between the
    delivery and the write is shown the new verdict, not the file's old one."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    publish_verdict(media, cache_key(media, "eng"), Verdict(Status.PENDING), fingerprint())

    assert read(str(state_cache), fingerprint()).files[media]["status"] == "pending"
    assert json.loads(state_cache.read_text())["files"][media]["status"] == "conform"


def test_the_window_closing_writes_without_anyone_asking(state_cache, media):
    """Nothing calls flush() between one delivery and the next, so the batch
    has to land on its own and the thread has to retire once it has."""
    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())
    flusher = sweep_cache._flusher
    assert flusher is not None

    flusher.join(30)
    assert not flusher.is_alive()
    assert json.loads(state_cache.read_text())["files"][media]["status"] == "conform"


def test_the_flusher_writes_what_is_due_and_leaves_the_rest(tmp_path, monkeypatch):
    """Two stores whose windows close far apart. A pass woken for one does not
    take the other's window away from it."""
    due, waiting = str(tmp_path / "due.json"), str(tmp_path / "waiting.json")
    for name in ("a.mkv", "b.mkv"):
        (tmp_path / name).write_bytes(b"x" * 10)

    monkeypatch.setattr(sweep_cache, "_COALESCE_SECONDS", 0.0)
    published(due, str(tmp_path / "a.mkv"), fingerprint())
    monkeypatch.setattr(sweep_cache, "_COALESCE_SECONDS", 30.0)
    monkeypatch.setattr(sweep_cache, "_COALESCE_LIMIT", 30.0)
    published(waiting, str(tmp_path / "b.mkv"), fingerprint())

    sweep_cache._write_batches(due_only=True)
    assert Path(due).exists()
    assert not Path(waiting).exists()


def test_a_walk_starting_mid_window_takes_the_unwritten_verdicts(state_cache, tmp_path):
    """Otherwise its first save would prune a file booked while nothing was
    sweeping, because the file it loaded had never heard of it."""
    episode = tmp_path / "booked.mkv"
    episode.write_bytes(b"x" * 10)
    publish_verdict(str(episode), cache_key(str(episode), "eng"), CONFORM, fingerprint())

    walk = SweepCache.load(str(state_cache), fingerprint())
    assert str(episode) in walk._previous


def test_a_walk_under_new_rules_drops_an_unwritten_batch(state_cache, media, caplog):
    """Same answer as for a file judged under rules that have since moved: the
    verdicts go, and the walk that noticed says why."""
    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())

    with caplog.at_level("INFO"):
        walk = SweepCache.load(str(state_cache), fingerprint() | {"languages": ["fre:add"]})
    assert walk._previous == {}
    assert "rule configuration changed" in caplog.text


def test_a_checkpoint_takes_the_batch_with_it(state_cache, media):
    """The walk carries every publication already, so once it has written, the
    batch has nothing left to add and must not overwrite it later."""
    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())
    SweepCache.load(str(state_cache), fingerprint()).keep()

    assert sweep_cache._pending == {}
    assert json.loads(state_cache.read_text())["files"][media]["status"] == "conform"


def test_a_rule_change_drops_what_the_old_rules_left_unwritten(state_cache, tmp_path):
    """A load would refuse those verdicts anyway, so the batch goes rather than
    being merged into one the new rules would keep."""
    old = tmp_path / "old.mkv"
    new = tmp_path / "new.mkv"
    for episode in (old, new):
        episode.write_bytes(b"x" * 10)
    publish_verdict(str(old), cache_key(str(old), "eng"), CONFORM, fingerprint())

    set_rules(remux="always")
    publish_verdict(str(new), cache_key(str(new), "eng"), CONFORM, fingerprint())
    sweep_cache.flush()

    assert list(json.loads(state_cache.read_text())["files"]) == [str(new)]


def test_clear_takes_the_unwritten_verdicts_with_it(state_cache, media):
    """They are part of what the library was showing, so they are part of what
    was cleared, and none of them may land afterwards."""
    publish_verdict(media, cache_key(media, "eng"), CONFORM, fingerprint())

    assert sweep_cache.clear() == 1
    sweep_cache.flush()
    assert not state_cache.exists()


def test_the_offered_view_is_what_a_checkpoint_would_write(cache_path, media, tmp_path):
    """One expression behind both, so what a reader is shown mid-walk and what
    the file says a moment later cannot disagree."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    fresh = str(tmp_path / "g.mkv")
    Path(fresh).write_bytes(b"fresh")
    cache_verdict(cache, fresh, cache_key(fresh, "eng"), Verdict(Status.PENDING, "add 2.0"))
    cache_verdict(cache, media, None, None)

    assert cache.publish_view() is True
    offered = cache._snapshot[1]

    cache.keep()
    with open(cache_path) as cache_file:
        assert offered == json.load(cache_file)["files"]


def test_a_warm_sweep_has_nothing_to_offer(cache_path, media):
    """carry() brings an unchanged entry forward byte for byte, so a settled
    library's sweep neither rebuilds a view nor gives anyone a reason to look."""
    cache = saved_cache(cache_path, (media, cache_key(media, "eng"), CONFORM))
    cache.carry(media)
    assert cache.publish_view() is False
    assert cache._snapshot is None


def test_a_verdict_and_a_drop_each_earn_a_new_view(cache_path, media):
    """The two things that change what a reader would see. A second ask with
    nothing between them is the same view, and says so."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    cache_verdict(cache, media, key, Verdict(Status.PENDING, "add 2.0"))
    assert cache.publish_view() is True
    assert cache.publish_view() is False
    cache_verdict(cache, media, None, None)
    assert cache.publish_view() is True


def test_view_generations_climb_across_walks(cache_path, media):
    """A reader keys a memo on the file's stamp and the generation together,
    so a number a later walk reused would serve an earlier walk's verdicts
    under an unmoved file."""
    key = cache_key(media, "eng")
    first = SweepCache(cache_path, fingerprint())
    cache_verdict(first, media, key, CONFORM)
    first.publish_view()
    second = SweepCache(cache_path, fingerprint())
    cache_verdict(second, media, key, CONFORM)
    second.publish_view()
    assert second._snapshot[0] > first._snapshot[0]


def test_a_walk_lends_its_view_for_as_long_as_it_runs(cache_path, media):
    cache = SweepCache(cache_path, fingerprint())
    cache_verdict(cache, media, cache_key(media, "eng"), CONFORM)
    cache.publish_view()
    assert sweep_cache.live_view(fingerprint()) is None
    with sweep_cache.live(cache):
        assert sweep_cache.live_view(fingerprint())[1].keys() == {media}
    assert sweep_cache.live_view(fingerprint()) is None


def test_a_walk_that_ends_badly_still_lets_go(cache_path):
    """Nothing was written, so readers are back on the last checkpoint, which
    is what a crashed sweep has always left behind."""
    cache = SweepCache(cache_path, fingerprint())
    with pytest.raises(RuntimeError), sweep_cache.live(cache):
        raise RuntimeError("the walk fell over")
    assert sweep_cache.live_view(fingerprint()) is None


def test_a_walk_with_nothing_to_offer_yet_reads_as_no_view(cache_path):
    """The first files of a cold walk. The file is the better answer until
    there is something to replace it with."""
    with sweep_cache.live(SweepCache(cache_path, fingerprint())):
        assert sweep_cache.live_view(fingerprint()) is None


def test_a_view_is_refused_to_rules_it_was_not_judged_under(cache_path, media):
    """A settings save mid-walk. The file and its own `current` flag are the
    honest answer, and this is the one place that comparison is made."""
    cache = SweepCache(cache_path, fingerprint())
    cache_verdict(cache, media, cache_key(media, "eng"), CONFORM)
    cache.publish_view()
    with sweep_cache.live(cache):
        assert sweep_cache.live_view(fingerprint() | {"languages": ["fre:add"]}) is None


def test_concurrent_walks_preserve_changes_and_restore_the_remaining_view(cache_path, media):
    key = cache_key(media, "eng")
    other = str(Path(media).with_name("another.mkv"))
    Path(other).write_bytes(b"other")
    first = SweepCache(cache_path, fingerprint())
    second = SweepCache(cache_path, fingerprint())
    with sweep_cache.live(first):
        cache_verdict(first, media, key, Verdict(Status.PENDING))
        first.publish_view()
        with sweep_cache.live(second):
            cache_verdict(second, media, key, CONFORM)
            cache_verdict(second, other, cache_key(other, "eng"), CONFORM)
            second.publish_view()
            second.keep()
            assert sweep_cache.live_view(fingerprint())[1][media]["status"] == "conform"
        first.carry(media)
        first.save()
        stored = read(cache_path, fingerprint()).files
        assert stored[media]["status"] == "conform"
        assert other in stored
        assert sweep_cache.live_view(fingerprint())[1][media]["status"] == "conform"
    assert sweep_cache.live_view(fingerprint()) is None


def test_concurrent_drop_is_not_resurrected_by_a_sweep(cache_path, media):
    key = cache_key(media, "eng")
    first = SweepCache(cache_path, fingerprint())
    with sweep_cache.live(first):
        cache_verdict(first, media, key, CONFORM)
        second = SweepCache(cache_path, fingerprint())
        with sweep_cache.live(second):
            cache_verdict(second, media, None, None)
            second.keep()
        first.save()
        assert media not in read(cache_path, fingerprint()).files


def test_a_joining_walk_does_not_restore_an_uncheckpointed_drop(cache_path, media):
    first = SweepCache(cache_path, fingerprint())
    cache_verdict(first, media, cache_key(media, "eng"), CONFORM)
    first.keep()
    with sweep_cache.live(first):
        cache_verdict(first, media, None, None)
        second = SweepCache.load(cache_path, fingerprint())
        with sweep_cache.live(second):
            second.keep()
            assert media not in read(cache_path, fingerprint()).files


def test_a_later_title_checkpoint_preserves_full_sweep_pruning(cache_path, media):
    first = SweepCache(cache_path, fingerprint())
    cache_verdict(first, media, cache_key(media, "eng"), CONFORM)
    first.keep()
    first = SweepCache.load(cache_path, fingerprint())
    second = SweepCache.load(cache_path, fingerprint())
    with sweep_cache.live(first), sweep_cache.live(second):
        first.save()
        second.keep()
        assert media not in read(cache_path, fingerprint()).files


def test_a_joining_walk_does_not_inherit_another_policy(cache_path, media):
    first = SweepCache(cache_path, fingerprint())
    cache_verdict(first, media, cache_key(media, "eng"), CONFORM)
    second = SweepCache(cache_path, fingerprint() | {"languages": ["fre:add"]})
    with sweep_cache.live(first), sweep_cache.live(second):
        assert second.lookup(media, cache_key(media, "eng")) is None


def test_observations_reject_same_key_mutations_and_release_revisions(state_cache, media):
    """The file key alone cannot detect a newer accepted verdict or invalidation."""

    key = cache_key(media, "eng")
    cache = SweepCache(str(state_cache), fingerprint())
    with sweep_cache.live(cache), sweep_cache.observing(media) as old:
        old.watch(media)  # Watching a same-path rewrite does not retain a second reference.
        with ThreadPoolExecutor() as workers:
            workers.submit(cache_verdict, cache, media, key, CONFORM).result(timeout=3)
        # The caller passes its original observation even after a newer verdict.
        assert not sweep_cache.publish(
            old, media, media, key, Verdict(Status.PENDING), fingerprint(), cache
        )
        assert not sweep_cache.publish(old, media, media, None, None, fingerprint(), cache)
        assert not sweep_cache.publish(
            old, media, media, key, Verdict(Status.PENDING), fingerprint()
        )
        assert cache._standing()[media]["status"] == "conform"
        assert len(sweep_cache._revisions) == 1
    assert not sweep_cache._revisions
    assert not sweep_cache.publish(old, media, media, key, CONFORM, fingerprint(), cache)


def test_invalidation_fences_a_probe_even_when_no_record_exists(state_cache, media):

    cache = SweepCache(str(state_cache), fingerprint())
    with sweep_cache.observing(media) as old:
        key = cache_key(media, "eng")
        with ThreadPoolExecutor() as workers:
            workers.submit(cache_verdict, cache, media, None, None).result(timeout=3)
        assert not sweep_cache.publish(old, media, media, key, CONFORM, fingerprint(), cache)
    assert not cache._standing()
    assert not sweep_cache._revisions


def test_a_pruned_observation_cannot_restore_a_partial_walk(state_cache, media):

    key = cache_key(media, "eng")
    saved_cache(str(state_cache), (media, key, CONFORM))
    partial = SweepCache.load(str(state_cache), fingerprint())
    full = SweepCache.load(str(state_cache), fingerprint())
    with sweep_cache.live(partial), sweep_cache.live(full), sweep_cache.observing(media) as old:
        with ThreadPoolExecutor() as workers:
            workers.submit(full.save).result(timeout=3)
        assert not sweep_cache.publish(old, media, media, key, CONFORM, fingerprint(), partial)
        partial.carry(media)
        partial.keep()
        assert read(str(state_cache), fingerprint()).files == {}


@pytest.mark.parametrize("changed", ["missing", "replaced", "revision", "unwatched"])
def test_rejected_replacements_leave_both_records_intact(state_cache, media, changed):

    output = str(Path(media).with_suffix(".mp4"))
    Path(output).write_bytes(b"output")
    key = cache_key(output, "eng")
    cache = saved_cache(
        str(state_cache), (media, cache_key(media, "eng"), CONFORM), (output, key, CONFORM)
    )
    before = cache._standing()
    with sweep_cache.observing(media) as observation:
        if changed != "unwatched":
            observation.watch(output)
        if changed == "missing":
            Path(output).unlink()
        elif changed == "replaced":
            Path(output).write_bytes(b"a different output")
        elif changed == "revision":
            with ThreadPoolExecutor() as workers:
                workers.submit(cache_verdict, cache, output, key, CONFORM).result(timeout=3)
        assert not sweep_cache.publish(
            observation, media, output, key, Verdict(Status.PENDING), fingerprint(), cache
        )
    assert cache._standing() == before
    assert not sweep_cache._revisions


def test_replacement_is_one_reader_generation(state_cache, media, monkeypatch):

    output = str(Path(media).with_suffix(".mp4"))
    Path(output).write_bytes(b"output")
    cache = saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    installed, release, reading = threading.Event(), threading.Event(), threading.Event()
    install = cache._install

    def paused_install(path, entry):
        install(path, entry)
        installed.set()
        assert release.wait(3)

    monkeypatch.setattr(cache, "_install", paused_install)

    def replace():
        with sweep_cache.observing(media) as observation:
            observation.watch(output)
            return sweep_cache.publish(
                observation,
                media,
                output,
                cache_key(output, "eng"),
                CONFORM,
                fingerprint(),
                cache,
            )

    def snapshot():
        reading.set()
        cache.publish_view()
        return cache._snapshot

    with ThreadPoolExecutor() as workers:
        replacement = workers.submit(replace)
        try:
            assert installed.wait(3)
            reader = workers.submit(snapshot)
            assert reading.wait(3)
            assert not reader.done()
        finally:
            release.set()
        assert replacement.result(timeout=3)
        generation, files = reader.result(timeout=3)
        assert set(files) == {output}
        assert cache.publish_view() is False
        assert cache._snapshot[0] == generation


def test_outside_publication_checkpoints_live_additions_and_drops(state_cache, media):
    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))
    other = str(Path(media).with_name("other.mkv"))
    Path(other).write_bytes(b"other")
    with sweep_cache.live(cache):
        cache_verdict(cache, media, None, None)
        cache_verdict(cache, other, cache_key(other, "eng"), CONFORM)
        publish_verdict("absent", None, None, fingerprint())
        assert set(read(str(state_cache), fingerprint()).files) == {other}
        cache.keep()
        assert set(read(str(state_cache), fingerprint()).files) == {other}


def test_observation_policy_and_cache_identity_are_checked(state_cache, media, tmp_path):
    key = cache_key(media, "eng")
    cache = SweepCache(str(state_cache), fingerprint())
    with sweep_cache.observing(media) as observation:
        different = SweepCache(str(tmp_path / "different.json"), fingerprint())
        assert not sweep_cache.publish(
            observation, media, media, key, CONFORM, fingerprint(), different
        )
        cache.fingerprint = fingerprint() | {"other": True}
        assert not sweep_cache.publish(
            observation, media, media, key, CONFORM, fingerprint(), cache
        )
        assert not sweep_cache.publish(
            observation, media, media, key, CONFORM, cache.fingerprint
        )


def test_policy_change_cannot_be_undone_by_an_older_walk(state_cache, media):
    key = cache_key(media, "eng")
    old_policy = fingerprint()
    old = SweepCache(str(state_cache), old_policy)
    with sweep_cache.live(old):
        cache_verdict(old, media, key, CONFORM)
        old.publish_view()
        set_config(LANGUAGES=("fre",))
        current = SweepCache(str(state_cache), fingerprint())
        with sweep_cache.live(current):
            cache_verdict(current, media, key, Verdict(Status.PENDING))
            current.keep()
            cache_verdict(old, media, key, CONFORM)
            old.save()
            old.keep()
            assert sweep_cache.live_view(old_policy) is None
            assert read(str(state_cache), fingerprint()).files[media]["status"] == "pending"
            late = SweepCache(str(state_cache), old_policy)
            with sweep_cache.live(late):
                assert late._superseded
                assert not current._superseded
        old.save()
        assert read(str(state_cache), fingerprint()).current


def test_old_disk_policy_is_not_mixed_with_a_current_outside_result(state_cache, media):
    key = cache_key(media, "eng")
    saved_cache(str(state_cache), (media, key, CONFORM))
    before = state_cache.read_bytes()
    set_config(LANGUAGES=("fre",))
    publish_verdict(media, key, CONFORM, fingerprint())
    assert state_cache.read_bytes() == before


def test_cached_hit_never_restores_a_local_drop_or_older_entry(cache_path, media):
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    cache_verdict(cache, media, key, Verdict(Status.PENDING))
    cache.carry(media)
    assert cache._standing()[media]["status"] == "pending"
    cache_verdict(cache, media, None, None)
    cache.carry(media)
    cache.keep()
    assert read(cache_path, fingerprint()).files == {}


def test_clear_fences_unpublished_results_and_cannot_be_undone_by_checkpoints(
    state_cache, media
):

    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))
    unrelated = SweepCache(str(state_cache.with_name("unrelated.json")), fingerprint())
    with (
        sweep_cache.live(cache),
        sweep_cache.live(unrelated),
        sweep_cache.observing(media) as old,
    ):
        with sweep_cache.observing("unpublished") as unpublished:
            with sweep_cache.observing(media, unrelated.path), ThreadPoolExecutor() as workers:
                assert workers.submit(library.clear).result(timeout=3) == 1
            assert not sweep_cache.publish(
                unpublished, "unpublished", "unpublished", None, None, fingerprint(), cache
            )
        assert not sweep_cache.publish(old, media, media, key, CONFORM, fingerprint(), cache)
        cache.carry(media)
        cache.keep()
        assert read(str(state_cache), fingerprint()).files == {}
        assert sweep_cache.live_view(fingerprint())[1] == {}
    assert not sweep_cache._revisions


def test_clear_write_failure_preserves_accepted_state(state_cache, media, monkeypatch):
    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))

    def refused(path):
        raise PermissionError("read only")

    with sweep_cache.live(cache), sweep_cache.observing(media) as observation:
        monkeypatch.setattr(sweep_cache.os, "remove", refused)
        with pytest.raises(PermissionError):
            sweep_cache.clear()
        assert observation.accepts({media})
        assert cache.lookup(media, key) == CONFORM


def test_outside_publication_does_not_merge_an_unrelated_live_cache(state_cache, media):
    unrelated = SweepCache(str(state_cache.with_name("another-cache.json")), fingerprint())
    key = cache_key(media, "eng")
    cache_verdict(unrelated, media, key, Verdict(Status.PENDING, "other cache"))
    with sweep_cache.live(unrelated):
        publish_verdict(media, key, CONFORM, fingerprint())
        assert unrelated._standing()[media]["status"] == "pending"
    assert read(str(state_cache), fingerprint()).files[media]["status"] == "conform"


def _header_edit(path: str) -> None:
    """Change the bytes and put size and mtime back, as mkvpropedit can."""
    before = os.stat(path)
    Path(path).write_bytes(b"y" * before.st_size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_an_edit_fences_the_probe_that_read_the_file_before_it(state_cache, media):
    """Size and mtime say nothing about a header edit, so the probe that read
    the file first has to be turned away by the edit rather than by its key."""
    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))
    with sweep_cache.live(cache), sweep_cache.observing(media) as older:
        with sweep_cache.observing(media) as editor:
            assert editor.changing(media)
            editor.changed()
            _header_edit(media)
            assert cache_key(media, "eng") == key
            assert not sweep_cache.publish(
                older, media, media, key, CONFORM, fingerprint(), cache
            )
            assert sweep_cache.publish(
                editor, media, media, key, Verdict(Status.PENDING), fingerprint(), cache
            )
        assert cache._standing()[media]["status"] == "pending"
    assert not sweep_cache._revisions


def test_a_probe_from_before_an_edit_cannot_publish_after_it_either(state_cache, media):
    """The other order: the edit books its verdict first and the older probe
    arrives behind it."""
    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))
    with sweep_cache.live(cache), sweep_cache.observing(media) as older:
        with sweep_cache.observing(media) as editor:
            assert editor.changing(media)
            editor.changed()
            _header_edit(media)
            assert sweep_cache.publish(
                editor, media, media, key, Verdict(Status.PENDING), fingerprint(), cache
            )
        assert not sweep_cache.publish(older, media, media, key, CONFORM, fingerprint(), cache)
        assert cache._standing()[media]["status"] == "pending"
    assert not sweep_cache._revisions


def test_a_read_begun_during_an_edit_waits_for_the_file(state_cache, media):
    """A probe started mid-edit describes a file that is already gone by the
    time it has a verdict to offer, so it starts after the edit instead."""
    watched = threading.Event()

    def read_it() -> bool:
        with sweep_cache.observing(media) as reader:
            watched.set()
            return reader.accepts({media})

    with ThreadPoolExecutor() as workers:
        with sweep_cache.observing(media) as editor:
            assert editor.changing(media)
            reading = workers.submit(read_it)
            assert not watched.wait(0.2)
        # Started after the edit let go, so its own verdict stands.
        assert reading.result(timeout=3)
    assert not sweep_cache._revisions


def test_two_edits_of_one_file_serialize_and_two_files_do_not(state_cache, media, tmp_path):
    other = str(tmp_path / "g.mkv")
    Path(other).write_bytes(b"x" * 10)

    def take(path: str, done: threading.Event) -> bool:
        with sweep_cache.observing(path) as second:
            taken = second.changing(path)
            done.set()
            return taken

    queueing, elsewhere = threading.Event(), threading.Event()
    with ThreadPoolExecutor() as workers:
        with sweep_cache.observing(media) as editor:
            assert editor.changing(media)
            queued = workers.submit(take, media, queueing)
            assert not queueing.wait(0.2)
            assert workers.submit(take, other, elsewhere).result(timeout=3)
        assert queued.result(timeout=3)
    assert not sweep_cache._revisions


def test_a_remux_owns_both_the_files_it_touches(state_cache, media, tmp_path):
    """Source and output are taken together and neither is held while waiting
    for the other, so a rewrite going the other way cannot deadlock on it."""
    output = str(tmp_path / "f-remuxed.mkv")
    took = threading.Event()

    def opposite() -> bool:
        with sweep_cache.observing(output) as second:
            taken = second.changing(output, media)
            took.set()
            return taken

    with ThreadPoolExecutor() as workers:
        with sweep_cache.observing(media) as rewrite:
            assert rewrite.changing(media, output)
            queued = workers.submit(opposite)
            assert not took.wait(0.2)
        assert queued.result(timeout=3)
    assert not sweep_cache._revisions


def test_a_skip_ends_the_wait_for_another_edit(state_cache, media):
    """Waiting out somebody else's edit stays cancellable: a file taken off its
    run settles now rather than after the edit it was queued behind."""
    skipped = threading.Event()
    with ThreadPoolExecutor() as workers, sweep_cache.observing(media) as waiting:
        with sweep_cache.observing(media) as holder:
            assert holder.changing(media)
            queued = workers.submit(waiting.changing, media, stopped=skipped.is_set)
            assert not queued.done()
            skipped.set()
            assert queued.result(timeout=3) is False
        assert waiting.changes is None
    assert not sweep_cache._revisions


def test_an_edit_that_published_no_verdict_takes_the_stored_one_with_it(state_cache, media):
    """An entry describing the file as it was before a half-written edit is
    worse than no entry at all."""
    cache = saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    with sweep_cache.live(cache):
        with sweep_cache.observing(media) as editor:
            assert editor.changing(media)
            editor.changed()
        assert media not in cache._standing()
    assert read(str(state_cache), fingerprint()).files == {}
    assert not sweep_cache._revisions


def test_an_edit_nothing_wrote_leaves_the_stored_verdict_alone(state_cache, media):
    """A refusal is not a change: the file is as the verdict found it."""
    cache = saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    with sweep_cache.live(cache), sweep_cache.observing(media) as refused:
        assert refused.changing(media)
    assert cache._standing()[media]["status"] == "conform"
    assert not sweep_cache._revisions


def test_a_clear_during_an_edit_leaves_nothing_to_publish_over_it(state_cache, media):
    """Clearing revokes what the edit was going to publish, and its
    invalidation on the way out cannot put an entry back."""
    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))
    with sweep_cache.live(cache):
        with sweep_cache.observing(media) as editor:
            assert editor.changing(media)
            editor.changed()
            with ThreadPoolExecutor() as workers:
                assert workers.submit(sweep_cache.clear).result(timeout=3) == 1
            assert not sweep_cache.publish(
                editor, media, media, key, CONFORM, fingerprint(), cache
            )
        assert cache._standing() == {}
    assert read(str(state_cache), fingerprint()).files == {}
    assert not sweep_cache._revisions


@pytest.mark.parametrize("operation", ["publish", "keep", "save", "clear", "live", "close"])
def test_cache_filesystem_operations_leave_the_state_mutex_available(
    state_cache, media, monkeypatch, operation
):
    key = cache_key(media, "eng")
    cache = saved_cache(str(state_cache), (media, key, CONFORM))
    checked = []

    def wrap(call):
        def io(*args, **kwargs):
            def acquire():
                acquired = sweep_cache._update_lock.acquire(blocking=False)
                if acquired:
                    sweep_cache._update_lock.release()
                return acquired

            with ThreadPoolExecutor() as pool:
                assert pool.submit(acquire).result(timeout=3)
            checked.append(True)
            return call(*args, **kwargs)

        return io

    monkeypatch.setattr(verdict_store, "write_json", wrap(verdict_store.write_json))
    monkeypatch.setattr(verdict_store, "load", wrap(verdict_store.load))
    monkeypatch.setattr(sweep_cache, "cache_key", wrap(sweep_cache.cache_key))
    monkeypatch.setattr(verdict_store.os, "remove", wrap(verdict_store.os.remove))
    if operation == "publish":
        publish_verdict(media, key, Verdict(Status.PENDING), fingerprint())
    elif operation == "close":
        with sweep_cache.observing(media) as observation:
            assert observation.changing(media)
            observation.changed()
    elif operation == "clear":
        sweep_cache.clear()
    elif operation == "live":
        with sweep_cache.live(cache):
            pass
    else:
        getattr(cache, operation)()
    assert checked


def test_a_cancelled_read_does_not_wait_for_an_edit_or_retain_a_revision(state_cache, media):
    with sweep_cache.observing(media) as owner:
        assert owner.changing(media)
        with (
            pytest.raises(sweep_cache.ObservationStoppedError),
            sweep_cache.observing(media, stopped=lambda: True),
        ):
            pytest.fail("read a file during its edit")
        assert owner.paths[media][0].users == 1
    assert not sweep_cache._revisions
