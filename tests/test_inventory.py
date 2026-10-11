"""Variants come from the *arrs' file lists, and doubt keeps them out of sourcing."""

import os
import urllib.error
from dataclasses import replace

import pytest

from conftest import configured_arr
from trackstarr import arr as arr_module
from trackstarr import inventory
from trackstarr.arr import Arr
from trackstarr.inventory import ContentKey, Variant
from trackstarr.library import Source, Title

DUNE = ContentKey("tmdb", "movie", "438631")


def connection(name: str, instance_id: str, files: list[dict], episodes=()) -> Arr:
    """A reachable *arr answering with these files, and episodes for Sonarr."""
    held = replace(configured_arr(name), instance_id=instance_id)
    held.files = lambda item_id: files
    held.episodes = lambda series_id: list(episodes)
    return held


def down(name: str, instance_id: str) -> Arr:
    held = replace(configured_arr(name), instance_id=instance_id)

    def refused(item_id):
        raise urllib.error.URLError("connection refused")

    held.files = refused
    held.episodes = refused
    return held


def film(*sources: Source, provider_id: str = "438631") -> Title:
    return Title("arr:radarr:1", "Dune", sources, "movie", provider_id=provider_id)


def series(*sources: Source) -> Title:
    return Title("arr:sonarr:1", "Severance", sources, "series", provider_id="371980")


def episode(file_id: int, season: int, number: int, tvdb_id: int | None) -> dict:
    return {
        "episodeFileId": file_id,
        "seasonNumber": season,
        "episodeNumber": number,
        "tvdbId": tvdb_id,
    }


def touch(path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"media")
    return str(path)


def by_path(taken: inventory.Inventory) -> dict[str, Variant]:
    return {variant.path: variant for variant in taken.variants}


def test_one_film_in_two_connections_shares_its_content_key(tmp_path):
    hd = touch(tmp_path / "movies/Dune/Dune.mkv")
    uhd = touch(tmp_path / "movies-4k/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": hd}])
    four_k = connection("radarr", "radarr-4k", [{"id": 9, "path": uhd, "edition": "IMAX"}])
    taken = inventory.take(
        film(Source("/movies/Dune", radarr, 1), Source("/movies-4k/Dune", four_k, 3))
    )
    assert taken.complete and taken.title_id == "arr:radarr:1"
    first, second = taken.variants
    assert (first.instance_id, first.item_id, first.file_id) == ("radarr", 1, 5)
    assert (second.instance_id, second.item_id, second.file_id) == ("radarr-4k", 3, 9)
    assert first.content_keys == second.content_keys == (DUNE,)
    assert (first.edition, second.edition) == ("", "IMAX")
    assert first.revision is not None and first.revision.size == 5
    assert first.excluded is None and second.excluded is None


def test_a_film_without_a_provider_id_has_no_key_to_match_on(tmp_path):
    path = touch(tmp_path / "movies/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": path}])
    (variant,) = inventory.take(
        film(Source("/movies/Dune", radarr, 1), provider_id="")
    ).variants
    assert variant.content_keys == () and variant.excluded is None


def test_a_connection_that_cannot_be_listed_is_unavailable_not_empty(tmp_path):
    path = touch(tmp_path / "movies/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": path}])
    taken = inventory.take(
        film(
            Source("/movies/Dune", radarr, 1),
            Source("/movies-4k/Dune", down("radarr", "radarr-4k"), 3),
        )
    )
    assert taken.unavailable == ("radarr-4k",) and not taken.complete
    assert [variant.instance_id for variant in taken.variants] == ["radarr"]


def test_unclaimed_folders_and_pathless_entries_are_left_out(tmp_path):
    path = touch(tmp_path / "movies/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": path}, {"id": 6}])
    taken = inventory.take(film(Source("/movies/Dune", radarr, 1), Source("/loose/Dune")))
    assert [variant.file_id for variant in taken.variants] == [5]
    assert taken.complete


def test_a_file_that_cannot_be_read_has_no_revision(tmp_path):
    missing = str(tmp_path / "movies/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": missing}])
    (variant,) = inventory.take(film(Source("/movies/Dune", radarr, 1))).variants
    assert variant.revision is None and variant.excluded is None


def test_a_folder_several_connections_list_is_excluded(tmp_path):
    path = touch(tmp_path / "movies/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": path}])
    source = Source("/movies/Dune", radarr, 1, conflicting_instances=("radarr-4k",))
    (variant,) = inventory.take(film(source)).variants
    assert variant.excluded == inventory.SHARED_FOLDER


def test_one_file_in_two_connections_is_excluded_by_path_or_inode(tmp_path):
    shared = touch(tmp_path / "movies/Dune/Dune.mkv")
    linked = str(tmp_path / "movies-4k/Dune/Dune.mkv")
    os.makedirs(os.path.dirname(linked))
    os.link(shared, linked)
    alone = touch(tmp_path / "movies-remote/Dune/Dune.mkv")
    radarr = connection("radarr", "radarr", [{"id": 5, "path": shared}])
    # The same path spelt differently is still the same file.
    aliased = connection("radarr", "radarr-alias", [{"id": 2, "path": shared + "/."}])
    four_k = connection("radarr", "radarr-4k", [{"id": 9, "path": linked}])
    remote = connection("radarr", "radarr-remote", [{"id": 4, "path": alone}])
    taken = inventory.take(
        film(
            Source("/movies/Dune", radarr, 1),
            Source("/alias/Dune", aliased, 2),
            Source("/movies-4k/Dune", four_k, 3),
            Source("/movies-remote/Dune", remote, 4),
        )
    )
    assert {variant.instance_id: variant.excluded for variant in taken.variants} == {
        "radarr": inventory.SAME_FILE,
        "radarr-alias": inventory.SAME_FILE,
        "radarr-4k": inventory.SAME_FILE,
        "radarr-remote": None,
    }


