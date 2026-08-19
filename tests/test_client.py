"""client.request against a real loopback server.

Every *arr and media-server call goes through request(), and the rest of
the suite replaces it wholesale, so nothing else exercises what it puts on
the wire. A real server rather than a faked urlopen, since a fake would be
checking the fake.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trackstarr.client import API_ERRORS, request


@pytest.fixture
def server():
    """A loopback HTTP server.

    Yields ``(url, state)``. ``state["received"]`` is the last request it
    saw; assigning ``state["response"]`` sets the ``(status, body)`` it
    replies with.
    """
    state: dict = {"received": None, "response": (200, b'{"ok": true}')}

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            state["received"] = {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(length) if length else b"",
            }
            status, body = state["response"]
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # BaseHTTPRequestHandler dispatches on the literal name do_<METHOD>,
        # so these cannot be renamed to satisfy N815.
        do_GET = do_POST = do_PUT = do_DELETE = _handle  # noqa: N815

        def log_message(self, fmt, *args) -> None:
            """Silence: pytest output is not an access log."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_no_payload_is_a_get(server):
    url, state = server
    assert request(f"{url}/api/v3/movie") == {"ok": True}
    received = state["received"]
    assert received["method"] == "GET"
    assert received["path"] == "/api/v3/movie"
    assert received["body"] == b""
    # Nothing was sent, so nothing should claim to be sending JSON.
    assert "Content-Type" not in received["headers"]


def test_a_payload_makes_it_a_json_post(server):
    url, state = server
    request(f"{url}/api/v3/notification", payload={"name": "trackstarr"})
    received = state["received"]
    assert received["method"] == "POST"
    assert received["headers"]["Content-Type"] == "application/json"
    assert json.loads(received["body"]) == {"name": "trackstarr"}


def test_an_explicit_method_beats_the_inference(server):
    """PUT with a body and DELETE without: both would be guessed wrong."""
    url, state = server
    request(f"{url}/api/v3/notification/5", payload={"id": 5}, method="PUT")
    assert state["received"]["method"] == "PUT"

    request(f"{url}/api/v3/notification/5", method="DELETE")
    assert state["received"]["method"] == "DELETE"


def test_an_empty_body_is_none_rather_than_a_parse_error(server):
    """A *arr DELETE answers 200 with nothing; json.loads("") would raise."""
    url, state = server
    state["response"] = (200, b"")
    assert request(f"{url}/api/v3/notification/5", method="DELETE") is None


def test_caller_headers_are_sent_and_left_alone(server):
    url, state = server
    headers = {"X-Api-Key": "k"}
    request(f"{url}/api/v3/movie", headers=headers, payload={"a": 1})

    assert state["received"]["headers"]["X-Api-Key"] == "k"
    # request() copies before adding Content-Type. Without that copy a
    # caller reusing one header dict would carry it into the next GET,
    # which has no body to describe.
    assert headers == {"X-Api-Key": "k"}


def test_an_http_error_is_an_api_error(server):
    """Callers catch API_ERRORS and treat the service as unavailable, so a
    failing response must land in there rather than escaping as its own type."""
    url, state = server
    state["response"] = (500, b"boom")
    with pytest.raises(API_ERRORS) as caught:
        request(f"{url}/api/v3/movie")
    # HTTPError is itself an open response, and urlopen raises it before
    # request() can enter its with-block, so nothing has closed it. Callers
    # drop it immediately and refcounting does the rest; close it here so the
    # deallocator does not run mid-test and trip filterwarnings=error.
    caught.value.close()


def test_unparseable_json_is_an_api_error(server):
    url, state = server
    state["response"] = (200, b"<html>not json</html>")
    with pytest.raises(API_ERRORS):
        request(f"{url}/api/v3/movie")
