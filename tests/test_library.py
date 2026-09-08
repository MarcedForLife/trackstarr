"""The library view's index: *arr titles joined to cached verdicts."""

import contextlib
import json
import os
import threading
import time

import pytest

from conftest import configured_arr
from trackstarr import config, library, ratings, settings, state, sweep_cache
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict


@pytest.fixture(autouse=True)
def _cold_index():
    """Every test builds its own library; the memo must not carry one into
    the next. The scores are memoised beside it and go the same way."""
    library.forget()
    ratings.forget()
    yield
    library.forget()
    ratings.forget()


@pytest.fixture
def media(tmp_path, monkeypatch) -> str:
    root = tmp_path / "media" / "movies"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    return str(root)


def movie(
    item_id: int,
    title: str,
    folder: str,
    year: int = 2024,
    added: str = "2024-01-01T00:00:00Z",
) -> dict:
    return {
        "id": item_id,
        "title": title,
        "year": year,
        "path": folder,
        "added": added,
        "originalLanguage": {"name": "English"},
    }


def store_scores(scores: dict[str, float]) -> None:
    """The IMDb table a daily fetch would have left in STATE_DIR."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    state.write_json(ratings.path(), {"scores": scores})
    ratings.forget()


def stub_arrs(monkeypatch, items: list[dict], name: str = "radarr") -> None:
    """One reachable *arr answering with these movies."""
    arr = configured_arr(name)
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])
    monkeypatch.setattr(type(arr), "all_items", lambda self: items)


def cache(*entries: tuple[str, Verdict], size: int = 100) -> None:
    """Write a sweep cache holding these verdicts, as a sweep would."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    store = SweepCache(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), Policy.from_config().fingerprint()
    )
    for path, verdict in entries:
        store.record(path, FileKey(size, 1, 1, "eng"), verdict)
    store.save()


@contextlib.contextmanager
def walking(*entries: tuple[str, Verdict], size: int = 100):
    """A walk holding the cache open with these verdicts reached and none of
    them written, which is every walk between two checkpoints."""
    store = SweepCache(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), Policy.from_config().fingerprint()
    )
    for path, verdict in entries:
        store.record(path, FileKey(size, 1, 1, "eng"), verdict)
    store.publish_view()
    with sweep_cache.live(store):
        yield store


#: A fixed point well behind anything a test writes, for backdating.
LAST_YEAR = 1_700_000_000.0


def backdate(when: float, *paths: str) -> None:
    """Move stored verdicts back to when a sweep that ran then would have
    reached them. The cache stamps whatever the moment is, so this is the only
    way to write one that is not now."""
    store = os.path.join(config.STATE_DIR, "sweep-cache.json")
    with open(store) as cache_file:
        data = json.load(cache_file)
    for path in paths:
        data["files"][path]["judged"] = when
    with open(store, "w") as cache_file:
        json.dump(data, cache_file)
    library.forget()


def would_fix() -> Verdict:
    return Verdict(
        Status.WOULD_FIX,
        "add 2.0 downmix from stream 1 (6ch eng)",
        tracks=[
            {"index": 0, "kind": "video", "codec": "h264"},
            {"index": 1, "kind": "audio", "codec": "eac3", "channels": 6, "lang": "eng"},
        ],
        planned=[
            {"index": 0, "src": 0, "kind": "video", "codec": "h264"},
            {
                "index": 1,
                "src": 1,
                "kind": "audio",
                "codec": "aac",
                "channels": 2,
                "title": "2.0",
                "flags": ["generated"],
            },
            {"index": 2, "src": 1, "kind": "audio", "codec": "eac3", "channels": 6},
        ],
        why={"reasons": ["add 2.0 downmix from stream 1 (6ch eng)"], "rules": ["downmix"]},
    )


#: What a rewrite leaves on the verdict it publishes; see
#: :func:`trackstarr.processing._fixed`.
REWROTE = {
    "at": "2026-03-01T12:00:00+13:00",
    "bytes_before": 2_000,
    "bytes_after": 1_800,
    "added": [1],
}


def regenerated(stale_only: bool = True) -> Verdict:
    """A stale 2.0 dropped and a fresh one generated in its place, which is
    one change however the card counts it. With ``stale_only`` off there is a
    second drop beside it that nothing replaces."""
    tracks = [
        {"index": 0, "kind": "video", "codec": "h264"},
        {"index": 1, "kind": "audio", "codec": "eac3", "channels": 6, "lang": "eng"},
        {"index": 2, "kind": "audio", "codec": "aac", "channels": 2, "lang": "eng"},
    ]
    if not stale_only:
        tracks.append(
            {"index": 3, "kind": "audio", "codec": "dts", "channels": 6, "lang": "dan"}
        )
    return Verdict(
        Status.WOULD_FIX,
        "regenerate 2.0 downmix",
        tracks=tracks,
        planned=[
            {"index": 0, "src": 0, "kind": "video", "codec": "h264"},
            {
                "index": 1,
                "src": 1,
                "kind": "audio",
                "codec": "eac3",
                "channels": 6,
                "lang": "eng",
            },
            {
                "index": 2,
                "src": 1,
                "kind": "audio",
                "codec": "aac",
                "channels": 2,
                "lang": "eng",
                "title": "2.0",
                "flags": ["generated"],
            },
        ],
        why={"reasons": ["regenerate 2.0 downmix"], "rules": ["downmix"]},
    )


