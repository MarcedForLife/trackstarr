"""Poster art: fetched from an *arr or found beside the files, then kept."""

import os
import threading
import time

import pytest

from conftest import cache, movie, pending, stub_arrs
from trackstarr import covers, library


@pytest.fixture(autouse=True)
def _cold_index():
    """The shelf these tests fetch through is memoised, and so is the record
    of which titles nothing had a poster for."""
    library.forget()
    covers.forget()
    yield
    library.forget()
    covers.forget()


def test_a_cover_comes_from_the_arr_that_cached_it(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    asked: list[str] = []

    def answer(url, headers=None, timeout=30):
        asked.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(covers, "fetch", answer)
    body, kind = covers.cover("arr:radarr:1")
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

    monkeypatch.setattr(covers, "fetch", answer)
    assert covers.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert covers.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert len(asked) == 1


def test_a_png_kept_on_disk_is_still_a_png(media, monkeypatch):
    """The type is sniffed back out of the stored bytes, so local artwork
    does not come back claiming to be a jpeg."""
    folder = os.path.join(media, "Loose Film")
    os.makedirs(folder)
    with open(os.path.join(folder, "poster.png"), "wb") as art:
        art.write(b"\x89PNG\r\n\x1a\nlocal")
    stub_arrs(monkeypatch, [])
    cache((f"{folder}/film.mkv", pending()))
    covers.cover(f"dir:{folder}")
    assert covers.cover(f"dir:{folder}")[1] == "image/png"


def test_a_title_with_no_poster_is_only_looked_for_once(media, monkeypatch):
    """Otherwise a shelf of unclaimed folders re-asks both *arrs on every
    load, and each ask is a connection that has to time out."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    tries: list[int] = []

    def refuse(url, headers=None, timeout=30):
        tries.append(1)
        raise OSError("no such poster")

    monkeypatch.setattr(covers, "fetch", refuse)
    assert covers.cover("arr:radarr:1") is None
    assert covers.cover("arr:radarr:1") is None
    # Both poster sizes on the first ask, and nothing on the second.
    assert len(tries) == len(covers._COVER_NAMES)


def test_a_settings_save_looks_again_for_the_posters_nothing_had(media, monkeypatch):
    """The *arr the title needed may have been unset or down at the time."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    monkeypatch.setattr(
        covers, "fetch", lambda url, headers=None, timeout=30: (b"", "image/jpeg")
    )
    assert covers.cover("arr:radarr:1") is None
    covers.forget()
    monkeypatch.setattr(
        covers, "fetch", lambda url, headers=None, timeout=30: (b"\xff\xd8jpeg", "image/jpeg")
    )
    assert covers.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"


def test_warming_fetches_the_posters_the_grid_has_not_got(media, monkeypatch):
    stub_arrs(
        monkeypatch, [movie(1, "Dune", f"{media}/Dune"), movie(2, "Arrival", f"{media}/A")]
    )
    asked: list[str] = []

    def answer(url, headers=None, timeout=30):
        asked.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(covers, "fetch", answer)
    covers._warm_all()
    assert len(asked) == 2
    # And the grid that follows reads them off our disk rather than re-asking.
    assert covers.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
    assert len(asked) == 2
    # As does the next warm-up: a settled library asks for nothing at all.
    covers._warm_all()
    assert len(asked) == 2


def _warm_up_finished() -> bool:
    """Wait for the background warm-up to let go of its lock."""
    for _ in range(500):
        if covers._warming.acquire(blocking=False):
            covers._warming.release()
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

    monkeypatch.setattr(covers, "_warm_all", slow_walk)
    covers.warm()
    assert walking.wait(5), "the caller was not made to wait for it"
    covers.warm()
    let_go.set()

    assert _warm_up_finished()
    assert walks == [1]


def test_a_warm_up_that_fails_says_so_and_lets_the_next_one_run(media, monkeypatch, caplog):
    """A background thread has nobody to raise at. The grid still works, it
    just fetches its own posters one request at a time, and the next warm-up
    must not find the lock stuck held by this one."""

    def boom():
        raise RuntimeError("the cache went away")

    monkeypatch.setattr(covers, "_warm_all", boom)
    covers.warm()

    assert _warm_up_finished()
    assert "could not warm the poster cache" in caplog.text


def test_an_answer_that_is_not_a_picture_falls_through_to_the_next_name(media, monkeypatch):
    """An *arr with no poster cached answers the request rather than 404ing
    it, so the content type is the only thing that says whether what came
    back is an image."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])

    def answer(url, headers=None, timeout=30):
        if url.endswith("poster-500.jpg"):
            return b"<html>", "text/html"
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(covers, "fetch", answer)
    assert covers.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"


def test_a_poster_that_cannot_be_kept_is_still_served(media, monkeypatch, caplog):
    """An unwritable state dir costs the cache, not the picture."""
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])
    monkeypatch.setattr(
        covers, "fetch", lambda url, headers=None, timeout=30: (b"\xff\xd8jpeg", "image/jpeg")
    )

    def refuse(partial, path):
        raise OSError("read-only file system")

    monkeypatch.setattr(covers.os, "replace", refuse)
    assert covers.cover("arr:radarr:1")[0] == b"\xff\xd8jpeg"
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
    cache((f"{folder}/film.mkv", pending()))

    assert covers.cover(f"dir:{folder}") == (b"local", "image/jpeg")


def test_a_cover_falls_back_to_artwork_beside_the_files(media, monkeypatch):
    folder = os.path.join(media, "Loose Film")
    os.makedirs(folder)
    with open(os.path.join(folder, "poster.jpg"), "wb") as art:
        art.write(b"local")
    stub_arrs(monkeypatch, [])
    cache((f"{folder}/film.mkv", pending()))
    assert covers.cover(f"dir:{folder}") == (b"local", "image/jpeg")


def test_a_cover_nobody_has_is_no_cover(media, monkeypatch):
    stub_arrs(monkeypatch, [movie(1, "Dune", f"{media}/Dune")])

    def refuse(url, headers=None, timeout=30):
        raise OSError("no such poster")

    monkeypatch.setattr(covers, "fetch", refuse)
    assert covers.cover("arr:radarr:1") is None