def test_ownership_doubt_is_named_before_a_files_own(tmp_path):
    path = touch(tmp_path / "shows/Severance/S01E01-E02.mkv")
    sonarr = connection(
        "sonarr",
        "sonarr",
        [{"id": 1, "path": path}],
        [episode(1, 1, 2, 102), episode(1, 1, 1, 101)],
    )
    shared = Source("/shows/Severance", sonarr, 7, conflicting_instances=("sonarr-4k",))
    (variant,) = inventory.take(series(shared)).variants
    assert variant.excluded == inventory.SHARED_FOLDER
    assert variant.episodes == ("S01E01", "S01E02")
    (variant,) = inventory.take(series(replace(shared, conflicting_instances=()))).variants
    assert variant.excluded == inventory.MULTI_EPISODE


def test_episodes_match_on_tvdb_ids_across_connections(tmp_path):
    hd = tmp_path / "shows/Severance/Season 01"
    uhd = tmp_path / "shows-4k/Severance/Season 01"
    sonarr = connection(
        "sonarr",
        "sonarr",
        [
            {"id": 1, "path": touch(hd / "S01E01.mkv")},
            {"id": 2, "path": touch(hd / "S01E02.mkv")},
        ],
        [episode(1, 1, 1, 101), episode(2, 1, 2, 102), episode(0, 1, 3, 103)],
    )
    four_k = connection(
        "sonarr",
        "sonarr-4k",
        [{"id": 8, "path": touch(uhd / "S01E01.mkv")}],
        [episode(8, 1, 1, 101), episode(None, 1, 2, 102)],
    )
    taken = inventory.take(
        series(Source("/shows/Severance", sonarr, 7), Source("/shows-4k/Severance", four_k, 4))
    )
    assert [(variant.instance_id, variant.episodes) for variant in taken.variants] == [
        ("sonarr", ("S01E01",)),
        ("sonarr", ("S01E02",)),
        ("sonarr-4k", ("S01E01",)),
    ]
    first, _, mirror = taken.variants
    assert first.content_keys == mirror.content_keys == (ContentKey("tvdb", "episode", "101"),)
    assert all(variant.excluded is None for variant in taken.variants)


@pytest.mark.parametrize(
    ("four_k_episodes", "reason"),
    [
        # Sonarr renumbered one side: the id now sits at another number.
        ([episode(8, 1, 2, 101)], "the same id at two numbers"),
        # The other side's metadata names a different episode at this number.
        ([episode(8, 1, 1, 999)], "two ids at one number"),
    ],
)
def test_numbering_that_disagrees_across_connections_waits_for_review(
    tmp_path, four_k_episodes, reason
):
    sonarr = connection(
        "sonarr",
        "sonarr",
        [{"id": 1, "path": touch(tmp_path / "hd/S01E01.mkv")}],
        [episode(1, 1, 1, 101)],
    )
    four_k = connection(
        "sonarr",
        "sonarr-4k",
        [{"id": 8, "path": touch(tmp_path / "uhd/S01E01.mkv")}],
        four_k_episodes,
    )
    taken = inventory.take(
        series(Source("/shows/Severance", sonarr, 7), Source("/shows-4k/Severance", four_k, 4))
    )
    assert [variant.excluded for variant in taken.variants] == [
        inventory.UNMATCHED_EPISODE,
        inventory.UNMATCHED_EPISODE,
    ], reason


def test_an_episode_without_a_tvdb_id_or_a_file_without_episodes_is_unmatched(tmp_path):
    sonarr = connection(
        "sonarr",
        "sonarr",
        [
            {"id": 1, "path": touch(tmp_path / "S01E01.mkv")},
            {"id": 2, "path": touch(tmp_path / "orphan.mkv")},
            {"id": 3, "path": touch(tmp_path / "S01E03.mkv")},
            {"path": ""},
        ],
        [episode(1, 1, 1, 0), episode(3, 1, 3, 103)],
    )
    taken = inventory.take(series(Source("/shows/Severance", sonarr, 7)))
    assert [(variant.file_id, variant.excluded) for variant in taken.variants] == [
        (1, inventory.UNMATCHED_EPISODE),
        (2, inventory.UNMATCHED_EPISODE),
        (3, None),
    ]
    assert taken.variants[0].content_keys == () and taken.variants[0].episodes == ("S01E01",)


def test_file_listings_ask_each_arr_for_one_title(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(
        arr_module, "request", lambda url, *args, **kwargs: asked.append(url) or None
    )
    assert configured_arr("radarr").files(7) == []
    assert configured_arr("sonarr").files(3) == []
    assert configured_arr("sonarr").episodes(3) == []
    assert asked == [
        "http://radarr:7878/api/v3/moviefile?movieId=7",
        "http://sonarr:8989/api/v3/episodefile?seriesId=3",
        "http://sonarr:8989/api/v3/episode?seriesId=3",
    ]
    unconfigured = replace(configured_arr("sonarr"), key="")
    assert unconfigured.files(3) == [] and unconfigured.episodes(3) == []
    assert len(asked) == 3
