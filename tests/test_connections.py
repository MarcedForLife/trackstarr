"""The connections page's model: is it there, and does it hold our webhook.

Faked HTTP throughout. No network.
"""

import json
import types
import urllib.error

import pytest

from conftest import set_config
from trackstarr import connections
from trackstarr.arr import WEBHOOK_NAME

ARR_STATUS = {"appName": "Radarr", "instanceName": "Films", "version": "5.14.0"}
JELLYFIN_INFO = {"ServerName": "Attic", "Version": "10.9.11"}
PLEX_SECTIONS = {
    "MediaContainer": {
        "title1": "Attic",
        "Directory": [{"key": "1", "Location": [{"path": "/srv/media/movies"}]}],
    }
}
JELLYFIN_FOLDERS = [{"Name": "Films", "Locations": ["/srv/media/movies"]}]


def http_error(code: int) -> urllib.error.HTTPError:
    """A failing response, closed on the spot so its deallocator does not trip
    filterwarnings=error mid-test."""
    error = urllib.error.HTTPError("http://x", code, "no", {}, None)
    error.close()
    return error


@pytest.fixture
def answers(monkeypatch):
    """Serve a canned answer per endpoint, recording every request. An
    exception value is raised instead."""
    recorded: list[str] = []
    canned: dict[str, object] = {
        "/api/v3/system/status": ARR_STATUS,
        "/library/sections": PLEX_SECTIONS,
        "/System/Info": JELLYFIN_INFO,
        "/Library/VirtualFolders": JELLYFIN_FOLDERS,
        # Nothing of ours registered, which is the quiet default.
        "/api/v3/notification": [],
    }

    def fake_request(url, headers=None, payload=None, timeout=30, method=None):
        recorded.append(url)
        for path, answer in canned.items():
            if url.endswith(path):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise http_error(404)

    monkeypatch.setattr(connections, "request", fake_request)
    # The *arr client imported the same function into its own namespace.
    monkeypatch.setattr(
        "trackstarr.arr.request", lambda url, *args, **kwargs: fake_request(url)
    )
    # ``canned`` so a test can swap one endpoint's answer for a failure.
    return types.SimpleNamespace(urls=recorded, canned=canned)


@pytest.fixture
def library():
    set_config(MEDIA_DIRS=["/data/media/movies"])


def test_a_service_with_no_address_is_switched_off(answers):
    result = connections.check("radarr")
    assert not result.ok
    assert "No address set" in result.detail
    # Nothing was asked of the network to find that out.
    assert answers.urls == []


def test_a_service_with_no_key_says_which_half_is_missing(answers):
    set_config(RADARR_URL="http://radarr:7878")
    assert "No API key set" in connections.check("radarr").detail


def test_an_address_without_a_scheme_is_refused_before_the_call(answers):
    result = connections.check("plex", "plex:32400", "token")
    assert not result.ok
    assert "http://" in result.detail
    assert answers.urls == []


def test_a_reachable_arr_reports_its_name_and_version(answers):
    result = connections.check("radarr", "http://radarr:7878", "key")
    assert result.ok
    assert result.detail == "Films 5.14.0"
    assert answers.urls[0] == "http://radarr:7878/api/v3/system/status"


def test_the_saved_values_stand_in_for_what_the_page_left_out(answers):
    """An untouched password field is sent as nothing, and must not read as a
    key being cleared."""
    set_config(RADARR_URL="http://radarr:7878")
    set_config(RADARR_API_KEY="saved")
    assert connections.check("radarr", "http://elsewhere:7878", "").ok
    # The typed address, the stored key.
    assert answers.urls[0] == "http://elsewhere:7878/api/v3/system/status"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http_error(401), "refused the API key"),
        (http_error(403), "refused the API key"),
        (http_error(404), "not this service's API"),
        (http_error(500), "answered 500"),
        (urllib.error.URLError("Connection refused"), "Could not reach it"),
        (json.JSONDecodeError("no", "", 0), "not with JSON"),
    ],
)
def test_every_failure_says_which_one_it_was(answers, error, expected):
    answers.canned["/api/v3/system/status"] = error
    result = connections.check("sonarr", "http://sonarr:8989", "key")
    assert not result.ok
    assert expected in result.detail


def test_plex_counts_its_libraries_and_names_the_server(answers, library):
    result = connections.check("plex", "http://plex:32400", "token")
    assert result.ok
    assert result.detail == "Plex, 1 library on Attic"


def test_a_media_server_indexing_nothing_of_ours_says_so(answers, library):
    """The silent failure the path map exists for: refreshes are best effort,
    so a library the server spells differently just never updates."""
    result = connections.check("plex", "http://plex:32400", "token")
    assert result.ok
    assert "/srv/media/movies" in result.hint
    assert "/data/media/movies=/srv/media/movies" in result.hint


def test_a_path_map_that_already_lands_inside_leaves_no_hint(answers, library):
    set_config(PLEX_PATH_MAP=[("/data/media", "/srv/media")])
    assert connections.check("plex", "http://plex:32400", "token").hint == ""


def test_a_server_indexing_a_folder_inside_ours_agrees(answers):
    """Ours is the whole library, theirs one folder in it. Both spellings
    mean the same mount, so neither direction is a mismatch."""
    set_config(MEDIA_DIRS=["/srv/media"])
    assert connections.check("plex", "http://plex:32400", "token").hint == ""


def test_jellyfin_reads_its_library_layout_for_the_hint(answers, library):
    result = connections.check("jellyfin", "http://jellyfin:8096", "key")
    assert result.detail == "Attic 10.9.11"
    assert "/srv/media/movies" in result.hint


def test_a_jellyfin_key_that_cannot_read_the_layout_still_passes(answers, library):
    """The hint is best effort on top of a check that already succeeded."""
    answers.canned["/Library/VirtualFolders"] = http_error(403)
    result = connections.check("jellyfin", "http://jellyfin:8096", "key")
    assert result.ok
    assert result.hint == ""


def test_an_arr_says_whether_it_holds_our_webhook(answers):
    assert connections.check("radarr", "http://radarr:7878", "key").webhook == "missing"

    answers.canned["/api/v3/notification"] = [
        {
            "id": 1,
            "name": WEBHOOK_NAME,
            "fields": [{"name": "url", "value": "http://old/webhook"}],
        }
    ]
    assert connections.check("radarr", "http://radarr:7878", "key").webhook == "stale"


def test_an_arr_that_cannot_be_asked_about_its_webhook_says_unknown(answers):
    answers.canned["/api/v3/notification"] = http_error(500)
    result = connections.check("radarr", "http://radarr:7878", "key")
    # The service is there; only the connections list could not be read.
    assert result.ok
    assert result.webhook == "unknown"


def test_a_media_server_is_never_asked_about_a_webhook(answers, library):
    assert connections.check("jellyfin", "http://jellyfin:8096", "key").webhook == ""


def test_configured_needs_both_halves():
    service = connections.BY_NAME["sonarr"]
    assert not connections.configured(service)
    set_config(SONARR_URL="http://sonarr:8989")
    assert not connections.configured(service)
    set_config(SONARR_API_KEY="key")
    assert connections.configured(service)