def test_a_title_with_no_verdicts_is_still_on_the_shelf(media, monkeypatch):
    """The *arrs are the spine: a film nothing has swept is a poster reading
    "not checked", not an absence the reader has to notice."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune (2024)")])
    shelf = library.shelf()
    assert [(card["name"], card["state"]) for card in shelf["titles"]] == [
        ("Dune", "unchecked")
    ]
    assert shelf["swept"] == 0


def test_a_title_with_nothing_downloaded_is_missing_rather_than_unchecked(media, monkeypatch):
    """The two absences are different news. Radarr tracking a film nobody has
    downloaded is a wishlist entry with nothing to judge; files on disk that no
    sweep has reached is work outstanding. Only the *arr can tell them apart."""
    wanted = movie(1, "Dune", f"{media}/Dune (2024)") | {"hasFile": False}
    got = movie(2, "Arrival", f"{media}/Arrival (2016)") | {"hasFile": True}
    stub_arrs(monkeypatch, [wanted, got])
    states = {card["name"]: card["state"] for card in library.shelf()["titles"]}
    assert states == {"Dune": "missing", "Arrival": "unchecked"}


def test_a_titles_score_comes_off_the_stored_imdb_table(media, monkeypatch):
    """Films and series alike: the *arrs are asked for the id and IMDb's own
    dataset for the number, so a series is no longer the one without."""
    store_scores({"tt15239678": 6.4, "tt7366338": 9.3})
    film = movie(1, "Dune", f"{media}/Dune (2024)") | {"imdbId": "tt15239678"}
    series = {
        "id": 2,
        "title": "Chernobyl",
        "path": f"{media}/Chernobyl",
        "imdbId": "tt7366338",
        # Sonarr's own unattributed rating, which nothing reads: a number
        # under an IMDb label has to be IMDb's.
        "ratings": {"votes": 500, "value": 4.1},
    }
    stub_arrs(monkeypatch, [film, series])

    scored = {card["name"]: card.get("rating") for card in library.shelf()["titles"]}
    assert scored == {"Dune": 6.4, "Chernobyl": 9.3}


def test_a_title_the_table_has_no_row_for_shows_no_score(media, monkeypatch):
    """Which is a title IMDb holds no rating for, and every title before the
    first fetch has landed."""
    store_scores({"tt15239678": 6.4})
    unrated = movie(1, "Chernobyl", f"{media}/Chernobyl") | {"imdbId": "tt0000001"}
    stub_arrs(monkeypatch, [unrated])

    assert "rating" not in library.shelf()["titles"][0]


def test_the_ids_handed_to_the_refresh_are_the_shelfs(media, monkeypatch):
    """What the daily fetch filters 1.7 million rows down to."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Dune", f"{media}/Dune (2024)") | {"imdbId": "tt15239678"},
            movie(2, "Home Video", f"{media}/Home Video"),
        ],
    )

    assert library.imdb_ids() == {"tt15239678"}


def test_a_library_missing_an_arr_hands_over_no_ids_at_all(media, monkeypatch):
    """A table rebuilt from half a library would drop every series' score for
    the day the *arr spent unreachable, so the pass is skipped instead."""

    def unreachable(self):
        raise OSError("radarr is down")

    arr = configured_arr("radarr")
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])
    monkeypatch.setattr(type(arr), "all_items", unreachable)

    assert library.imdb_ids() is None


def test_sonarr_says_it_with_an_episode_count(media, monkeypatch):
    """The same question, spelled the other way: Radarr answers per movie and
    Sonarr with a tally inside its statistics."""
    none = {
        "id": 1,
        "title": "Chernobyl",
        "path": f"{media}/Chernobyl",
        "statistics": {"episodeFileCount": 0},
    }
    some = {
        "id": 2,
        "title": "Severance",
        "path": f"{media}/Severance",
        "statistics": {"episodeFileCount": 9},
    }
    stub_arrs(monkeypatch, [none, some], name="sonarr")
    states = {card["name"]: card["state"] for card in library.shelf()["titles"]}
    assert states == {"Chernobyl": "missing", "Severance": "unchecked"}


def test_an_episode_count_that_is_not_a_number_reads_as_present(media, monkeypatch):
    """Taken as present rather than missing: the wrong way round would hide a
    title from the default grid on the strength of a field an *arr simply did
    not send properly."""
    odd = {
        "id": 1,
        "title": "Chernobyl",
        "path": f"{media}/Chernobyl",
        "statistics": {"episodeFileCount": None},
    }
    stub_arrs(monkeypatch, [odd], name="sonarr")
    assert library.shelf()["titles"][0]["state"] == "unchecked"


def test_a_title_with_no_folder_of_its_own_is_left_out(media, monkeypatch):
    """A path that normalises to nothing is the filesystem root, and a title
    claiming that would claim every file in the library."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Rooted", "/"),
            movie(2, "Trailing", f"{media}/Dune (2024)/"),
        ],
    )
    assert [card["name"] for card in library.shelf()["titles"]] == ["Trailing"]
    assert library.title("arr:radarr:2")["folder"] == f"{media}/Dune (2024)"


def test_a_date_no_calendar_could_read_is_no_date(media, monkeypatch):
    """The *arrs are .NET and serialise up to seven fractional digits, which
    fromisoformat will not take; something else arrives now and then. A title
    with an unreadable date still has everything else to say about itself."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Dune", f"{media}/Dune", added="2024-01-01T00:00:00.1234567Z"),
            movie(2, "Arrival", f"{media}/Arrival", added="whenever"),
        ],
    )
    cards = {card["name"]: card for card in library.shelf()["titles"]}
    assert cards["Dune"]["added"] > 0
    assert "added" not in cards["Arrival"], "no date rather than a wrong one"


def test_a_verdict_beats_what_the_arr_believes_about_the_file(media, monkeypatch):
    """What the sweep found on disk wins. An *arr that has not caught up with an
    import must not empty a title the cache holds real verdicts for."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder) | {"hasFile": False}])
    cache((f"{folder}/dune.mkv", would_fix()))
    assert library.shelf()["titles"][0]["state"] == "would-fix"


def test_missing_titles_sort_below_the_unswept_ones(media, monkeypatch):
    """Worst first, and a title with no file is not work: it goes under the one
    that has files nobody has looked at yet."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Arrival", f"{media}/Arrival") | {"hasFile": False},
            movie(2, "Dune", f"{media}/Dune") | {"hasFile": True},
        ],
    )
    assert [card["name"] for card in library.shelf()["titles"]] == ["Dune", "Arrival"]


