"""Plex and Jellyfin refresh against faked HTTP. No network."""

import urllib.error

import pytest

from conftest import set_config
from trackstarr import media_server
from trackstarr.media_server import refresh_servers, server_status

PLEX_SECTIONS = {
    "MediaContainer": {
        "Directory": [
            {"key": "1", "Location": [{"path": "/data/media"}]},
            {"key": "2", "Location": [{"path": "/data/media/movies"}]},
        ]
    }
}


@pytest.fixture(autouse=True)
def _fresh_state():
    """Module-level caches must not leak between tests."""
    media_server.reset()
    yield
    media_server.reset()


@pytest.fixture
def plex():
    set_config(PLEX_URL="http://plex:32400")
    set_config(PLEX_TOKEN="token")


@pytest.fixture
def jellyfin():
    set_config(JELLYFIN_URL="http://jellyfin:8096")
    set_config(JELLYFIN_API_KEY="key")


@pytest.fixture
def requests(monkeypatch):
    """Record (url, headers, payload) and serve the Plex section list to GETs."""
    recorded = []

    def fake_request(url, headers=None, payload=None, timeout=30, method=None):
        recorded.append((url, headers, payload))
        return PLEX_SECTIONS if url.endswith("/library/sections") else None

    monkeypatch.setattr(media_server, "request", fake_request)
    return recorded


def test_jellyfin_names_the_exact_file(jellyfin, requests):
    refresh_servers("/data/media/movies/A/A.mkv")

    url, headers, payload = requests[-1]
    assert url == "http://jellyfin:8096/Library/Media/Updated"
    assert headers["X-Emby-Token"] == "key"
    assert payload["Updates"] == [
        {"Path": "/data/media/movies/A/A.mkv", "UpdateType": "Modified"}
    ]


def test_plex_refreshes_the_most_specific_section(plex, requests):
    refresh_servers("/data/media/movies/A/A.mkv")

    url, headers, payload = requests[-1]
    # Section 2 (/data/media/movies) wins over section 1 (/data/media).
    assert url.startswith("http://plex:32400/library/sections/2/refresh?")
    assert "path=%2Fdata%2Fmedia%2Fmovies%2FA" in url
    assert headers["X-Plex-Token"] == "token"
    assert payload is None


def test_plex_section_list_is_fetched_once(plex, requests):
    refresh_servers("/data/media/movies/A/A.mkv")
    refresh_servers("/data/media/movies/B/B.mkv")

    section_gets = [url for url, *_ in requests if url.endswith("/library/sections")]
    refreshes = [url for url, *_ in requests if "/refresh?" in url]
    assert len(section_gets) == 1
    assert len(refreshes) == 2


def test_plex_outside_any_section_does_nothing(plex, requests):
    refresh_servers("/elsewhere/f.mkv")
    assert [url for url, *_ in requests] == ["http://plex:32400/library/sections"]


def test_plex_maps_our_paths_to_the_ones_it_indexes(plex, requests):
    """The server mounts the library somewhere else, which is the usual case
    once trackstarr runs in a container and Plex doesn't."""
    set_config(PLEX_PATH_MAP=[("/library", "/data/media")])
    refresh_servers("/library/movies/A/A.mkv")

    url, *_ = requests[-1]
    assert url.startswith("http://plex:32400/library/sections/2/refresh?")
    assert "path=%2Fdata%2Fmedia%2Fmovies%2FA" in url


def test_jellyfin_maps_our_paths_too(jellyfin, requests):
    set_config(JELLYFIN_PATH_MAP=[("/library", "/media")])
    refresh_servers("/library/movies/A/A.mkv")

    *_, payload = requests[-1]
    assert payload["Updates"] == [{"Path": "/media/movies/A/A.mkv", "UpdateType": "Modified"}]


def test_plex_says_once_that_it_indexes_none_of_our_paths(plex, requests, caplog):
    """The mismatch is otherwise invisible: refreshes are best effort, so the
    library just never updates. Once, though, not once per file in a sweep."""
    for name in "ABC":
        refresh_servers(f"/elsewhere/{name}/{name}.mkv")

    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "PLEX_PATH_MAP" in warnings[0].getMessage()
    # The locations it does index, so the reader can write the mapping.
    assert "/data/media" in warnings[0].getMessage()


def test_unconfigured_servers_make_no_calls(requests):
    refresh_servers("/data/media/movies/A/A.mkv")
    assert requests == []


def test_repeated_failures_mute_a_server(jellyfin, monkeypatch):
    attempts = []

    def refuse(url, headers=None, payload=None, timeout=30, method=None):
        attempts.append(url)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(media_server, "request", refuse)
    for _ in range(5):
        refresh_servers("/data/media/movies/A/A.mkv")
    # Three attempts, then the server is left alone until restart.
    assert len(attempts) == 3


def test_unexpected_errors_never_escape(plex, monkeypatch):
    """The best-effort contract covers more than the enumerated API errors."""

    def broken(url, headers=None, payload=None, timeout=30, method=None):
        return "unauthorized"

    monkeypatch.setattr(media_server, "request", broken)
    refresh_servers("/data/media/movies/A/A.mkv")


def test_server_status_reports_each_server_for_the_startup_summary():
    """serve logs this, so a misconfigured token shows as "off" at boot rather
    than as refreshes that silently never happen."""
    set_config(PLEX_URL="http://plex:32400")
    set_config(PLEX_TOKEN="token")
    set_config(JELLYFIN_URL="")
    set_config(JELLYFIN_API_KEY="")
    assert server_status() == {"plex": True, "jellyfin": False}


def test_map_path_takes_the_first_pair_that_matches():
    """The pairs are sorted longest-local-first, so walking past one that
    doesn't match is the normal case for a library of more than one mount."""
    mapping = [("/data/media/tv", "/srv/tv"), ("/data/media", "/srv/media")]
    movie = media_server.map_path("/data/media/movies/A.mkv", mapping)
    assert movie == "/srv/media/movies/A.mkv"
    assert media_server.map_path("/data/media/tv/S/E.mkv", mapping) == "/srv/tv/S/E.mkv"
    assert media_server.map_path("/elsewhere/A.mkv", mapping) == "/elsewhere/A.mkv"
