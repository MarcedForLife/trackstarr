"""Webhook registration against a faked *arr API. No network."""

import urllib.error
from dataclasses import replace

import pytest

from trackstarr import auth
from trackstarr.arr import AUTH_HEADER, WEBHOOK_NAME, _sent_secret, radarr

URL = "http://trackstarr:5120"

#: A value nothing minted, so it never verifies against a stored digest.
#: The default for the tests that are about anything but the credential.
UNMINTED = "s3cret"


def headers_field(secret: str) -> dict:
    return {"name": "headers", "value": [{"key": AUTH_HEADER, "value": secret}]}


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


def registration(url: str = URL, secret: str = UNMINTED, **overrides) -> dict:
    """A notification object as the *arr would report ours."""
    return {
        "id": 5,
        "name": WEBHOOK_NAME,
        "onDownload": True,
        "onUpgrade": True,
        "fields": [
            {"name": "url", "value": url},
            {"name": "method", "value": 1},
            headers_field(secret),
        ],
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
    # Whatever we sent is what the listener will accept.
    assert auth.authorized(_sent_secret(payload))


def test_leaves_a_current_connection_alone():
    calls: list[tuple] = []
    arr = make_arr([registration(secret=auth.mint("radarr"))], calls)
    assert arr.register_webhook(URL)
    assert [recorded[0] for recorded in calls] == ["GET"]


def test_registering_twice_rotates_nothing():
    """The digest kept at minting verifies the value we saved, so a restart
    finds its own connection current instead of re-registering forever."""
    first_calls: list[tuple] = []
    assert make_arr([], first_calls).register_webhook(URL)
    saved = first_calls[-1][2]

    second_calls: list[tuple] = []
    arr = make_arr([{**saved, "id": 5}], second_calls)
    assert arr.register_webhook(URL)
    assert [recorded[0] for recorded in second_calls] == ["GET"]


def test_updates_a_stale_connection_in_place():
    calls: list[tuple] = []
    stale = registration(url="http://old-name:9999", secret=auth.mint("radarr"))
    arr = make_arr([stale], calls)
    assert arr.register_webhook(URL)

    method, path, payload = calls[-1]
    assert (method, path) == ("PUT", "/api/v3/notification/5")
    assert payload["id"] == 5
    assert {"name": "url", "value": URL} in payload["fields"]


def test_updates_when_an_event_was_unticked():
    calls: list[tuple] = []
    arr = make_arr([registration(secret=auth.mint("radarr"), onUpgrade=False)], calls)
    assert arr.register_webhook(URL)
    assert calls[-1][0] == "PUT"


def test_replaces_a_secret_the_listener_would_reject():
    """Someone edited the header in the *arr's UI. We cannot read our own
    copy back to compare, so the check is against the digest, and anything
    that fails it is replaced rather than left to 401 every callback."""
    auth.mint("radarr")
    calls: list[tuple] = []
    arr = make_arr([registration(secret="rotated-away")], calls)
    assert arr.register_webhook(URL)

    method, _, payload = calls[-1]
    assert method == "PUT"
    assert auth.authorized(_sent_secret(payload))


def test_reprovisions_a_connection_after_a_wiped_state_dir():
    """The *arr still holds a secret, we hold no digest for it. The plaintext
    is unrecoverable, so the connection gets a freshly minted one."""
    calls: list[tuple] = []
    arr = make_arr([registration(secret="from-a-previous-install")], calls)
    assert arr.register_webhook(URL)

    method, _, payload = calls[-1]
    assert method == "PUT"
    assert auth.authorized(_sent_secret(payload))


def test_a_connection_without_our_header_is_replaced():
    calls: list[tuple] = []
    without_headers = registration()
    without_headers["fields"] = [{"name": "url", "value": URL}, {"name": "method", "value": 1}]
    arr = make_arr([without_headers], calls)
    assert arr.register_webhook(URL)
    assert calls[-1][0] == "PUT"


def test_reports_failure_for_retry_when_arr_is_down():
    def refuse(path, payload=None, timeout=30, method=None):
        raise urllib.error.URLError("connection refused")

    arr = make_arr(call=refuse)
    assert arr.register_webhook(URL) is False


def test_reports_failure_for_retry_when_the_secret_cannot_be_stored(monkeypatch):
    """A secret we cannot check later would be registered and then refused,
    so the *arr must keep its working connection until STATE_DIR is writable."""

    def cannot_store(name):
        raise OSError("read-only file system")

    monkeypatch.setattr(auth, "mint", cannot_store)
    calls: list[tuple] = []
    arr = make_arr([], calls)
    assert arr.register_webhook(URL) is False
    assert [recorded[0] for recorded in calls] == ["GET"]


def test_disabled_arr_needs_no_registration():
    arr = replace(radarr(), url="", key="")
    assert arr.register_webhook(URL) is True


@pytest.mark.parametrize(
    "value", [None, "not-a-list", [], [{"key": "X-Other", "value": "v"}], ["junk"]]
)
def test_a_malformed_headers_field_is_not_a_secret(value):
    """Read back from a foreign API, so a shape we did not save must come
    back as "no secret" rather than an exception in the register thread."""
    notification = registration()
    notification["fields"] = [{"name": "headers", "value": value}]
    assert _sent_secret(notification) == ""
