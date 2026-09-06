"""Radarr and Sonarr clients: a title's ``originalLanguage``, a rescan of a
rewritten file, and a webhook connection pointing back here."""

import logging
import os
from dataclasses import dataclass

from . import auth, config
from .client import API_ERRORS, request
from .langs import ARR_NON_LANGUAGES, from_name

log = logging.getLogger(__name__)

#: What the connection is called inside Radarr and Sonarr.
WEBHOOK_NAME = "trackstarr"

#: The header the connection sends with every webhook, carrying the shared
#: secret.
AUTH_HEADER = "X-Api-Key"

#: The one path webhook POSTs are accepted on, so the rest of the namespace
#: stays free for the API. Here because it is part of the saved connection.
WEBHOOK_PATH = "/webhook"


def webhook_url() -> str:
    """The URL the *arrs are registered to call: WEBHOOK_URL plus the path."""
    return config.WEBHOOK_URL + WEBHOOK_PATH


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
        """The whole movie/series list. Raises API_ERRORS on an outage, so a
        caller can tell it from an empty library."""
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

    def _connection(self) -> dict | None:
        """Our notification entry in this *arr, or None. Raises API_ERRORS."""
        existing = self._call("/api/v3/notification") or []
        return next((entry for entry in existing if entry.get("name") == WEBHOOK_NAME), None)

    def _connection_current(self, ours: dict, url: str) -> bool:
        """Whether an entry points here and carries a secret the listener
        accepts. Verified against the stored digest, since only that is kept."""
        return _webhook_current(ours, _payload(url)) and auth.matches(
            self.name, _sent_secret(ours)
        )

    def webhook_status(self, url: str) -> str:
        """Whether this *arr will call us: connected, stale or missing. For
        the connections page, so it works with unsaved values. Raises
        API_ERRORS."""
        ours = self._connection()
        if ours is None:
            return "missing"
        return "connected" if self._connection_current(ours, url) else "stale"

    def register_webhook(self, url: str) -> bool:
        """Create or update this *arr's webhook connection back to us.

        True once it points at ``url`` with a secret the listener accepts;
        False is safe to retry. A connection that does not verify gets a fresh
        secret.
        """
        if not self.enabled:
            return True
        try:
            ours = self._connection()
        except API_ERRORS as err:
            log.warning("%s: webhook registration failed (%s), will retry", self.name, err)
            return False
        if ours and self._connection_current(ours, url):
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
            # Saving fires a test event at the url, so the listener must be up.
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
        # Always rotates the secret, so seeing this on every restart means the
        # *arr is not returning the header as saved.
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
    """The connection we want the *arr to hold. Without a secret it is what
    an existing connection is compared against; with one, the body to save."""
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
    """A notification's settings as name -> value. An unexpected shape is a
    miss, not an exception."""
    return {entry.get("name"): entry.get("value") for entry in notification.get("fields") or []}


def _sent_secret(notification: dict) -> str:
    """The AUTH_HEADER value an existing connection would call us with."""
    headers = _fields(notification).get("headers")
    for header in headers if isinstance(headers, list) else []:
        if isinstance(header, dict) and header.get("key") == AUTH_HEADER:
            return str(header.get("value") or "")
    return ""


def _webhook_current(notification: dict, payload: dict) -> bool:
    """Whether an existing connection matches what we would save: every event
    flag the payload turns on is on, and every field matches."""
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
    """What the *arrs know about a title. Its folder is the index key."""

    lang: str | None
    item_id: int
    arr: Arr


@dataclass(frozen=True)
class LibraryIndex:
    """Every title the *arrs answered for, and whether they all answered.

    With ``complete`` False an unmatched file's original language is unknown,
    not "none", and anything that rewrites must treat the two differently.
    """

    items: dict[str, LibraryItem]
    complete: bool


def path_index(arrs: list[Arr]) -> LibraryIndex:
    """Library titles keyed by folder. An *arr that fails to answer marks the
    index incomplete; the others' titles still match."""
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
            # A folder that normalises to nothing is the root, which would
            # claim every file.
            if base := (item.get("path") or "").rstrip("/"):
                # First wins.
                items.setdefault(base, LibraryItem(original_of(item), item["id"], arr))
    log.info("indexed %d titles from the *arrs", len(items))
    return LibraryIndex(items, complete)


def innermost[T](folders: dict[str, T], path: str) -> T | None:
    """The value under the deepest key containing ``path``, or None.

    Walks the folders outwards, so a nested title wins. Purely lexical.
    Generic because :mod:`trackstarr.library` indexes the same folders under
    a richer value.
    """
    while True:
        if (found := folders.get(path)) is not None:
            return found
        parent = os.path.dirname(path)
        # dirname of a root is itself, so this terminates.
        if parent == path:
            return None
        path = parent


def match_path(index: LibraryIndex, path: str) -> LibraryItem | None:
    """The innermost indexed title containing ``path``, or None."""
    return innermost(index.items, path)
