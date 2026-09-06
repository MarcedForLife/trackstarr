"""Links from a library title to the same title elsewhere: Plex or Jellyfin to
watch it, Radarr or Sonarr to manage it, IMDb to read about it.

The *arr and IMDb links are built from ids the title already carries. A media
server knows a title only by its own id, so it is asked which item lives at
our folder. Read-only and best effort: a service that is off, unreachable or
has no such item is a button not drawn.

Links use the ``*_PUBLIC_URL`` setting when set, since the address this
container calls (``http://plex:32400``) is rarely one a browser can reach.
"""

import functools
import logging
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from . import config
from .client import API_ERRORS, request
from .media_server import map_path, path_within, plex_locations

log = logging.getLogger(__name__)

#: Shorter than the refresh timeout: somebody is waiting on this one.
TIMEOUT = 8

#: How many of a prefix search's answers are worth confirming. "The" is a
#: prefix of sixty-five series.
MAX_CANDIDATES = 5

#: How long a resolved link is reused. An item's id does not change, and every
#: sheet opened would otherwise be two calls.
FOUND_TTL = 900.0

#: How long a miss is remembered. Short, since a server that was down also
#: answers with nothing.
MISSING_TTL = 60.0


@dataclass(frozen=True)
class Subject:
    """The title a link is wanted for.

    The folder, in this container's paths, is what everything is checked
    against. A media server is searched by name and year; the *arr and slug
    build its own link, and are empty for an unclaimed folder.
    """

    folder: str
    name: str = ""
    year: int | None = None
    arr: str = ""
    slug: str = ""
    #: The IMDb id the *arr carried; empty when unclaimed or unknown.
    imdb_id: str = ""


@dataclass(frozen=True)
class Candidate:
    """One item a server offered, in terms common to both servers."""

    id: str
    title: str
    year: int | None
    #: Where the server says the files are: a file for a film, a folder for a
    #: series. Empty when the listing did not carry them.
    paths: tuple[str, ...] = ()
    #: Fetches the paths a listing did not carry. Called at most once, and only
    #: until a candidate matches.
    locate: Callable[[], tuple[str, ...]] = field(default=lambda: (), repr=False)


def _plex_headers() -> dict:
    return {"X-Plex-Token": config.PLEX_TOKEN, "Accept": "application/json"}


def _plex_configured() -> bool:
    return bool(config.PLEX_URL and config.PLEX_TOKEN)


def _plex_entries(answer: dict | None) -> list[dict]:
    return ((answer or {}).get("MediaContainer") or {}).get("Metadata") or []


def _plex_files(entry: dict) -> tuple[str, ...]:
    """The files a film's listing names. Series entries carry none; see
    :func:`_plex_folders`."""
    return tuple(
        part["file"]
        for medium in entry.get("Media") or []
        for part in medium.get("Part") or []
        if part.get("file")
    )


def _plex_folders(rating_key: str) -> tuple[str, ...]:
    """The folders one item occupies, from its own metadata. One more call,
    since a series listing does not carry them."""
    answer = request(
        f"{config.PLEX_URL}/library/metadata/{rating_key}", _plex_headers(), timeout=TIMEOUT
    )
    entries = _plex_entries(answer)
    return tuple(
        location["path"].rstrip("/")
        for entry in entries[:1]
        for location in entry.get("Location") or []
        if location.get("path")
    )


def _plex_candidates(folder: str, name: str) -> list[Candidate]:
    """What Plex holds under this name in the section holding this folder.

    Scoped to the section so a film and a series of one name are never each
    other's answer. ``title`` is a prefix match.
    """
    section = next(
        (key for location, key in plex_locations() if path_within(folder, location)), None
    )
    if section is None:
        log.debug("plex: %s is outside every section it indexes", folder)
        return []
    query = urllib.parse.urlencode({"title": name})
    answer = request(
        f"{config.PLEX_URL}/library/sections/{section}/all?{query}",
        _plex_headers(),
        timeout=TIMEOUT,
    )
    return [
        Candidate(
            id=str(entry["ratingKey"]),
            title=str(entry.get("title") or ""),
            year=entry.get("year"),
            paths=_plex_files(entry),
            # Bound now, so the caller is server-agnostic.
            locate=functools.partial(_plex_folders, str(entry["ratingKey"])),
        )
        for entry in _plex_entries(answer)
        if entry.get("ratingKey")
    ]


#: Each server's own id, per address and credential. Static, and both link
#: formats need it.
_identities: dict[tuple[str, str, str], str] = {}
_identity_lock = threading.Lock()


def _identity(server: str, url: str, key: str, read: Callable[[], str]) -> str:
    with _identity_lock:
        cached = _identities.get((server, url, key))
    if cached is not None:
        return cached
    found = read()
    with _identity_lock:
        _identities[(server, url, key)] = found
    return found


