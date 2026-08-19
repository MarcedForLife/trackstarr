"""Radarr and Sonarr clients.

Three things are wanted from them: a title's ``originalLanguage``, a nudge to
rescan a rewritten file, and a webhook connection pointing back here so
nothing has to be clicked together in their UIs. ``originalLanguage`` is
already on the movie/series object, so there is no TMDB key to configure and
nothing to rate-limit.
"""

import logging
import os
from dataclasses import dataclass

from . import auth, config
from .client import API_ERRORS, request
from .langs import ARR_NON_LANGUAGES, from_name

log = logging.getLogger(__name__)

#: What the connection is called inside Radarr and Sonarr.
WEBHOOK_NAME = "trackstarr"

#: Header the connection is configured to send back with every webhook,
#: carrying the shared secret the listener requires.
AUTH_HEADER = "X-Api-Key"


@dataclass
class Arr:
    """One *arr, and every fact that differs between Radarr and Sonarr."""

    name: str
    url: str
    key: str
    item_ep: str  # endpoint listing the movies/series
    rescan_cmd: str  # command that re-reads a file from disk
    rescan_key: str  # id field that command expects
    body_key: str  # webhook body key holding the movie/series object
    folder_key: str  # field on that object holding the title's folder
    file_key: str  # webhook body key for a single imported file
    files_key: str  # webhook body key for a batch of them

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def _call(
        self,
        path: str,
        payload: dict | None = None,
        timeout: int = 30,
        method: str | None = None,
    ):
        return request(f"{self.url}{path}", {"X-Api-Key": self.key}, payload, timeout, method)

    def all_items(self) -> list[dict]:
        """The whole movie/series list. Raises API_ERRORS when the *arr
        cannot answer, so a caller can tell an outage from an empty library."""
        if not self.enabled:
            return []
        return self._call(self.item_ep, timeout=120) or []

    def item(self, item_id: int) -> dict | None:
        if not self.enabled:
            return None
        try:
            return self._call(f"{self.item_ep}/{item_id}")
        except API_ERRORS as err:
            log.warning("%s: lookup of id %s failed (%s)", self.name, item_id, err)
            return None

    def register_webhook(self, url: str) -> bool:
        """Create or update this *arr's webhook connection back to us.

        True once it exists, points at ``url`` and carries a secret the
        listener accepts; False (the *arr unreachable, or it refused the
        save) is safe to retry. Only a digest of the secret is kept, so what
        the *arr holds is verified against that, and a connection that
        doesn't verify gets a fresh one.
        """
        if not self.enabled:
            return True
        try:
            existing = self._call("/api/v3/notification") or []
        except API_ERRORS as err:
            log.warning("%s: webhook registration failed (%s), will retry", self.name, err)
            return False
        ours = next((entry for entry in existing if entry.get("name") == WEBHOOK_NAME), None)
        if (
            ours
            and _webhook_current(ours, _payload(url))
            and auth.matches(self.name, _sent_secret(ours))
        ):
            return True
        try:
            secret = auth.mint(self.name)
        except OSError as err:
            log.warning(
                "%s: cannot provision a webhook secret (%s), will retry", self.name, err
            )
            return False
        payload = _payload(url, secret)
        try:
            # Saving makes the *arr fire a test event at the url, so the
            # listener has to be accepting connections already.
            if ours:
                self._call(
                    f"/api/v3/notification/{ours['id']}",
                    {**payload, "id": ours["id"]},
                    method="PUT",
                )
            else:
                self._call("/api/v3/notification", payload)
        except API_ERRORS as err:
            log.warning("%s: webhook registration failed (%s), will retry", self.name, err)
            return False
        # Reaching here always rotates one, so this logged on every restart
        # means the *arr is not giving the header back as saved.
        log.info("%s: webhook connection registered with a fresh secret -> %s", self.name, url)
        return True

    def rescan(self, item_id: int) -> None:
        """Re-read the file, so the *arr's size and media info stay true."""
        if not self.enabled:
            return
        try:
            self._call("/api/v3/command", {"name": self.rescan_cmd, self.rescan_key: item_id})
        except API_ERRORS as err:
            log.warning("%s: rescan of id %s failed (%s)", self.name, item_id, err)


def _payload(url: str, secret: str | None = None) -> dict:
    """The connection we want the *arr to hold.

    Without a secret it is the settings half alone, what an existing
    connection is compared against; with one it is the body to save.
    """
    fields = [
        {"name": "url", "value": url},
        {"name": "method", "value": 1},
    ]
    if secret is not None:
        # Custom headers, sent with every callback including the test.
        fields.append({"name": "headers", "value": [{"key": AUTH_HEADER, "value": secret}]})
    return {
        "name": WEBHOOK_NAME,
        "implementation": "Webhook",
        "configContract": "WebhookSettings",
        # Import and upgrade are the only events the listener acts on.
        "onDownload": True,
        "onUpgrade": True,
        "fields": fields,
    }


