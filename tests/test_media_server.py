"""Plex and Jellyfin refresh against faked HTTP. No network."""

from __future__ import annotations

import urllib.error

import pytest

from trackstarr import config, media_server
from trackstarr.media_server import refresh_servers

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
    media_server._plex_sections.clear()
    media_server._failures.clear()
    yield
    media_server._plex_sections.clear()
    media_server._failures.clear()


@pytest.fixture
def plex(monkeypatch):
    monkeypatch.setattr(config, "PLEX_URL", "http://plex:32400")
    monkeypatch.setattr(config, "PLEX_TOKEN", "token")


@pytest.fixture
def jellyfin(monkeypatch):
    monkeypatch.setattr(config, "JELLYFIN_URL", "http://jellyfin:8096")
    monkeypatch.setattr(config, "JELLYFIN_API_KEY", "key")


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