def _plex_machine() -> str:
    """The server's machine identifier, which its web app addresses it by."""

    def read() -> str:
        answer = request(f"{config.PLEX_URL}/identity", _plex_headers(), timeout=TIMEOUT)
        return str(((answer or {}).get("MediaContainer") or {}).get("machineIdentifier") or "")

    return _identity("plex", config.PLEX_URL, config.PLEX_TOKEN, read)


def _plex_link(subject: Subject) -> str:
    if not _plex_configured():
        return ""
    mapped = map_path(subject.folder, config.PLEX_PATH_MAP)
    found = _resolve(mapped, subject.name, subject.year, _plex_candidates)
    if found is None:
        return ""
    machine = _plex_machine()
    if not machine:
        return ""
    # The route is in the fragment, so the key must be escaped inside it.
    key = urllib.parse.quote(f"/library/metadata/{found.id}", safe="")
    base = config.PLEX_PUBLIC_URL or config.PLEX_URL
    return f"{base}/web/index.html#!/server/{machine}/details?key={key}"


def _jellyfin_headers() -> dict:
    return {"X-Emby-Token": config.JELLYFIN_API_KEY, "Accept": "application/json"}


def _jellyfin_configured() -> bool:
    return bool(config.JELLYFIN_URL and config.JELLYFIN_API_KEY)


def _jellyfin_candidates(folder: str, name: str) -> list[Candidate]:
    """What Jellyfin holds under this name. Not scoped like the Plex search:
    each item carries its ``Path``, so the folder settles it afterwards."""
    query = urllib.parse.urlencode(
        {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series",
            "Fields": "Path",
            "SearchTerm": name,
            "Limit": str(MAX_CANDIDATES * 4),
            "EnableImages": "false",
        }
    )
    answer = request(
        f"{config.JELLYFIN_URL}/Items?{query}", _jellyfin_headers(), timeout=TIMEOUT
    )
    return [
        Candidate(
            id=str(item["Id"]),
            title=str(item.get("Name") or ""),
            year=item.get("ProductionYear"),
            paths=((item["Path"].rstrip("/"),) if item.get("Path") else ()),
        )
        for item in (answer or {}).get("Items") or []
        if item.get("Id")
    ]


def _jellyfin_server() -> str:
    """The server id Jellyfin's web app uses, from /System/Info.

    Failure is swallowed, unlike Plex's identifier: a client with one server
    reads the link without it, so a refusal costs the parameter, not the button.
    """

    def read() -> str:
        try:
            answer = request(
                f"{config.JELLYFIN_URL}/System/Info", _jellyfin_headers(), timeout=TIMEOUT
            )
        except API_ERRORS as err:
            log.debug("jellyfin: could not read its server id (%s)", err)
            return ""
        return str((answer or {}).get("Id") or "")

    return _identity("jellyfin", config.JELLYFIN_URL, config.JELLYFIN_API_KEY, read)


def _jellyfin_link(subject: Subject) -> str:
    if not _jellyfin_configured():
        return ""
    mapped = map_path(subject.folder, config.JELLYFIN_PATH_MAP)
    found = _resolve(mapped, subject.name, subject.year, _jellyfin_candidates)
    if found is None:
        return ""
    base = config.JELLYFIN_PUBLIC_URL or config.JELLYFIN_URL
    # Optional: a client with one server reads the page without it.
    server = _jellyfin_server()
    return f"{base}/web/#/details?id={found.id}" + (f"&serverId={server}" if server else "")


#: Where a name stops being spelled the same on both sides: the *arrs add a
#: parenthetical ("The Traitors (US)") and punctuation differs ("Dude, Where's
#: My Car?"). What comes before is enough to search on.
_PLAIN = re.compile(r"[^0-9A-Za-z ]")

#: Shorter than this and the search returns the whole library.
MIN_PREFIX = 3


def _shortened(name: str) -> str:
    """The head of a name worth searching again on, or "" when there is none."""
    head = _PLAIN.split(name, maxsplit=1)[0].strip()
    return head if len(head) >= MIN_PREFIX and head != name.strip() else ""


def _same_title(offered: str, wanted: str) -> bool:
    return offered.strip().casefold() == wanted.strip().casefold()


def _holds(candidate: Candidate, folder: str) -> bool:
    """Whether this candidate's files are the ones in our folder, in either
    direction: a film's path is inside the folder, a series' is the folder."""
    paths = candidate.paths or candidate.locate()
    return any(path_within(path, folder) or path_within(folder, path) for path in paths)


def _resolve(
    folder: str,
    name: str,
    year: int | None,
    search: Callable[[str, str], list[Candidate]],
) -> Candidate | None:
    """The server's item for this title, in at most two searches.

    The second is for a name the two sides spell differently. :func:`_pick`
    still compares against the whole name, so a shorter search never lets a
    neighbour through on its name alone.
    """
    found = _pick(search(folder, name), folder, name, year)
    if found is None and (prefix := _shortened(name)):
        found = _pick(search(folder, prefix), folder, name, year)
    return found


