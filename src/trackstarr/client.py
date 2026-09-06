"""The HTTP chokepoint for every service client: the *arrs, Plex, Jellyfin."""

import contextlib
import json
import urllib.request
from collections.abc import Iterator
from typing import IO

#: Everything request() can raise: urllib's errors subclass OSError, plus a
#: response that will not parse.
API_ERRORS = (OSError, json.JSONDecodeError)


@contextlib.contextmanager
def stream(url: str, timeout: int = 30) -> Iterator[IO[bytes]]:
    """The response as a file object, for a body too big to hold, such as
    IMDb's 45MB ratings dataset. Raises API_ERRORS."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        yield response


def fetch(url: str, headers: dict | None = None, timeout: int = 30) -> tuple[bytes, str]:
    """Raw body and content type, for cover art. Raises API_ERRORS."""
    req = urllib.request.Request(url, headers=dict(headers or {}))
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def request(
    url: str,
    headers: dict | None = None,
    payload: dict | None = None,
    timeout: int = 30,
    method: str | None = None,
):
    """JSON in, JSON out. Returns None for an empty response body."""
    headers = dict(headers or {})
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method or ("POST" if payload is not None else "GET"),
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else None
