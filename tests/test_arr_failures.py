"""What each *arr call does when the *arr is down, or switched off.

Every one of these runs inside a sweep or a webhook import. A Radarr that is
restarting must degrade to "no library information" and let the rewrite go on
using what the file itself says, never abandon the walk.
"""

from dataclasses import replace

import pytest

from trackstarr import arr as arr_mod
from trackstarr.arr import Arr, all_arrs, original_of, radarr
from trackstarr.client import API_ERRORS


@pytest.fixture
def down(monkeypatch):
    """A reachable-looking Radarr whose every call fails."""

    def fail(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(arr_mod, "request", fail)
    return replace(radarr(), url="http://radarr:7878", key="key")


@pytest.fixture
def off():
    """A Radarr with no URL or key configured, as when the user runs Sonarr only."""
    return replace(radarr(), url="", key="")


def test_a_disabled_arr_is_never_called(off, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("a disabled arr must not reach the network")

    monkeypatch.setattr(arr_mod, "request", explode)
    assert off.enabled is False
    assert off.all_items() == []
    assert off.item(1) is None
    off.rescan(1)


def test_a_library_fetch_that_fails_yields_no_items(down):
    """Empty, not an exception: the sweep still has files to judge, it just
    cannot ask which language they were released in."""
    assert down.all_items() == []


def test_an_item_lookup_that_fails_yields_none(down):
    assert down.item(1) is None


def test_a_rescan_that_fails_is_swallowed(down):
    """The rewrite has already happened by this point. Failing to tell Radarr
    about it leaves stale metadata, which is not worth failing the import."""
    down.rescan(1)


def test_registration_reports_failure_so_it_is_retried(down):
    assert down.register_webhook("http://trackstarr:5120") is False


def test_the_call_carries_the_api_key(monkeypatch):
    seen = {}

    def record(url, headers=None, payload=None, timeout=30, method=None):
        seen.update(url=url, headers=headers, timeout=timeout, method=method)
        return [{"id": 1}]

    monkeypatch.setattr(arr_mod, "request", record)
    client = replace(radarr(), url="http://radarr:7878", key="secret-key")

    assert client.all_items() == [{"id": 1}]
    assert seen["url"] == "http://radarr:7878/api/v3/movie"
    assert seen["headers"] == {"X-Api-Key": "secret-key"}
    # The library listing is the one slow call, so it gets a longer timeout.
    assert seen["timeout"] == 120


def test_an_empty_library_response_is_a_list(monkeypatch):
    """The *arrs answer 200 with no body on an empty library."""
    monkeypatch.setattr(arr_mod, "request", lambda *a, **k: None)
    assert replace(radarr(), url="http://radarr:7878", key="k").all_items() == []


def test_all_arrs_reports_both_regardless_of_configuration():
    """The startup banner lists each with its on/off state, so both are always
    present and `enabled` is what varies."""
    assert [a.name for a in all_arrs()] == ["radarr", "sonarr"]
    assert all(isinstance(a, Arr) for a in all_arrs())


@pytest.mark.parametrize("item", [None, {}, {"originalLanguage": None}])
def test_original_of_missing_language_is_none(item):
    assert original_of(item) is None


def test_original_of_maps_a_real_language():
    """639-2/B, which is what Matroska and ffmpeg write: fre, not fra."""
    assert original_of({"originalLanguage": {"name": "French"}}) == "fre"


def test_an_unmapped_language_is_unknown_not_an_error(caplog):
    """Radarr grows new languages; an unrecognised one must not stop an import."""
    assert original_of({"originalLanguage": {"name": "Klingon"}}) is None
    assert "unmapped original language" in caplog.text


def test_oserror_is_an_api_error():
    """The fixtures above raise OSError to stand in for every transport
    failure, which only holds while API_ERRORS keeps covering it."""
    assert issubclass(OSError, API_ERRORS)


def test_a_save_that_fails_leaves_the_registration_to_be_retried(monkeypatch):
    """The listing succeeds and the save does not, which is the shape of a
    restart racing the *arr coming up. Reporting False is what schedules the
    retry; reporting True would leave the webhook permanently unregistered."""
    calls: list[str] = []

    def flaky(url, headers=None, payload=None, timeout=30, method=None):
        calls.append(url)
        if payload is None:
            return []
        raise OSError("connection reset")

    monkeypatch.setattr(arr_mod, "request", flaky)
    client = replace(radarr(), url="http://radarr:7878", key="key")

    assert client.register_webhook("http://trackstarr:5120") is False
    # It got as far as trying to save, rather than failing on the listing.
    assert len(calls) == 2
