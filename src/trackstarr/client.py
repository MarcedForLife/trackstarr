"""The HTTP chokepoint for every service client: the *arrs, Plex, Jellyfin."""

import json
import urllib.request

#: Everything request() can raise: connection and HTTP failures, since
#: urllib.error.URLError subclasses OSError, plus a response that won't parse.
API_ERRORS = (OSError, json.JSONDecodeError)


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
