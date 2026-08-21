"""Plex and Jellyfin nudges, to refresh their view of a rewritten file.

Both normally notice changes through their own filesystem watchers, which
see nothing on a network mount.

Best effort by contract: nothing here may fail the job that fixed the file.
A server failing repeatedly is muted for the rest of the process, and a
missed nudge heals on the next scheduled scan.
"""

import logging
import os
import urllib.parse

from . import config
from .client import request

log = logging.getLogger(__name__)


#: Generous for a LAN nudge nothing waits on. A hung server costs this per
#: attempt until muting kicks in.
_TIMEOUT = 10

#: Consecutive failures per server before it is left alone until restart.
_MUTE_AFTER = 3
_failures: dict[str, int] = {}

#: Servers already told that our paths land outside everything they index, so
#: a sweep full of misses costs one line rather than one per file.
_unmapped: set[str] = set()


def _plex_enabled() -> bool:
    return bool(config.PLEX_URL and config.PLEX_TOKEN)


def _plex_headers() -> dict:
    return {"X-Plex-Token": config.PLEX_TOKEN, "Accept": "application/json"}


#: Section locations per (url, token). Static server config, so fetched once
#: per process rather than once per fixed file.
_plex_sections: dict[tuple[str, str], list[tuple[str, str]]] = {}


def _plex_locations() -> list[tuple[str, str]]:
    """``(location, section key)`` pairs, longest first, so the first
    prefix match is the most specific."""
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


def path_within(path: str, base: str) -> bool:
    """True when path is base itself or inside it, on directory boundaries.

    Lexical, which is right because Plex reports these locations and they
    need not exist here. Callers pass bases with no trailing slash; the
    trailing-slash join is what keeps /data/media off /data/media2.
    """
    return path == base or path.startswith(base + "/")


def map_path(path: str, mapping: list[tuple[str, str]]) -> str:
    """``path`` as the server spells it, per the longest matching prefix.

    An empty mapping, or a path under none of its pairs, is passed through:
    the usual case is mounts that already agree.
    """
    for local, remote in mapping:
        if path_within(path, local):
            return remote + path[len(local) :]
    return path


def _warn_unmapped(server: str, folder: str, known: list[str], setting: str) -> None:
    """Say once that this server indexes nothing we send it.

    The mismatch is otherwise invisible: refreshes are best effort, so a
    library the server spells differently just never updates, and the file
    that looks wrong in the app looks right on disk.
    """
    if server in _unmapped:
        log.debug("%s: nothing indexes %s", server, folder)
        return
    _unmapped.add(server)
    # A ready-to-paste pair when both halves are known, since the fix is
    # otherwise three facts the reader has to assemble.
    both_halves_known = config.MEDIA_DIRS and known
    example = f"{config.MEDIA_DIRS[0]}={known[0]}" if both_halves_known else "LOCAL=REMOTE"
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
    """Partial-scan the innermost library section holding the file.

    Plex has no "this one file changed" endpoint. The closest is a
    path-scoped refresh of the owning section.
    """
    folder = map_path(os.path.dirname(path), config.PLEX_PATH_MAP)
    locations = _plex_locations()
    section = next((key for location, key in locations if path_within(folder, location)), None)
    if section is None:
        _warn_unmapped("plex", folder, [location for location, _ in locations], "PLEX_PATH_MAP")
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
    """Tell Jellyfin (or Emby, same API) exactly which file changed.

    Nothing comes back saying whether it knew the path, so a mapping is the
    only thing standing between a different mount and a silent no-op.
    """
    mapped = map_path(path, config.JELLYFIN_PATH_MAP)
    request(
        f"{config.JELLYFIN_URL}/Library/Media/Updated",
        {"X-Emby-Token": config.JELLYFIN_API_KEY},
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
