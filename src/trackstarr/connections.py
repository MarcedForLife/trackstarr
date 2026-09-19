"""Whether the services trackstarr talks to are reachable and answering.

Each check calls the service's status endpoint, never cached. An *arr is also
asked to call our webhook, since a connection saved inside it proves nothing
about whether that address resolves where it runs. It takes the address and key
to use, so the page can test an edit before saving, and answers with one line a
reader can act on. A refused API key is a different job from nothing listening
there.
"""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import config
from .arr import WebhookState, radarr, sonarr, source_label, webhook_url
from .client import API_ERRORS, refused_reason, request
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
    name = data.get("instanceName") or data.get("appName") or "The service"
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


def service_for(name: str) -> Service | None:
    if name in BY_NAME:
        service = BY_NAME[name]
        return replace(service, label=source_label(name)) if name in _ARR_FACTORIES else service
    prefix = name.upper().replace("-", "_")
    if not config.arr_setting(prefix + "_URL"):
        return None
    kind, instance = name.split("-", 1) if "-" in name else ("", "")
    if kind not in ("radarr", "sonarr") or not instance.isalnum():
        return None
    return replace(
        BY_NAME[kind],
        name=name,
        label=source_label(name),
        url_name=prefix + "_URL",
        key_name=prefix + "_API_KEY",
    )


#: The *arrs, which are also asked whether they hold our webhook.
_ARR_FACTORIES = {"radarr": radarr, "sonarr": sonarr}


@dataclass(frozen=True)
class Result:
    """One check's answer, as the page shows it."""

    ok: bool
    detail: str
    #: Something to do about it, when there is.
    hint: str = ""
    #: For an *arr: connected, unreachable, stale, missing, or unknown when it
    #: could not be asked. Empty for the rest.
    webhook: str = ""
    #: What the *arr said when it could not call us. Only for unreachable.
    webhook_detail: str = ""
    #: Root diagnostics: ready, attention, unknown, or empty for media servers.
    paths: str = ""


def _headers(header: str, key: str) -> dict:
    # Plex answers XML without the Accept header, and the others ignore it.
    return {header: key, "Accept": "application/json"}


def _value(name: str) -> str:
    return config.value(name)


def configured(service: Service) -> bool:
    """Whether both address and key are set. One alone leaves the service off."""
    return bool(_value(service.url_name) and _value(service.key_name))


def _explain(err: Exception) -> str:
    """Whatever urllib raised, as one line a reader can act on."""
    code = getattr(err, "code", None)
    if code in (401, 403):
        return "Received a forbidden response (API key)."
    if code == 404:
        return (
            "Received a not found response. Check the address is the service's base URL, "
            "path prefix included."
        )
    if code:
        return refused_reason(err, code)
    if isinstance(err, json.JSONDecodeError):
        return (
            "The service returned an unexpected response. Check the address points at its API."
        )
    return f"Could not reach the service, {getattr(err, 'reason', None) or err}."


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


def _webhook_state(name: str, url: str, key: str) -> WebhookState:
    """Whether the *arr at these values will call us."""
    arr = replace(_ARR_FACTORIES[name.split("-", 1)[0]](), name=name, url=url, key=key)
    try:
        return arr.webhook_status(webhook_url())
    except API_ERRORS as err:
        log.debug("%s: could not ask about the webhook connection (%s)", name, err)
        return WebhookState("unknown")


def _arr_path_hint(url: str, key: str) -> tuple[str, str]:
    """Check every reported root in our filesystem, using this check's credentials."""
    try:
        roots = request(f"{url}/api/v3/rootfolder", _headers("X-Api-Key", key), timeout=TIMEOUT)
    except API_ERRORS:
        return (
            (
                "Library paths could not be checked. "
                "Retry Test when the root folders API is available."
            ),
            "unknown",
        )
    if not isinstance(roots, list):
        return (
            (
                "Library paths could not be checked: "
                "the root folders API returned an unexpected response."
            ),
            "unknown",
        )
    paths = [root.get("path") for root in roots if isinstance(root, dict) and root.get("path")]
    if not paths:
        return "No library root folders are configured in this instance.", "attention"
    results = []
    ready = 0
    for raw in paths:
        path = str(raw)
        visible = os.path.isdir(path)
        covered = os.path.isabs(path) and any(
            path_within(os.path.normpath(path), os.path.normpath(base).rstrip("/"))
            for base in config.current().MEDIA_DIRS
        )
        visibility = (
            "visible as a directory to Trackstarr"
            if visible
            else (
                "not visible as a directory to Trackstarr; "
                "check container mounts and permissions"
            )
        )
        coverage = (
            "inside MEDIA_DIRS"
            if covered
            else "outside MEDIA_DIRS; add this library root to MEDIA_DIRS for sweeps"
        )
        ready += visible and covered
        results.append(f"{path}: {visibility}; {coverage}.")
    return (
        f"Library roots: {ready} of {len(paths)} visible and inside MEDIA_DIRS.\n"
        + "\n".join(results),
        "ready" if ready == len(paths) else "attention",
    )


def check(name: str, url: str = "", key: str = "") -> Result:
    """Ask one service whether it is there. An empty ``url`` or ``key`` falls
    back to the saved value, which is how an untouched password field
    travels."""
    service = service_for(name)
    if service is None:
        raise KeyError(name)
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
    webhook = (
        _webhook_state(name, url, key)
        if name.split("-", 1)[0] in _ARR_FACTORIES
        else WebhookState("")
    )
    paths = ""
    if name.split("-", 1)[0] in _ARR_FACTORIES:
        hint, paths = _arr_path_hint(url, key)
    return Result(True, service.describe(answer), hint, webhook.state, webhook.detail, paths)
