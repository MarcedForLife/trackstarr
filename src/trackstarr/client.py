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


#: Where a refused request's reason lives, best first. The *arrs use
#: ``message``; Jellyfin answers ASP.NET problem details, which use ``title``.
#: Plex is not here: it refuses in HTML.
_REFUSAL_KEYS = ("message", "title", "error")


def refusal(err: Exception) -> str:
    """The service's own words for an error it answered, or "" when it gave
    none worth showing."""
    body = getattr(err, "read", None)
    if body is None:
        return ""
    try:
        said = json.loads(body() or b"{}")
    # An already-read body raises ValueError, a dead connection OSError.
    except ValueError, OSError:
        return ""
    finally:
        # An HTTPError holds an open response until somebody closes it.
        with contextlib.suppress(OSError):
            err.close()  # type: ignore[attr-defined]
    if not isinstance(said, dict):
        return ""
    return next((str(said[key]) for key in _REFUSAL_KEYS if said.get(key)), "")


def refused_reason(err: Exception, code: int) -> str:
    """An error status as one line, carrying the service's own words where it
    gave any. 5xx is the service's own trouble, 4xx is the request's."""
    trouble = (
        f"The service responded with an internal error ({code})"
        if code >= 500
        else f"The service rejected the request ({code})"
    )
    said = refusal(err)
    return f'{trouble} and reported "{said}".' if said else f"{trouble}."


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