def test_verdicts_land_under_the_title_whose_folder_holds_them(media, monkeypatch):
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache(
        (f"{folder}/Dune.mkv", would_fix()),
        (f"{folder}/extras/short.mkv", Verdict(Status.CONFORM)),
    )
    card = library.shelf()["titles"][0]
    assert card["files"] == 2
    assert card["counts"] == {"would-fix": 1, "conform": 1}
    # The worst thing true of any of its files is what the card says.
    assert card["state"] == "would-fix"
    assert card["adds"] == ["2.0"]


def test_a_title_of_unsupported_containers_says_so_rather_than_reading_empty(
    media, monkeypatch
):
    """The reported bug: an AVI-only title had no verdicts under it, so the grid
    could only call it unchecked and count nothing. Now it names the container
    and the file it is."""
    folder = f"{media}/Ronin (1998)"
    stub_arrs(monkeypatch, [movie(1, "Ronin", folder)])
    cache(
        (
            f"{folder}/Ronin.avi",
            Verdict(
                Status.UNSUPPORTED,
                "container .avi not in ALLOWED_EXTS",
                why={"skip": "container .avi not in ALLOWED_EXTS"},
            ),
        )
    )
    card = library.shelf()["titles"][0]
    assert card["state"] == "unsupported"
    assert card["files"] == 1
    assert card["bytes"] == 100

    (opened,) = library.title(card["id"])["files"]
    assert opened["status"] == "unsupported"
    assert opened["why"]["skip"] == "container .avi not in ALLOWED_EXTS"


def test_a_stray_unsupported_file_does_not_outrank_the_work(media, monkeypatch):
    """A sample.avi beside forty episodes must not read as the title's answer:
    nothing will ever happen to it, and something will happen to the rest."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache(
        (f"{folder}/Dune.mkv", would_fix()),
        (
            f"{folder}/sample.avi",
            Verdict(Status.UNSUPPORTED, "container .avi not in ALLOWED_EXTS"),
        ),
    )
    card = library.shelf()["titles"][0]
    assert card["counts"] == {"would-fix": 1, "unsupported": 1}
    assert card["state"] == "would-fix"


def test_a_regenerated_downmix_is_one_change_not_a_gain_and_a_loss(media, monkeypatch):
    """The layout the file ends with is the one it started with, so a card
    naming a layout gained and a track lost would be arithmetic for the
    reader to undo."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/Dune.mkv", regenerated()))
    card = library.shelf()["titles"][0]
    assert card["rebuilds"] == ["2.0"]
    assert "adds" not in card
    assert "drops" not in card


def test_a_drop_nothing_replaces_is_still_counted_beside_a_rebuild(media, monkeypatch):
    """Only the stale downmix is claimed by the one taking its place. The
    Danish track goes with nothing coming back for it, and says so."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/Dune.mkv", regenerated(stale_only=False)))
    card = library.shelf()["titles"][0]
    assert card["rebuilds"] == ["2.0"]
    assert card["drops"] == 1


def _relanguaged() -> Verdict:
    """A German 2.0 and a French one dropped, with a French 2.0 generated in
    their place. The German is a loss; only the French is a rebuild."""
    return Verdict(
        Status.WOULD_FIX,
        "regenerate 2.0 downmix",
        tracks=[
            {"index": 0, "kind": "video", "codec": "h264"},
            {"index": 1, "kind": "audio", "codec": "eac3", "channels": 6, "lang": "fre"},
            {"index": 2, "kind": "audio", "codec": "aac", "channels": 2, "lang": "ger"},
            {"index": 3, "kind": "audio", "codec": "aac", "channels": 2, "lang": "fre"},
        ],
        planned=[
            {"index": 0, "src": 0, "kind": "video", "codec": "h264"},
            {
                "index": 1,
                "src": 1,
                "kind": "audio",
                "codec": "eac3",
                "channels": 6,
                "lang": "fre",
            },
            {
                "index": 2,
                "src": 1,
                "kind": "audio",
                "codec": "aac",
                "channels": 2,
                "lang": "fre",
                "title": "2.0",
                "flags": ["generated"],
            },
        ],
        why={"reasons": ["regenerate 2.0 downmix"], "rules": ["downmix"]},
    )


def test_a_rebuild_is_paired_with_the_drop_in_its_own_language(media, monkeypatch):
    """A German 2.0 dropped for a French one is two changes, so the pairing
    goes by language before layout."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/Dune.mkv", _relanguaged()))

    card = library.shelf()["titles"][0]
    assert card["rebuilds"] == ["2.0"]
    assert card["drops"] == 1


def test_an_unnamed_rebuild_still_claims_the_track_it_replaces(media, monkeypatch):
    """The name is what a card lists a rebuild by, and a plan can carry a
    generated track with none. The drop it stands in for is claimed before the
    name is looked at, so the file does not read as having lost a track."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    unnamed = regenerated()
    del unnamed.planned[2]["title"]
    cache((f"{folder}/Dune.mkv", unnamed))

    card = library.shelf()["titles"][0]
    assert "rebuilds" not in card
    assert "drops" not in card


def test_a_verdict_with_no_readable_stamp_does_not_take_the_card_with_it(media, monkeypatch):
    """The card's date is the newest of its files. One entry that cannot say
    when is one file's worth of the answer missing, not the card's."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/one.mkv", would_fix()), (f"{folder}/two.mkv", Verdict(Status.CONFORM)))
    backdate("last tuesday", f"{folder}/one.mkv")
    backdate(LAST_YEAR, f"{folder}/two.mkv")

    card = library.shelf()["titles"][0]
    assert card["files"] == 2
    assert card["processed"] == LAST_YEAR