def _pick(
    candidates: list[Candidate], folder: str, name: str, year: int | None
) -> Candidate | None:
    """The one candidate that is this title, or None.

    The path decides. Name and year are the fallback for a server with a
    different mount and no path map, and only answer when exactly one
    candidate fits.
    """
    named = [
        candidate for candidate in candidates if _same_title(candidate.title, name)
    ] or candidates[:MAX_CANDIDATES]
    for candidate in named:
        if _holds(candidate, folder):
            return candidate
    fitting = [
        candidate
        for candidate in named
        if _same_title(candidate.title, name)
        and (not year or not candidate.year or candidate.year == year)
    ]
    return fitting[0] if len(fitting) == 1 else None


@dataclass(frozen=True)
class Server:
    """One service a title can be opened in, and how to reach it there."""

    name: str
    label: str
    #: Whether the settings name this one. An *arr wants only an address, since
    #: a link is not a call.
    configured: Callable[[], bool]
    resolve: Callable[[Subject], str]
    #: Whether resolving needs no network call. Known links ride along with the
    #: title; the rest are the second request.
    known: bool = False


def _arr_server(name: str, label: str, route: str, address: Callable[[], str]) -> Server:
    """Radarr or Sonarr, whose pages route on the slug it gave us. Answers
    only for the titles it claims."""

    def link(subject: Subject) -> str:
        base = address()
        if not base or subject.arr != name or not subject.slug:
            return ""
        # One path segment: quote() leaves a slash alone by default.
        return f"{base}{route}{urllib.parse.quote(subject.slug, safe='')}"

    return Server(name, label, lambda: bool(address()), link, known=True)


#: What an IMDb id must look like before it goes into an href.
_IMDB_ID = re.compile(r"tt\d+")


def _imdb_link(subject: Subject) -> str:
    """IMDb's page for the title, from the *arr's id. Nothing to configure: a
    title with an id has the link."""
    if not _IMDB_ID.fullmatch(subject.imdb_id):
        return ""
    return f"https://www.imdb.com/title/{subject.imdb_id}/"


SERVERS: tuple[Server, ...] = (
    # Watch first, manage second, IMDb last.
    Server("plex", "Plex", _plex_configured, _plex_link),
    Server("jellyfin", "Jellyfin", _jellyfin_configured, _jellyfin_link),
    _arr_server(
        "radarr", "Radarr", "/movie/", lambda: config.RADARR_PUBLIC_URL or config.RADARR_URL
    ),
    _arr_server(
        "sonarr", "Sonarr", "/series/", lambda: config.SONARR_PUBLIC_URL or config.SONARR_URL
    ),
    Server("imdb", "IMDb", lambda: True, _imdb_link, known=True),
)


def offered(subject: Subject) -> list[dict]:
    """Every service that could link to this title, with the known links
    filled in.

    Read with the title itself, so the button row is there from the first
    frame rather than growing under the reader's thumb. The *arr links need no
    call, so they are not made to wait behind a Plex search.
    """
    found = []
    for server in SERVERS:
        if server.known:
            if url := server.resolve(subject):
                found.append({"server": server.name, "label": server.label, "url": url})
        elif server.configured():
            found.append({"server": server.name, "label": server.label})
    return found


#: Resolved links per (server, folder), with when. "" is a remembered miss.
_found: dict[tuple[str, str], tuple[float, str]] = {}
_found_lock = threading.Lock()


def forget() -> None:
    """Drop every remembered answer. Called when the settings change, since a
    corrected address or path map is when the last answer was wrong."""
    with _found_lock:
        _found.clear()
    with _identity_lock:
        _identities.clear()


def _remembered(server: Server, subject: Subject) -> str:
    """One server's link for one title, cached. Every failure ends here: a
    link is the least of what the sheet is for."""
    cache_key = (server.name, subject.folder)
    with _found_lock:
        cached = _found.get(cache_key)
    if cached is not None:
        age = time.monotonic() - cached[0]
        if age < (FOUND_TTL if cached[1] else MISSING_TTL):
            return cached[1]
    try:
        url = server.resolve(subject)
    except API_ERRORS as err:
        log.debug("%s: could not look up %s (%s)", server.name, subject.folder, err)
        url = ""
    with _found_lock:
        _found[cache_key] = (time.monotonic(), url)
    return url


def for_title(subject: Subject) -> list[dict]:
    """Every service that has this title, as ``server``, ``label`` and ``url``.

    Each server's path map is applied on the way out. Known links are rebuilt
    rather than cached, since a cache would be a second place for an address
    to go stale.
    """
    if not subject.folder:
        return []
    if not subject.name:
        subject = replace(subject, name=os.path.basename(subject.folder))
    links = []
    for server in SERVERS:
        if not server.configured():
            continue
        url = server.resolve(subject) if server.known else _remembered(server, subject)
        if url:
            links.append({"server": server.name, "label": server.label, "url": url})
    return links
