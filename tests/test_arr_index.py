"""The title index: which *arr title, if any, a library file belongs to.

Built once per sweep and then asked about every file, so the answers matter
as much as the speed. The paths are the *arrs' own and need not exist here,
which is why every case below is lexical.
"""

import pytest

from conftest import configured_arr
from trackstarr.arr import Arr, match_path, path_index


def stub_arr(*items: tuple[str, str], name: str = "radarr") -> Arr:
    """An *arr reporting the given (folder, original language name) titles."""
    arr = configured_arr(name)
    arr.all_items = lambda: [
        {"path": path, "id": i, "originalLanguage": {"name": language}}
        for i, (path, language) in enumerate(items, 1)
    ]
    return arr


def indexed(*items: tuple[str, str]) -> dict:
    return path_index([stub_arr(*items)])


def test_the_index_keys_a_title_by_its_folder():
    index = indexed(("/data/media/movies/Film (2024)", "Korean"))
    assert set(index) == {"/data/media/movies/Film (2024)"}

    (item,) = index.values()
    assert (item.lang, item.item_id, item.arr.name) == ("kor", 1, "radarr")


@pytest.mark.parametrize(
    ("path", "found"),
    [
        ("/data/media/movies/Film/Film.mkv", True),
        # The title's own folder, not a file inside it.
        ("/data/media/movies/Film", True),
        # Deeper: Sonarr keeps season folders under the series.
        ("/data/media/movies/Film/Extras/Deleted/scene.mkv", True),
        ("/data/media/movies/Other/Other.mkv", False),
        ("/elsewhere/f.mkv", False),
        # A sibling that merely shares a prefix. Matching on directory
        # boundaries is what keeps Film2 out of Film.
        ("/data/media/movies/Film2/f.mkv", False),
    ],
)
def test_match_path_finds_the_title_containing_a_file(path, found):
    index = indexed(("/data/media/movies/Film", "Korean"))
    assert (match_path(index, path) is not None) is found


def test_match_path_prefers_the_innermost_title():
    """One title's folder inside another's: the file belongs to the nearest,
    which is what walking outwards from the file buys."""
    index = indexed(
        ("/data/media/movies", "English"),
        ("/data/media/movies/Film", "Korean"),
    )
    assert match_path(index, "/data/media/movies/Film/f.mkv").lang == "kor"
    assert match_path(index, "/data/media/movies/Loose.mkv").lang == "eng"


def test_an_unindexed_path_never_walks_past_the_root():
    """A file under no title at all has to end as None rather than looping
    forever on a root whose parent is itself."""
    assert match_path(indexed(), "/f.mkv") is None
    assert match_path(indexed(), "relative.mkv") is None


def test_a_trailing_slash_on_a_reported_folder_is_ignored():
    """It would otherwise be keyed under a name no file's parent ever spells."""
    index = indexed(("/data/media/movies/Film/", "Korean"))
    assert match_path(index, "/data/media/movies/Film/f.mkv").lang == "kor"


@pytest.mark.parametrize("folder", ["/", ""])
def test_a_title_folder_that_is_no_folder_is_not_indexed(folder):
    """The root would claim every file in the library, since every path is
    inside it."""
    assert indexed((folder, "Korean")) == {}


def test_the_first_arr_wins_a_folder_they_both_claim():
    """Radarr and Sonarr pointed at one directory is a misconfiguration, but
    it has to resolve the same way every sweep rather than by insertion luck."""
    index = path_index(
        [
            stub_arr(("/data/media", "Korean")),
            stub_arr(("/data/media", "English"), name="sonarr"),
        ]
    )
    assert match_path(index, "/data/media/f.mkv").arr.name == "radarr"