def test_a_nested_title_wins_over_the_one_around_it(media, monkeypatch):
    """The same innermost-folder rule the sweep matches by, so a boxset
    inside another title's folder does not swallow it."""
    outer = f"{media}/Collection"
    inner = f"{outer}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Collection", outer), movie(2, "Dune", inner)])
    cache((f"{inner}/Dune.mkv", would_fix()))
    counted = {card["name"]: card.get("files", 0) for card in library.shelf()["titles"]}
    assert counted == {"Collection": 0, "Dune": 1}


def test_files_no_arr_claims_are_grouped_by_their_own_folder(media, monkeypatch):
    """A file the *arrs have never heard of is the one most worth seeing, so
    it gets a tile of its own rather than being dropped."""
    stub_arrs(monkeypatch, [])
    cache((f"{media}/Loose Film (1999)/film.mkv", would_fix()))
    card = library.shelf()["titles"][0]
    assert (card["name"], card["kind"], card["state"]) == (
        "Loose Film (1999)",
        "folder",
        "would-fix",
    )


def test_a_file_loose_in_a_media_dir_has_no_title_folder(media, monkeypatch):
    stub_arrs(monkeypatch, [])
    cache((f"{media}/stray.mkv", would_fix()))
    assert library.shelf()["titles"] == []


def test_a_verdict_outside_every_media_dir_belongs_to_no_title(media, monkeypatch, tmp_path):
    """A verdict from before a media dir was taken out of the settings, or for
    a file that has since moved. It is under none of them now, and inventing a
    title for it would put a folder nothing sweeps on the grid."""
    tv = tmp_path / "media" / "tv"
    tv.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tv), media])
    stub_arrs(monkeypatch, [])
    cache(
        (f"{media}/Loose Film (1999)/film.mkv", would_fix()),
        (str(tmp_path / "elsewhere" / "Stray (2001)" / "stray.mkv"), would_fix()),
    )

    assert [card["name"] for card in library.shelf()["titles"]] == ["Loose Film (1999)"]


def test_the_shelf_leads_with_what_needs_work(media, monkeypatch):
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Tidy", f"{media}/Tidy"),
            movie(2, "Broken", f"{media}/Broken"),
            movie(3, "Aardvark", f"{media}/Aardvark"),
        ],
    )
    cache(
        (f"{media}/Tidy/a.mkv", Verdict(Status.CONFORM)),
        (f"{media}/Broken/b.mkv", would_fix()),
    )
    assert [card["name"] for card in library.shelf()["titles"]] == [
        "Broken",
        "Tidy",
        "Aardvark",
    ]


def test_the_overview_strip_leads_with_what_landed_last(media, monkeypatch):
    """The grid leads with what needs work; the strip beside a dashboard
    leads with what has just arrived, whatever the sweep made of it."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Older", f"{media}/Older", added="2024-01-01T00:00:00Z"),
            # Seven fractional digits, which is what a .NET service serialises
            # and more than fromisoformat takes.
            movie(2, "Newer", f"{media}/Newer", added="2025-06-01T12:00:00.1234567Z"),
        ],
    )
    cache(
        (f"{media}/Older/a.mkv", would_fix()),
        (f"{media}/Newer/b.mkv", Verdict(Status.CONFORM)),
    )
    assert [card["name"] for card in library.shelf()["titles"]] == ["Older", "Newer"]
    assert [card["name"] for card in library.summary(order="added")["head"]] == [
        "Newer",
        "Older",
    ]


def test_a_title_with_nothing_downloaded_is_not_the_newest_poster(media, monkeypatch):
    """A film added to Radarr this morning that has not downloaded yet has no
    verdict to give, so it goes behind every title that has one."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Downloaded", f"{media}/Downloaded", added="2024-01-01T00:00:00Z"),
            movie(2, "Wanted", f"{media}/Wanted", added="2025-06-01T00:00:00Z"),
        ],
    )
    cache((f"{media}/Downloaded/a.mkv", Verdict(Status.CONFORM)))
    assert [card["name"] for card in library.summary()["head"]] == ["Downloaded", "Wanted"]


def test_a_card_says_when_its_files_were_last_judged(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    before = int(time.time())
    cache((f"{media}/Dune/d.mkv", would_fix()))
    assert library.shelf()["titles"][0]["processed"] >= before


def test_the_newest_of_a_titles_files_is_when_it_was_processed(media, monkeypatch):
    """A series is one card over thirty files, and what the order answers is
    when trackstarr last had any of them open."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    cache(
        (f"{media}/Dune/one.mkv", would_fix()),
        (f"{media}/Dune/two.mkv", Verdict(Status.CONFORM)),
    )
    backdate(LAST_YEAR, f"{media}/Dune/one.mkv")
    backdate(LAST_YEAR + 1000, f"{media}/Dune/two.mkv")
    assert library.shelf()["titles"][0]["processed"] == LAST_YEAR + 1000


def test_a_card_carries_the_weight_of_its_changes(media, monkeypatch):
    """Both the grid and the strip order on how much a rewrite would change,
    so the number the two of them read is worked out once, here."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    cache(
        (f"{media}/Dune/one.mkv", would_fix()),
        (f"{media}/Dune/two.mkv", Verdict(Status.CONFORM)),
    )
    # One layout gained across the title's files, and nothing dropped.
    assert library.shelf()["titles"][0]["weight"] == 1


