"""Whether the services trackstarr talks to are reachable and answering.

Each check is one read-only call to the service's status endpoint, never
cached. It takes the address and key to use, so the page can test an edit
before saving, and answers with one line a reader can act on: "it refused the
API key" is a different job from "nothing is listening there".
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import config
from .arr import radarr, sonarr, webhook_url
from .client import API_ERRORS, request
from .media_server import PLEX_SECTIONS, map_path, path_within, plex_sections

log = logging.getLogger(__name__)

#: A LAN service that has not answered in ten seconds is not answering.
TIMEOUT = 10


@dataclass(frozen=True)
class Service:
    """One service the settings name, and how to ask whether it is there."""

    name: str
    label: str
    url_name: str
    key_name: str
    #: The header the credential travels in; all three spell it differently.
    header: str
    #: A path whose answer proves the address and the credential; a public
    #: endpoint would call a wrong key good.
    probe: str
    #: That answer as one line, ready to show.
    describe: Callable[[dict | None], str]
    #: The path map setting, for servers that index the library themselves.
    map_name: str = ""
    #: The library locations the service indexes, given the probe's answer and
    #: the address and key. Empty for the *arrs, which are told each path.
    locations: Callable[[dict | None, str, str], list[str]] = lambda answer, url, key: []


def _arr_version(answer: dict | None) -> str:
    """Radarr and Sonarr both answer /system/status with their own name."""
    data = answer or {}
    name = data.get("instanceName") or data.get("appName") or "It"
    return f"{name} {data.get('version') or 'answered'}".strip()


def _plex_version(answer: dict | None) -> str:
    container = (answer or {}).get("MediaContainer") or {}
    count = len(plex_sections(answer))
    return f"Plex, {count} librar{'y' if count == 1 else 'ies'}{_plex_name(container)}"


def _plex_name(container: dict) -> str:
    return f" on {container['title1']}" if container.get("title1") else ""


def _plex_locations(answer: dict | None, url: str, key: str) -> list[str]:
    return [location for location, _ in plex_sections(answer)]


def _jellyfin_version(answer: dict | None) -> str:
    data = answer or {}
    return f"{data.get('ServerName') or 'Jellyfin'} {data.get('Version') or ''}".strip()


def _jellyfin_locations(answer: dict | None, url: str, key: str) -> list[str]:
    """The folders Jellyfin indexes. Best effort: a key that cannot read the
    layout still refreshes fine, so a failure costs only the hint."""
    try:
        folders = request(
            f"{url}/Library/VirtualFolders", _headers("X-Emby-Token", key), timeout=TIMEOUT
        )
    except API_ERRORS as err:
        log.debug("jellyfin: could not read the library layout (%s)", err)
        return []
    return [
        location.rstrip("/")
        for folder in folders or []
        for location in folder.get("Locations") or []
        if location
    ]


SERVICES: tuple[Service, ...] = (
    Service(
        "radarr",
        "Radarr",
        "RADARR_URL",
        "RADARR_API_KEY",
        "X-Api-Key",
        "/api/v3/system/status",
        _arr_version,
    ),
    Service(
        "sonarr",
        "Sonarr",
        "SONARR_URL",
        "SONARR_API_KEY",
        "X-Api-Key",
        "/api/v3/system/status",
        _arr_version,
    ),
    Service(
        "plex",
        "Plex",
        "PLEX_URL",
        "PLEX_TOKEN",
        "X-Plex-Token",
        # The sections list doubles as the check: a bad token is refused.
        PLEX_SECTIONS,
        _plex_version,
        map_name="PLEX_PATH_MAP",
        locations=_plex_locations,
    ),
    Service(
        "jellyfin",
        "Jellyfin",
        "JELLYFIN_URL",
        "JELLYFIN_API_KEY",
        "X-Emby-Token",
        "/System/Info",
        _jellyfin_version,
        map_name="JELLYFIN_PATH_MAP",
        locations=_jellyfin_locations,
    ),
)

BY_NAME = {service.name: service for service in SERVICES}

#: The *arrs, which are also asked whether they hold our webhook.
_ARR_FACTORIES = {"radarr": radarr, "sonarr": sonarr}


@dataclass(frozen=True)
class Result:
    """One check's answer, as the page shows it."""

    ok: bool
    detail: str
    #: Something to do about it, when there is.
    hint: str = ""
    #: For an *arr: connected, stale, missing, or unknown when it could not be
    #: asked. Empty for the rest.
    webhook: str = ""


def _headers(header: str, key: str) -> dict:
    # Plex answers XML without the Accept header, and the others ignore it.
    return {header: key, "Accept": "application/json"}


def _value(name: str) -> str:
    return str(getattr(config.current(), name, "") or "")


def configured(service: Service) -> bool:
    """Whether both address and key are set. One alone leaves the service off."""
    return bool(_value(service.url_name) and _value(service.key_name))


def _explain(err: Exception) -> str:
    """Whatever urllib raised, as one line a reader can act on."""
    code = getattr(err, "code", None)
    if code in (401, 403):
        return "It answered, but refused the API key."
    if code == 404:
        return (
            "Something answered, but not this service's API. Check the address is its base "
            "URL, path prefix included."
        )
    if code:
        return f"It answered {code}."
    if isinstance(err, json.JSONDecodeError):
        return "Something answered, but not with JSON. Check the address points at the service."
    return f"Could not reach it: {getattr(err, 'reason', None) or err}."


def _path_hint(service: Service, locations: list[str]) -> str:
    """A hint when this server indexes nothing we would send it. Otherwise
    invisible: refreshes are best effort, so a mismatched library never
    updates."""
    settings = config.current()
    mapping = getattr(settings, service.map_name, [])
    ours = [map_path(media_dir, mapping) for media_dir in settings.MEDIA_DIRS]
    if not locations or not ours:
        return ""
    # Either direction: a server may index the whole library or one folder in
    # it.
    if any(
        path_within(mine, theirs) or path_within(theirs, mine)
        for mine in ours
        for theirs in locations
    ):
        return ""
    return (
        f"It indexes {', '.join(locations)}, which nothing in MEDIA_DIRS "
        f"({', '.join(settings.MEDIA_DIRS)}) lands inside, so refreshes would be skipped. "
        f"Add a path map below, such as {ours[0]}={locations[0]}."
    )


def _webhook_state(name: str, url: str, key: str) -> str:
    """Whether the *arr at these values holds our connection."""
    arr = replace(_ARR_FACTORIES[name](), url=url, key=key)
    try:
        return arr.webhook_status(webhook_url())
    except API_ERRORS as err:
        log.debug("%s: could not read the connections list (%s)", name, err)
        return "unknown"


def check(name: str, url: str = "", key: str = "") -> Result:
    """Ask one service whether it is there. An empty ``url`` or ``key`` falls
    back to the saved value, which is how an untouched password field
    travels."""
    service = BY_NAME[name]
    url = (url or _value(service.url_name)).rstrip("/")
    key = key or _value(service.key_name)
    if not url or not key:
        missing = "address" if not url else "API key"
        return Result(False, f"No {missing} set, so {service.label} is switched off.")
    if not url.startswith(("http://", "https://")):
        return Result(False, "The address has to start with http:// or https://.")
    try:
        answer = request(
            f"{url}{service.probe}", _headers(service.header, key), timeout=TIMEOUT
        )
    except API_ERRORS as err:
        return Result(False, _explain(err))
    hint = _path_hint(service, service.locations(answer, url, key)) if service.map_name else ""
    webhook = _webhook_state(name, url, key) if name in _ARR_FACTORIES else ""
    return Result(True, service.describe(answer), hint, webhook)
