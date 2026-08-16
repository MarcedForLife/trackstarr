"""Plex and Jellyfin nudges, to refresh their view of a rewritten file.

Both servers normally notice library changes through their own filesystem
watchers, which see nothing on a network mount. One nudge per rewritten file
keeps their track lists and sizes true.

Best effort by contract: nothing here may fail the job that fixed the file,
so refresh_servers catches everything, and a server that fails a few times
running is muted for the rest of the process instead of costing a timeout
per file. A missed nudge heals on the server's next scheduled scan.
"""

from __future__ import annotations

import logging
import os
import urllib.parse

from . import config
from .client import request
from .paths import path_within

log = logging.getLogger(__name__)

#: Generous for a LAN nudge nothing waits on; a hung (not refusing) server
#: costs this per attempt until muting kicks in.
_TIMEOUT = 10

#: Consecutive failures per server before it is left alone until restart.
_MUTE_AFTER = 3
_failures: dict[str, int] = {}


def _plex_enabled() -> bool:
    return bool(config.PLEX_URL and config.PLEX_TOKEN)


def _plex_headers() -> dict:
    return {"X-Plex-Token": config.PLEX_TOKEN, "Accept": "application/json"}


#: Section locations per (url, token): static server config, fetched once
#: per process rather than once per fixed file.
_plex_sections: dict[tuple[str, str], list[tuple[str, str]]] = {}


def _plex_locations() -> list[tuple[str, str]]:
    """``(location, section key)`` pairs, longest location first so the
    first prefix match is the most specific one."""
    cache_key = (config.PLEX_URL, config.PLEX_TOKEN)
    if cache_key not in _plex_sections:
        data = request(f"{config.PLEX_URL}/library/sections", _plex_headers(), timeout=_TIMEOUT)
        directories = ((data or {}).get("MediaContainer") or {}).get("Directory") or []
        locations = [
            (location["path"].rstrip("/"), str(directory.get("key")))
            for directory in directories
            for location in directory.get("Location") or []
            if location.get("path")
        ]
        locations.sort(key=lambda entry: -len(entry[0]))
        _plex_sections[cache_key] = locations
    return _plex_sections[cache_key]


def _plex_refresh(path: str) -> None:
    """Partial-scan the innermost library section containing the file.

    Plex has no "this one file changed" endpoint; the closest is a
    path-scoped refresh of the owning section.
    """
    folder = os.path.dirname(path)
    section = next(
        (key for location, key in _plex_locations() if path_within(folder, location)), None
    )
    if section is None:
        log.debug("plex: no library section contains %s", path)
        return
    query = urllib.parse.urlencode({"path": folder})
    request(
        f"{config.PLEX_URL}/library/sections/{section}/refresh?{query}",
        _plex_headers(),
        timeout=_TIMEOUT,
    )
    log.info("plex: refreshing %s", folder)


def _jellyfin_enabled() -> bool:
    return bool(config.JELLYFIN_URL and config.JELLYFIN_API_KEY)


def _jellyfin_refresh(path: str) -> None:
    """Tell Jellyfin (or Emby, same API) exactly which file changed."""
    request(
        f"{config.JELLYFIN_URL}/Library/Media/Updated",
        {"X-Emby-Token": config.JELLYFIN_API_KEY},
        payload={"Updates": [{"Path": path, "UpdateType": "Modified"}]},
        timeout=_TIMEOUT,
    )
    log.info("jellyfin: notified about %s", path)


#: name -> (configured?, refresh). Adding a server is one line here.
_SERVERS = {
    "plex": (_plex_enabled, _plex_refresh),
    "jellyfin": (_jellyfin_enabled, _jellyfin_refresh),
}


def server_status() -> dict[str, bool]:
    """name -> configured, for the startup summary."""
    return {name: enabled() for name, (enabled, _) in _SERVERS.items()}


def refresh_servers(path: str) -> None:
    """Nudge every configured media server about a rewritten file."""
    for name, (enabled, refresh) in _SERVERS.items():
        if not enabled() or _failures.get(name, 0) >= _MUTE_AFTER:
            continue
        try:
            refresh(path)
            _failures[name] = 0
        except Exception as err:
            failures = _failures[name] = _failures.get(name, 0) + 1
            if failures >= _MUTE_AFTER:
                log.warning(
                    "%s: %d refresh failures running, muting until restart (%s)",
                    name,
                    failures,
                    err,
                )
            else:
                log.warning("%s: refresh of %s failed (%s)", name, path, err)