def test_the_strip_can_lead_with_what_was_processed_last(media, monkeypatch):
    """Added and processed are different questions, and a strip set to the
    second has to be able to disagree with the first."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Older", f"{media}/Older", added="2025-06-01T00:00:00Z"),
            movie(2, "Newer", f"{media}/Newer", added="2024-01-01T00:00:00Z"),
        ],
    )
    cache(
        (f"{media}/Older/a.mkv", would_fix()),
        (f"{media}/Newer/b.mkv", Verdict(Status.CONFORM)),
    )
    backdate(LAST_YEAR, f"{media}/Older/a.mkv")
    assert [card["name"] for card in library.summary(order="added")["head"]] == [
        "Older",
        "Newer",
    ]
    # Which is also what the strip does unasked: a title swept a minute ago is
    # the one something has happened to.
    assert [card["name"] for card in library.summary()["head"]] == ["Newer", "Older"]


def test_an_order_nothing_goes_by_is_the_default_rather_than_an_error(media, monkeypatch):
    """The strip is a dozen posters, and a query string is not worth a broken
    landing page."""
    stub_arrs(
        monkeypatch,
        [
            movie(1, "Older", f"{media}/Older", added="2024-01-01T00:00:00Z"),
            movie(2, "Newer", f"{media}/Newer", added="2025-06-01T00:00:00Z"),
        ],
    )
    cache(
        (f"{media}/Older/a.mkv", would_fix()),
        (f"{media}/Newer/b.mkv", Verdict(Status.CONFORM)),
    )
    assert library.summary(order="sideways")["head"] == library.summary()["head"]


def test_a_folder_no_arr_claims_is_dated_by_the_folder_itself(media, monkeypatch):
    """Nothing tracks it, so its mtime is the only added date."""
    folder = f"{media}/Loose Film (1999)"
    os.makedirs(folder)
    stub_arrs(monkeypatch, [])
    cache((f"{folder}/film.mkv", would_fix()))
    card = library.shelf()["titles"][0]
    assert card["added"] == int(os.stat(folder).st_mtime)


def test_a_shelf_says_when_an_arr_could_not_be_listed(media, monkeypatch):
    """Titles are missing rather than absent, and the page has to be able to
    say so instead of showing a short library as the whole one."""
    arr = configured_arr()
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])

    def refuse(self):
        raise OSError("down")

    monkeypatch.setattr(type(arr), "all_items", refuse)
    assert library.shelf()["complete"] is False


def test_a_shelf_says_when_the_rules_have_moved_on(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    cache((f"{media}/Dune/d.mkv", would_fix()))
    assert library.shelf()["current"] is True
    monkeypatch.setattr(config, "AUDIO_LAYOUTS", ("2.0",))
    library.forget()
    assert library.shelf()["current"] is False


def test_a_title_hands_back_both_states_and_the_reason(media, monkeypatch):
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/Dune.mkv", would_fix()))
    detail = library.title("arr:radarr:1")
    assert detail["name"] == "Dune"
    only = detail["files"][0]
    assert only["name"] == "Dune.mkv"
    assert [track["index"] for track in only["tracks"]] == [0, 1]
    assert [track["channels"] for track in only["planned"] if track.get("channels")] == [2, 6]
    assert only["why"]["rules"] == ["downmix"]


def test_a_passed_file_still_says_a_rewrite_made_it_pass(media, monkeypatch):
    """A rewritten file conforms like any other, so what the rewrite left on
    the verdict is the sheet's only way to tell the two apart."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache(
        (f"{folder}/Dune.mkv", Verdict(Status.CONFORM, fixed=REWROTE)),
        (f"{folder}/Extras.mkv", Verdict(Status.CONFORM)),
    )
    files = {file["name"]: file for file in library.title("arr:radarr:1")["files"]}
    assert files["Dune.mkv"]["fixed"] == REWROTE
    # Off every other file, which is most of a settled library.
    assert "fixed" not in files["Extras.mkv"]


def test_a_rewritten_file_leads_the_others_of_its_verdict(media, monkeypatch):
    """On a long series the cap falls on the files with nothing to report, and
    a rewritten one is the only Passed file with anything to read."""
    folder = f"{media}/Show"
    stub_arrs(monkeypatch, [movie(1, "Show", folder)], name="sonarr")
    cache(
        (f"{folder}/s01e01.mkv", Verdict(Status.CONFORM)),
        (f"{folder}/s01e02.mkv", Verdict(Status.CONFORM, fixed=REWROTE)),
        (f"{folder}/s01e03.mkv", would_fix()),
    )
    detail = library.title("arr:sonarr:1")
    assert [file["name"] for file in detail["files"]] == [
        "s01e03.mkv",
        "s01e02.mkv",
        "s01e01.mkv",
    ]


def test_a_card_counts_the_files_a_rewrite_left(media, monkeypatch):
    """The poster says Passed either way, so the count is the whole of what
    marks a title trackstarr has been through."""
    stub_arrs(monkeypatch, [movie(1, "Show", f"{media}/Show")], name="sonarr")
    cache(
        (f"{media}/Show/one.mkv", Verdict(Status.CONFORM, fixed=REWROTE)),
        (f"{media}/Show/two.mkv", Verdict(Status.CONFORM, fixed=REWROTE)),
        (f"{media}/Show/three.mkv", Verdict(Status.CONFORM)),
    )
    card = library.shelf()["titles"][0]
    assert card["state"] == "conform"
    assert card["fixed"] == 2


def test_a_title_lists_what_needs_work_first(media, monkeypatch):
    """A series past the cap shows a part of itself, so the part it shows has
    to be the part worth reading."""
    folder = f"{media}/Show"
    stub_arrs(monkeypatch, [movie(1, "Show", folder)], name="sonarr")
    cache(
        (f"{folder}/s01e01.mkv", Verdict(Status.CONFORM)),
        (f"{folder}/s01e02.mkv", would_fix()),
        (f"{folder}/s01e03.mkv", Verdict(Status.SKIP, "no video stream")),
    )
    detail = library.title("arr:sonarr:1")
    assert [file["status"] for file in detail["files"]] == ["would-fix", "skip", "conform"]
    assert detail["total"] == 3