def _fields(notification: dict) -> dict:
    """A notification's settings as name -> value.

    Read back from a foreign API, so anything but the shape we saved degrades
    to a miss rather than an exception on the register thread.
    """
    return {entry.get("name"): entry.get("value") for entry in notification.get("fields") or []}


def _sent_secret(notification: dict) -> str:
    """The AUTH_HEADER value an existing connection would call us with."""
    headers = _fields(notification).get("headers")
    for header in headers if isinstance(headers, list) else []:
        if isinstance(header, dict) and header.get("key") == AUTH_HEADER:
            return str(header.get("value") or "")
    return ""


def _webhook_current(notification: dict, payload: dict) -> bool:
    """Whether an existing connection already matches what we would save.

    Driven by the payload, so the desired state is declared once: every event
    flag the payload turns on has to be on, and every field has to match.
    """
    current = _fields(notification)
    events_on = all(
        notification.get(key)
        for key, value in payload.items()
        if key.startswith("on") and value is True
    )
    fields_match = all(
        current.get(entry["name"]) == entry["value"] for entry in payload["fields"]
    )
    return events_on and fields_match


def radarr() -> Arr:
    return Arr(
        name="radarr",
        url=config.RADARR_URL,
        key=config.RADARR_API_KEY,
        item_ep="/api/v3/movie",
        rescan_cmd="RescanMovie",
        rescan_key="movieId",
        body_key="movie",
        folder_key="folderPath",
        file_key="movieFile",
        files_key="movieFiles",
    )


def sonarr() -> Arr:
    return Arr(
        name="sonarr",
        url=config.SONARR_URL,
        key=config.SONARR_API_KEY,
        item_ep="/api/v3/series",
        rescan_cmd="RescanSeries",
        rescan_key="seriesId",
        body_key="series",
        folder_key="path",
        file_key="episodeFile",
        files_key="episodeFiles",
    )


def all_arrs() -> list[Arr]:
    return [radarr(), sonarr()]


def original_of(item: dict | None) -> str | None:
    """Pull ISO 639-2/B out of a movie or series object."""
    if not item:
        return None
    name = (item.get("originalLanguage") or {}).get("name")
    code = from_name(name)
    if name and code is None and name.strip().lower() not in ARR_NON_LANGUAGES:
        log.warning("unmapped original language %r, treating as unknown", name)
    return code


@dataclass(frozen=True)
class LibraryItem:
    """What the *arrs know about a title: its original language and id.

    Where it lives is the key it is indexed under, not a field.
    """

    lang: str | None
    item_id: int
    arr: Arr


@dataclass(frozen=True)
class LibraryIndex:
    """Every title the *arrs answered for, and whether they all answered.

    ``complete`` False means unmatched files may only look unmatched because
    an outage hid their titles, so their original language is not "none",
    it is unknown. Anything that rewrites has to treat the two differently.
    """

    items: dict[str, LibraryItem]
    complete: bool


def path_index(arrs: list[Arr]) -> LibraryIndex:
    """Library titles with their original language, keyed by their folder.

    Built once per sweep and asked about every file in the library, so a
    mapping rather than a list. See :func:`match_path`. An *arr that fails
    to answer is logged and marks the index incomplete; the others' titles
    still match.
    """
    items: dict[str, LibraryItem] = {}
    complete = True
    for arr in arrs:
        try:
            fetched = arr.all_items()
        except API_ERRORS as err:
            log.warning("%s: could not fetch library (%s)", arr.name, err)
            complete = False
            continue
        for item in fetched:
            # A folder that normalises to nothing is the filesystem root,
            # which would claim every file in the library.
            if base := (item.get("path") or "").rstrip("/"):
                # First wins, which is the order the list this replaced kept.
                items.setdefault(base, LibraryItem(original_of(item), item["id"], arr))
    log.info("indexed %d titles from the *arrs", len(items))
    return LibraryIndex(items, complete)


def match_path(index: LibraryIndex, path: str) -> LibraryItem | None:
    """The innermost indexed title containing ``path``, or None.

    Walks the file's folders outwards, so a title nested inside another's
    folder still wins, at one dict lookup per directory level. Purely
    lexical: the *arrs report these folders, which need not exist here.
    """
    while True:
        if found := index.items.get(path):
            return found
        parent = os.path.dirname(path)
        # dirname of a root is itself, so this terminates.
        if parent == path:
            return None
        path = parent
