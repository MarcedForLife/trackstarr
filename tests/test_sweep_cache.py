"""The sweep verdict cache. Filesystem only, no media."""

import json
from pathlib import Path

import pytest

from conftest import set_config
from trackstarr import __version__, config, policy, sweep_cache
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FORMAT, FileKey, SweepCache, Verdict, cache_key, read

CONFORM = Verdict(Status.CONFORM)

#: What a rewrite leaves on the verdict it publishes; see
#: :func:`trackstarr.processing._modified`.
REWROTE = {
    "at": "2026-03-01T12:00:00+13:00",
    "bytes_before": 2_000,
    "bytes_after": 1_800,
    "added": [1],
}


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

    cache.record(media, key, CONFORM)
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
    cache.record(missing, key, CONFORM)
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
    cache.record(media, key, CONFORM)
    return cache, key, str(other), other_key


def test_save_prunes_entries_the_sweep_never_visited(cache_path, media, tmp_path):
    """A save ends a sweep, so a file it never saw has been moved or deleted."""
    cache, key, other, other_key = _one_of_two_visited(cache_path, media, tmp_path)
    cache.save()

    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) == CONFORM
    assert reloaded.lookup(other, other_key) is None


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

    cache.drop(media)
    cache.checkpoint()
    reloaded = SweepCache.load(cache_path, fingerprint())
    assert reloaded.lookup(media, key) is None


def test_a_dropped_file_that_gets_a_verdict_after_all_keeps_it(cache_path, media):
    """A re-check judges the same file twice inside one run when somebody
    picks a title, runs it, and picks it again."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, Verdict(Status.PENDING)))

    cache.drop(media)
    cache.record(media, key, CONFORM)
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
    cache.record(media, cache_key(media, "eng"), CONFORM)
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


def test_a_rewrite_record_outlives_the_same_verdict_reached_again(cache_path, media):
    """A re-check re-probes a file trackstarr rewrote and finds it conforms,
    which is the right answer and says nothing about the rewrite. Without this
    the library would go back to calling the file untouched."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, Verdict(Status.CONFORM, modified=REWROTE)))

    cache.record(media, key, CONFORM)
    cache.save()
    assert read(cache_path, fingerprint()).files[media]["modified"] == REWROTE


def test_a_changed_file_leaves_the_rewrite_record_behind(cache_path, media):
    """Whatever we made of the file, it is not what is on disk now."""
    rewrote = Verdict(Status.CONFORM, modified=REWROTE)
    saved_cache(cache_path, (media, cache_key(media, "eng"), rewrote))
    Path(media).write_bytes(b"x" * 20)

    cache = SweepCache.load(cache_path, fingerprint())
    cache.record(media, cache_key(media, "eng"), CONFORM)
    cache.save()
    assert "modified" not in read(cache_path, fingerprint()).files[media]


def test_an_unrewritten_file_has_no_record(cache_path, media):
    """Almost every entry in a settled library is one, so the field stays off
    them."""
    saved_cache(cache_path, (media, cache_key(media, "eng"), CONFORM))

    assert "modified" not in json.loads(Path(cache_path).read_text())["files"][media]


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
    """The cache where :func:`update` writes it, since it takes no path."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    return tmp_path / "sweep-cache.json"


def test_update_books_one_verdict_without_disturbing_the_others(state_cache, media, tmp_path):
    neighbour = str(tmp_path / "other.mkv")
    Path(neighbour).write_bytes(b"y" * 10)
    saved_cache(str(state_cache), (neighbour, cache_key(neighbour, "eng"), CONFORM))

    verdict = Verdict(Status.PENDING, "drop subtitle 3 (spa, Spanish)")
    sweep_cache.update(media, cache_key(media, "eng"), verdict, fingerprint())

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
    sweep_cache.update(media, key, verdict, fingerprint())
    from_delivery = json.loads(state_cache.read_text())["files"][media]

    saved_cache(str(state_cache), (media, key, verdict))
    assert json.loads(state_cache.read_text())["files"][media] == from_delivery


def test_update_creates_the_cache_when_nothing_has_swept(state_cache, media):
    """One verdict is still a verdict. The library shows it against its
    title and everything else as unchecked, which is what is known."""
    sweep_cache.update(media, cache_key(media, "eng"), CONFORM, fingerprint())
    assert read(str(state_cache), fingerprint()).files[media]["status"] == "conform"


def test_update_keeps_the_rewrite_record_the_sweep_stored(state_cache, media):
    """A delivery re-judging a file we rewrote reaches the same verdict a
    re-check would, and must not drop the record the same way."""
    key = cache_key(media, "eng")
    saved_cache(str(state_cache), (media, key, Verdict(Status.CONFORM, modified=REWROTE)))

    sweep_cache.update(media, key, CONFORM, fingerprint())
    assert read(str(state_cache), fingerprint()).files[media]["modified"] == REWROTE


def test_update_with_no_verdict_drops_the_stored_entry(state_cache, media):
    """What a rewritten file leaves behind: the old entry describes a file
    that no longer exists at that size and mtime."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    sweep_cache.update(media, None, None, fingerprint())
    assert read(str(state_cache), fingerprint()).files == {}