def test_a_long_title_is_capped_and_says_so(media, monkeypatch):
    folder = f"{media}/Show"
    stub_arrs(monkeypatch, [movie(1, "Show", folder)], name="sonarr")
    cache(
        *(
            (f"{folder}/e{n:03}.mkv", Verdict(Status.CONFORM))
            for n in range(library.MAX_FILES + 5)
        )
    )
    detail = library.title("arr:sonarr:1")
    assert len(detail["files"]) == library.MAX_FILES
    assert detail["total"] == library.MAX_FILES + 5


def test_an_unknown_title_is_no_answer_rather_than_an_empty_one(media, monkeypatch):
    stub_arrs(monkeypatch, [])
    assert library.title("arr:radarr:404") is None


def test_a_cover_comes_from_the_arr_that_cached_it(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    asked: list[str] = []

    def answer(url, headers=None, timeout=30):
        asked.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(library, "fetch", answer)
    body, kind = library.cover("arr:radarr:1")
    assert (body, kind) == (b"\xff\xd8jpeg", "image/jpeg")
    # Under /api/v3: the bare /MediaCover path the *arrs' own pages link to is
    # behind their browser login, and answers an API key with a redirect to it.
    assert asked == ["http://radarr:7878/api/v3/mediacover/1/poster-500.jpg"]


def test_a_fetched_cover_is_kept_and_not_fetched_again(media, monkeypatch):
    """A grid asks for hundreds of posters and reloads ask again; the *arr
    should see each title once, ever."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    asked: list[str] = []

    def answer(url, headers=None, timeout=30):
        asked.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(library, "fetch", answer)
    assert library.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert library.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert len(asked) == 1


def test_a_png_kept_on_disk_is_still_a_png(media, monkeypatch):
    """The type is sniffed back out of the stored bytes, so local artwork
    does not come back claiming to be a jpeg."""
    folder = os.path.join(media, "Loose Film")
    os.makedirs(folder)
    with open(os.path.join(folder, "poster.png"), "wb") as art:
        art.write(b"\x89PNG\r\n\x1a\nlocal")
    stub_arrs(monkeypatch, [])
    cache((f"{folder}/film.mkv", would_fix()))
    library.cover(f"dir:{folder}")
    assert library.cover(f"dir:{folder}")[1] == "image/png"


def test_a_title_with_no_poster_is_only_looked_for_once(media, monkeypatch):
    """Otherwise a shelf of unclaimed folders re-asks both *arrs on every
    load, and each ask is a connection that has to time out."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    tries: list[int] = []

    def refuse(url, headers=None, timeout=30):
        tries.append(1)
        raise OSError("no such poster")

    monkeypatch.setattr(library, "fetch", refuse)
    assert library.cover("arr:radarr:1") is None
    assert library.cover("arr:radarr:1") is None
    # Both poster sizes on the first ask, and nothing on the second.
    assert len(tries) == len(library._COVER_NAMES)


def test_warming_fetches_the_posters_the_grid_has_not_got(media, monkeypatch):
    stub_arrs(
        monkeypatch, [movie(1, "Dune", f"{media}/Dune"), movie(2, "Arrival", f"{media}/A")]
    )
    asked: list[str] = []

    def answer(url, headers=None, timeout=30):
        asked.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(library, "fetch", answer)
    library._warm_all()
    assert len(asked) == 2
    # And the grid that follows reads them off our disk rather than re-asking.
    assert library.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert len(asked) == 2
    # As does the next warm-up: a settled library asks for nothing at all.
    library._warm_all()
    assert len(asked) == 2


def _warm_up_finished() -> bool:
    """Wait for the background warm-up to let go of its lock."""
    for _ in range(500):
        if library._warming.acquire(blocking=False):
            library._warming.release()
            return True
        time.sleep(0.01)
    return False


def test_warming_returns_at_once_and_runs_one_walk_at_a_time(media, monkeypatch):
    """It is called on the request that hands the grid over, so it must not
    block that; and a second grid load a moment later is left to the walk
    already going rather than starting another over the same list."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    walks: list[int] = []
    walking = threading.Event()
    let_go = threading.Event()

    def slow_walk():
        walks.append(1)
        walking.set()
        let_go.wait(5)

    monkeypatch.setattr(library, "_warm_all", slow_walk)
    library.warm()
    assert walking.wait(5), "the caller was not made to wait for it"
    library.warm()
    let_go.set()

    assert _warm_up_finished()
    assert walks == [1]


def test_a_warm_up_that_fails_says_so_and_lets_the_next_one_run(media, monkeypatch, caplog):
    """A background thread has nobody to raise at. The grid still works, it
    just fetches its own posters one request at a time -- and the next warm-up
    must not find the lock stuck held by this one."""

    def boom():
        raise RuntimeError("the cache went away")

    monkeypatch.setattr(library, "_warm_all", boom)
    library.warm()

    assert _warm_up_finished()
    assert "could not warm the poster cache" in caplog.text


def test_the_sweep_cache_is_parsed_once_per_version_of_it(media, monkeypatch):
    """On a real library this file is megabytes and one page load reads it
    once per poster; parsing it each time is what made the grid crawl."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    cache((f"{media}/Dune/d.mkv", would_fix()))
    parses: list[int] = []
    real = library.sweep_cache.read
    monkeypatch.setattr(
        library.sweep_cache,
        "read",
        lambda path, fingerprint: (parses.append(1), real(path, fingerprint))[1],
    )
    library.shelf()
    library.shelf()
    library.title("arr:radarr:1")
    assert len(parses) == 1
    # A sweep writing a new verdict is picked up without waiting anything out.
    cache((f"{media}/Dune/d.mkv", Verdict(Status.CONFORM)))
    library.shelf()
    assert len(parses) == 2


def test_an_answer_that_is_not_a_picture_falls_through_to_the_next_name(media, monkeypatch):
    """An *arr with no poster cached answers the request rather than 404ing
    it, so the content type is the only thing that says whether what came
    back is an image."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])

    def answer(url, headers=None, timeout=30):
        if url.endswith("poster-500.jpg"):
            return b"<html>", "text/html"
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(library, "fetch", answer)
    assert library.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"


def test_a_poster_that_cannot_be_kept_is_still_served(media, monkeypatch, caplog):
    """An unwritable state dir costs the cache, not the picture."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    monkeypatch.setattr(
        library, "fetch", lambda url, headers=None, timeout=30: (b"\xff\xd8jpeg", "image/jpeg")
    )

    def refuse(partial, path):
        raise OSError("read-only file system")

    monkeypatch.setattr(library.os, "replace", refuse)
    assert library.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert "could not keep the poster" in caplog.text


