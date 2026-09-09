"""Plex and Jellyfin refreshes for a rewritten file, since their filesystem
watchers see nothing on a network mount.

Best effort: nothing here may fail the job that rewrote the file. A server
failing repeatedly is muted until restart, and a missed refresh heals on the
next scheduled scan.
"""

import logging
import os
import urllib.parse

from . import config
from .client import request

log = logging.getLogger(__name__)


#: Generous for a LAN call nothing waits on.
_TIMEOUT = 10

#: Consecutive failures per server before it is left alone until restart.
_MUTE_AFTER = 3
_failures: dict[str, int] = {}

#: Servers already warned that our paths land outside everything they index,
#: so a sweep of misses costs one line.
_unmapped: set[str] = set()


def _plex_enabled() -> bool:
    settings = config.current()
    return bool(settings.PLEX_URL and settings.PLEX_TOKEN)


def _plex_headers() -> dict:
    return {"X-Plex-Token": config.current().PLEX_TOKEN, "Accept": "application/json"}


#: Section locations per (url, token). Static, so fetched once per process.
_plex_sections: dict[tuple[str, str], list[tuple[str, str]]] = {}


#: Where both the refresh and the connections check read Plex's libraries.
PLEX_SECTIONS = "/library/sections"


def reset() -> None:
    """Forget the muted servers, the unmapped warnings and the fetched Plex
    sections. For tests: all three last until a restart."""
    _failures.clear()
    _unmapped.clear()
    _plex_sections.clear()


def plex_sections(data: dict | None) -> list[tuple[str, str]]:
    """``(location, section key)`` pairs from a Plex sections answer, longest
    location first. Parses without fetching so :mod:`trackstarr.connections`
    can use it without touching the cache."""
    directories = ((data or {}).get("MediaContainer") or {}).get("Directory") or []
    locations = [
        (location["path"].rstrip("/"), str(directory.get("key")))
        for directory in directories
        for location in directory.get("Location") or []
        if location.get("path")
    ]
    locations.sort(key=lambda entry: -len(entry[0]))
    return locations


def plex_locations() -> list[tuple[str, str]]:
    """:func:`plex_sections` for the configured server, fetched once. Public
    for :mod:`trackstarr.links`, so there is one cache to invalidate."""
    settings = config.current()
    cache_key = (settings.PLEX_URL, settings.PLEX_TOKEN)
    if cache_key not in _plex_sections:
        data = request(f"{settings.PLEX_URL}{PLEX_SECTIONS}", _plex_headers(), timeout=_TIMEOUT)
        _plex_sections[cache_key] = plex_sections(data)
    return _plex_sections[cache_key]


def path_within(path: str, base: str) -> bool:
    """Whether path is base or inside it, on directory boundaries. Lexical,
    since the server's locations need not exist here. The slash join keeps
    /data/media off /data/media2."""
    return path == base or path.startswith(base + "/")


def map_path(path: str, mapping: list[tuple[str, str]]) -> str:
    """``path`` as the server spells it, by the longest matching prefix. Passed
    through when nothing matches."""
    for local, remote in mapping:
        if path_within(path, local):
            return remote + path[len(local) :]
    return path


def _warn_unmapped(server: str, folder: str, known: list[str], setting: str) -> None:
    """Warn once that this server indexes nothing we send it. Otherwise
    invisible: refreshes are best effort, so a mismatched library never
    updates."""
    if server in _unmapped:
        log.debug("%s: nothing indexes %s", server, folder)
        return
    _unmapped.add(server)
    # A ready-to-paste pair when both halves are known.
    media_dirs = config.current().MEDIA_DIRS
    example = f"{media_dirs[0]}={known[0]}" if media_dirs and known else "LOCAL=REMOTE"
    log.warning(
        "%s: %s is outside everything it indexes (%s), so refreshes are being skipped; "
        "mount the library where it sees it, or set %s=%s",
        server,
        folder,
        ", ".join(known) or "nothing",
        setting,
        example,
    )


def _plex_refresh(path: str) -> None:
    """Path-scoped refresh of the section holding the file, the nearest thing
    Plex has to "this file changed"."""
    folder = map_path(os.path.dirname(path), config.current().PLEX_PATH_MAP)
    locations = plex_locations()
    section = next((key for location, key in locations if path_within(folder, location)), None)
    if section is None:
        _warn_unmapped("plex", folder, [location for location, _ in locations], "PLEX_PATH_MAP")
        return
    query = urllib.parse.urlencode({"path": folder})
    request(
        f"{config.current().PLEX_URL}/library/sections/{section}/refresh?{query}",
        _plex_headers(),
        timeout=_TIMEOUT,
    )
    log.info("plex: refreshing %s", folder)


def _jellyfin_enabled() -> bool:
    settings = config.current()
    return bool(settings.JELLYFIN_URL and settings.JELLYFIN_API_KEY)


def _jellyfin_refresh(path: str) -> None:
    """Tell Jellyfin (or Emby, same API) which file changed. Nothing says
    whether it knew the path, so a wrong mount is a silent no-op."""
    settings = config.current()
    mapped = map_path(path, settings.JELLYFIN_PATH_MAP)
    request(
        f"{settings.JELLYFIN_URL}/Library/Media/Updated",
        {"X-Emby-Token": settings.JELLYFIN_API_KEY},
        payload={"Updates": [{"Path": mapped, "UpdateType": "Modified"}]},
        timeout=_TIMEOUT,
    )
    log.info("jellyfin: notified about %s", mapped)


#: name -> (configured?, refresh). A new server is one line here.
_SERVERS = {
    "plex": (_plex_enabled, _plex_refresh),
    "jellyfin": (_jellyfin_enabled, _jellyfin_refresh),
}


def server_status() -> dict[str, bool]:
    """name -> configured, for the startup summary."""
    return {name: enabled() for name, (enabled, _) in _SERVERS.items()}


def refresh_servers(path: str) -> None:
    """Tell every configured media server about a rewritten file."""
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