def test_update_leaves_a_cache_judged_under_other_rules_alone(state_cache, media):
    """Those verdicts are already flagged as not current, and `current` is one
    flag for the whole file: there is no honest way to file a fresh verdict
    among them. The next sweep drops the lot."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    before = state_cache.read_text()

    stale = fingerprint() | {"languages": ["fre:add"]}
    sweep_cache.update(media, cache_key(media, "eng"), Verdict(Status.PENDING), stale)
    assert state_cache.read_text() == before


def test_update_refuses_a_verdict_it_cannot_key(state_cache, tmp_path):
    """An unreadable file has nothing to key an entry by, so nothing could
    later tell whether it had changed. record() refuses these too."""
    gone = str(tmp_path / "vanished.mkv")
    sweep_cache.update(gone, cache_key(gone, "eng"), CONFORM, fingerprint())
    assert not state_cache.exists()


def test_update_dropping_nothing_does_not_rewrite_the_cache(state_cache, media, tmp_path):
    """A library's worth of JSON is not rewritten to say an absent entry is
    still absent."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))
    before = state_cache.stat().st_mtime_ns
    sweep_cache.update(str(tmp_path / "never-seen.mkv"), None, None, fingerprint())
    assert state_cache.stat().st_mtime_ns == before


def test_update_survives_a_cache_it_cannot_read(state_cache, media, caplog):
    state_cache.write_text("{not json")
    sweep_cache.update(media, cache_key(media, "eng"), CONFORM, fingerprint())
    assert state_cache.read_text() == "{not json"
    assert "could not read the sweep cache" in caplog.text


def test_update_leaves_a_cache_whose_files_are_not_an_object_alone(state_cache, media):
    """Parseable JSON in the right build's shape, with the wrong thing where
    the library goes. There is nowhere to file a verdict, and the next sweep
    replaces the file whole."""
    damaged = json.dumps({"format": FORMAT, "config": fingerprint(), "files": ["a list"]})
    state_cache.write_text(damaged)

    sweep_cache.update(media, cache_key(media, "eng"), CONFORM, fingerprint())
    assert state_cache.read_text() == damaged


def test_update_survives_a_write_it_cannot_make(state_cache, media, monkeypatch, caplog):
    """A full volume costs one file's verdict until the next sweep looks at
    it, not the delivery that was in the middle of booking it."""
    saved_cache(str(state_cache), (media, cache_key(media, "eng"), CONFORM))

    def full(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(sweep_cache, "write_json", full)
    sweep_cache.update(media, cache_key(media, "eng"), Verdict(Status.PENDING), fingerprint())

    assert read(str(state_cache), fingerprint()).files[media]["status"] == "conform"
    assert "could not write the sweep cache" in caplog.text


def test_the_offered_view_is_what_a_checkpoint_would_write(cache_path, media, tmp_path):
    """One expression behind both, so what a reader is shown mid-walk and what
    the file says a moment later cannot disagree."""
    key = cache_key(media, "eng")
    cache = saved_cache(cache_path, (media, key, CONFORM))
    fresh = str(tmp_path / "g.mkv")
    cache.record(fresh, FileKey(20, 2, 1, "eng"), Verdict(Status.PENDING, "add 2.0"))
    cache.drop(media)

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
    cache.record(media, key, Verdict(Status.PENDING, "add 2.0"))
    assert cache.publish_view() is True
    assert cache.publish_view() is False
    cache.drop(media)
    assert cache.publish_view() is True


def test_view_generations_climb_across_walks(cache_path, media):
    """A reader keys a memo on the file's stamp and the generation together,
    so a number a later walk reused would serve an earlier walk's verdicts
    under an unmoved file."""
    key = cache_key(media, "eng")
    first = SweepCache(cache_path, fingerprint())
    first.record(media, key, CONFORM)
    first.publish_view()
    second = SweepCache(cache_path, fingerprint())
    second.record(media, key, CONFORM)
    second.publish_view()
    assert second._snapshot[0] > first._snapshot[0]


def test_a_walk_lends_its_view_for_as_long_as_it_runs(cache_path, media):
    cache = SweepCache(cache_path, fingerprint())
    cache.record(media, cache_key(media, "eng"), CONFORM)
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
    cache.record(media, cache_key(media, "eng"), CONFORM)
    cache.publish_view()
    with sweep_cache.live(cache):
        assert sweep_cache.live_view(fingerprint() | {"languages": ["fre:add"]}) is None


def test_a_second_walk_takes_the_slot_and_says_so(cache_path, media, caplog):
    """Something upstream is meant to refuse the second one. If one ever gets
    through, the newer walk is the better of the two to be reading."""
    first = SweepCache(cache_path, fingerprint())
    second = SweepCache(cache_path, fingerprint())
    second.record(media, cache_key(media, "eng"), CONFORM)
    second.publish_view()
    with sweep_cache.live(first), sweep_cache.live(second):
        assert sweep_cache.live_view(fingerprint()) == second._snapshot
    assert "already holding the sweep cache" in caplog.text