def test_an_empty_file_beside_the_media_is_not_the_cover(media, monkeypatch):
    """A zero-byte poster.jpg is a download that never finished. The next name
    down is still worth trying."""
    folder = os.path.join(media, "Loose Film")
    os.makedirs(folder)
    with open(os.path.join(folder, "poster.jpg"), "wb"):
        pass
    with open(os.path.join(folder, "folder.jpg"), "wb") as art:
        art.write(b"local")
    stub_arrs(monkeypatch, [])
    cache((f"{folder}/film.mkv", would_fix()))

    assert library.cover(f"dir:{folder}") == (b"local", "image/jpeg")


def test_a_cover_falls_back_to_artwork_beside_the_files(media, monkeypatch):
    folder = os.path.join(media, "Loose Film")
    os.makedirs(folder)
    with open(os.path.join(folder, "poster.jpg"), "wb") as art:
        art.write(b"local")
    stub_arrs(monkeypatch, [])
    cache((f"{folder}/film.mkv", would_fix()))
    assert library.cover(f"dir:{folder}") == (b"local", "image/jpeg")


def test_a_cover_nobody_has_is_no_cover(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])

    def refuse(url, headers=None, timeout=30):
        raise OSError("no such poster")

    monkeypatch.setattr(library, "fetch", refuse)
    assert library.cover("arr:radarr:1") is None


def test_a_damaged_cache_is_an_empty_index_not_a_failed_request(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(os.path.join(config.STATE_DIR, "sweep-cache.json"), "w") as broken:
        broken.write("{not json")
    assert library.shelf()["titles"][0]["state"] == "unchecked"


def test_the_index_is_reused_until_it_is_dropped(media, monkeypatch):
    """One page load must not cost two library-sized fetches, and a saved
    address must not wait out the TTL to take effect."""
    arr = configured_arr()
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])
    calls: list[int] = []

    def once(self):
        calls.append(1)
        return [movie(1, "Dune", f"{media}/Dune")]

    monkeypatch.setattr(type(arr), "all_items", once)
    library.shelf()
    library.shelf()
    assert len(calls) == 1
    library.forget()
    library.shelf()
    assert len(calls) == 2


def test_the_index_is_dropped_when_the_settings_change(settings_state, monkeypatch):
    """The memo outlives a save by minutes, which is long enough for a
    corrected address to look like it did nothing."""
    monkeypatch.setattr(library, "all_arrs", list)
    library.shelf()
    assert library._cached is not None
    settings.update({"RADARR_URL": "http://radarr:7878"}, by="tester")
    library.forget()
    assert library._cached is None


def test_a_verdict_written_by_a_sweep_is_readable_by_the_view(media, monkeypatch):
    """The two halves agree on the file's shape, so a change to one that the
    other cannot read fails here rather than in a browser."""
    folder = f"{media}/Dune"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/d.mkv", would_fix()))
    with open(os.path.join(config.STATE_DIR, "sweep-cache.json")) as stored:
        entry = next(iter(json.load(stored)["files"].values()))
    assert {"status", "reasons", "tracks", "planned", "why"} <= set(entry)


def test_clearing_drops_the_verdicts_and_nothing_else(media, monkeypatch):
    """Clearing empties the verdicts. The *arr titles stay, and so does the rest
    of STATE_DIR."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune (2024)")])
    cache((f"{media}/Dune (2024)/dune.mkv", would_fix()))
    keepsake = os.path.join(config.STATE_DIR, "events.jsonl")
    with open(keepsake, "w") as other:
        other.write('{"event": "sweep"}\n')
    assert library.shelf()["titles"][0]["state"] == "would-fix"

    assert library.clear() == 1

    assert not os.path.exists(os.path.join(config.STATE_DIR, "sweep-cache.json"))
    assert os.path.exists(keepsake)
    shelf = library.shelf()
    cards = [(card["name"], card["state"]) for card in shelf["titles"]]
    assert cards == [("Dune", "unchecked")]
    assert shelf["swept"] == 0


def test_clearing_takes_the_memo_with_it(media, monkeypatch):
    """`_parsed` would notice on its own, but the title list is on a timer, and
    a library still showing cleared verdicts gets cleared twice."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune (2024)")])
    cache((f"{media}/Dune (2024)/dune.mkv", would_fix()))
    library.shelf()

    library.clear()
    # No forget() of the test's own: the clear has to have done it.
    assert library.shelf()["titles"][0]["state"] == "unchecked"


def test_clearing_an_empty_cache_is_not_an_error(media, monkeypatch):
    """The state being asked for is already the state it is in."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune (2024)")])
    assert library.clear() == 0


def test_selected_resolves_ids_to_the_folders_behind_them(media, monkeypatch):
    """What a re-check walks. Ids rather than paths is the whole point: they
    resolve against the library's own index, so the walk cannot be pointed
    anywhere the library does not already hold."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/Dune.mkv", would_fix()))
    picked = library.selected(["arr:radarr:1"])
    assert [(title.name, title.folder) for title in picked] == [("Dune", folder)]


