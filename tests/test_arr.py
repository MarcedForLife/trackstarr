"""Webhook registration against a faked *arr API. No network."""

from __future__ import annotations

import urllib.error
from dataclasses import replace

from trackstarr.arr import WEBHOOK_NAME, radarr

URL = "http://trackstarr:8080"


def make_arr(
    existing: list[dict] | None = None, calls: list[tuple] | None = None, *, call=None
):
    """A Radarr client whose _call records requests and serves a canned notification list."""
    arr = replace(radarr(), url="http://radarr:7878", key="key")

    def fake_call(path, payload=None, timeout=30, method=None):
        calls.append((method or ("POST" if payload is not None else "GET"), path, payload))
        return existing if payload is None else {"id": 1}

    arr._call = call or fake_call
    return arr


def registration(url: str = URL, **overrides) -> dict:
    """A notification object as the *arr would report ours."""
    return {
        "id": 5,
        "name": WEBHOOK_NAME,
        "onDownload": True,
        "onUpgrade": True,
        "fields": [{"name": "url", "value": url}, {"name": "method", "value": 1}],
        **overrides,
    }


def test_creates_the_connection_when_absent():
    calls: list[tuple] = []
    arr = make_arr([{"id": 3, "name": "Discord"}], calls)
    assert arr.register_webhook(URL)

    method, path, payload = calls[-1]
    assert (method, path) == ("POST", "/api/v3/notification")
    assert payload["name"] == WEBHOOK_NAME
    assert payload["implementation"] == "Webhook"
    assert payload["onDownload"] and payload["onUpgrade"]
    assert {"name": "url", "value": URL} in payload["fields"]


def test_leaves_a_current_connection_alone():
    calls: list[tuple] = []
    arr = make_arr([registration()], calls)
    assert arr.register_webhook(URL)
    assert [recorded[0] for recorded in calls] == ["GET"]


def test_updates_a_stale_connection_in_place():
    calls: list[tuple] = []
    arr = make_arr([registration(url="http://old-name:9999")], calls)
    assert arr.register_webhook(URL)

    method, path, payload = calls[-1]
    assert (method, path) == ("PUT", "/api/v3/notification/5")
    assert payload["id"] == 5
    assert {"name": "url", "value": URL} in payload["fields"]


def test_updates_when_an_event_was_unticked():
    calls: list[tuple] = []
    arr = make_arr([registration(onUpgrade=False)], calls)
    assert arr.register_webhook(URL)
    assert calls[-1][0] == "PUT"


def test_reports_failure_for_retry_when_arr_is_down():
    def refuse(path, payload=None, timeout=30, method=None):
        raise urllib.error.URLError("connection refused")

    arr = make_arr(call=refuse)
    assert arr.register_webhook(URL) is False


def test_disabled_arr_needs_no_registration():
    arr = replace(radarr(), url="", key="")
    assert arr.register_webhook(URL) is True