def test_selected_reaches_a_folder_no_arr_claims(media, monkeypatch):
    """A file the *arrs have never heard of is the one most worth re-checking,
    so its own tile has to be runnable like any other."""
    stub_arrs(monkeypatch, [])
    folder = f"{media}/Loose Film (1999)"
    cache((f"{folder}/film.mkv", would_fix()))
    assert [title.folder for title in library.selected([f"dir:{folder}"])] == [folder]


def test_selected_drops_an_id_nothing_goes_by(media, monkeypatch):
    """A title removed in Radarr since the grid loaded must not cost the
    reader the rest of what they picked."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/Dune.mkv", would_fix()))
    picked = library.selected(["arr:radarr:1", "arr:radarr:99", "dir:/etc"])
    assert [title.name for title in picked] == ["Dune"]


def test_paths_resolve_to_the_card_of_the_title_holding_them(media, monkeypatch):
    """What the history's posters are built from. An event names a file; the
    folder it belongs to is the library's own answer, so the card raised from
    a feed row is the card the grid draws."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache(
        (f"{folder}/Dune.mkv", would_fix()),
        (f"{folder}/extras/short.mkv", Verdict(Status.CONFORM)),
    )
    owners, cards = library.cards_for_paths(
        [f"{folder}/Dune.mkv", f"{folder}/extras/short.mkv"]
    )
    # Two files, one title: the card travels once and each line carries the id.
    assert set(owners.values()) == {"arr:radarr:1"}
    assert list(cards) == ["arr:radarr:1"]
    card = cards["arr:radarr:1"]
    assert (card["name"], card["state"], card["files"]) == ("Dune", "would-fix", 2)


def test_paths_reach_a_folder_no_arr_claims(media, monkeypatch):
    """A rewrite of a file the *arrs never heard of still has a poster to
    stand under, for the same reason its tile is on the shelf."""
    stub_arrs(monkeypatch, [])
    folder = f"{media}/Loose Film (1999)"
    cache((f"{folder}/film.mkv", would_fix()))
    owners, cards = library.cards_for_paths([f"{folder}/film.mkv"])
    assert owners == {f"{folder}/film.mkv": f"dir:{folder}"}
    assert cards[f"dir:{folder}"]["kind"] == "folder"


def test_a_path_under_no_title_names_none(media, monkeypatch):
    """A file outside every media dir, or loose in one. The row goes without a
    poster rather than the request going without an answer."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune (2024)")])
    cache((f"{media}/Dune (2024)/Dune.mkv", would_fix()))
    assert library.cards_for_paths(["/elsewhere/film.mkv", f"{media}/stray.mkv"]) == (
        {},
        {},
    )
    assert library.cards_for_paths([]) == ({}, {})


def test_a_running_walks_verdicts_show_before_they_reach_disk(media, monkeypatch):
    """The cache file only moves on the sweep's own checkpoint, so a cold
    walk's first half minute used to read as a library nothing had looked at."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    with walking((f"{folder}/dune.mkv", would_fix())):
        assert not os.path.exists(os.path.join(config.STATE_DIR, "sweep-cache.json"))
        card = library.shelf()["titles"][0]
        assert (card["state"], card["files"]) == ("would-fix", 1)
        assert library.summary()["counts"] == {"would-fix": 1}


def test_a_running_walk_is_current_over_a_file_it_is_replacing(media, monkeypatch):
    """The walk loaded under the rules in force, so what it is finding is
    current whatever the verdicts it is about to overwrite were judged under."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/dune.mkv", would_fix()))
    monkeypatch.setattr(config, "AUDIO_LAYOUTS", ("2.0",))
    library.forget()
    assert library.shelf()["current"] is False

    with walking((f"{folder}/dune.mkv", Verdict(Status.CONFORM))):
        shelf = library.shelf()
        assert shelf["current"] is True
        assert shelf["titles"][0]["state"] == "conform"


def test_the_grid_is_built_once_per_version_of_the_verdicts(media, monkeypatch):
    """Every card rebuilt and every verdict tallied per request was affordable
    at one request a checkpoint, and is not at one every few seconds per tab."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    cache((f"{folder}/dune.mkv", would_fix()))
    first = library.shelf()
    assert library.shelf() is first


def test_the_grid_is_rebuilt_when_a_walk_offers_more(media, monkeypatch):
    """The file has not moved and will not until the checkpoint; the verdicts
    behind the grid have."""
    folder = f"{media}/Dune (2024)"
    stub_arrs(monkeypatch, [movie(1, "Dune", folder)])
    with walking((f"{folder}/one.mkv", would_fix())) as store:
        first = library.shelf()
        store.record(f"{folder}/two.mkv", FileKey(100, 1, 1, "eng"), Verdict(Status.CONFORM))
        store.publish_view()
        second = library.shelf()
    assert second is not first
    assert second["titles"][0]["files"] == 2


def test_the_grid_is_rebuilt_when_the_titles_are_fetched_again(media, monkeypatch):
    """The *arr list is on a timer of its own, and a memo outliving it would
    freeze a title added in Radarr for as long as the service sat idle."""
    folder = f"{media}/Dune (2024)"
    items = [movie(1, "Dune", folder)]
    stub_arrs(monkeypatch, items)
    cache((f"{folder}/dune.mkv", would_fix()))
    monkeypatch.setattr(library, "_INDEX_TTL", 0.0)
    first = library.shelf()

    items.append(movie(2, "Arrival", f"{media}/Arrival"))
    second = library.shelf()
    assert second is not first
    assert [card["name"] for card in second["titles"]] == ["Dune", "Arrival"]


def test_forgetting_drops_the_built_grid(media, monkeypatch):
    """The rules are not one of the reads it is keyed on, so a settings save
    has to be able to drop it outright."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    cache((f"{media}/Dune/d.mkv", would_fix()))
    first = library.shelf()
    library.forget()
    assert library._built is None
    assert library.shelf() is not first
