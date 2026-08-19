"""Radarr and Sonarr clients.

Three things are wanted from them: a title's ``originalLanguage``, a nudge to
rescan a file after it has been rewritten, and a webhook connection pointing
back here so nothing has to be clicked together in their UIs.
``originalLanguage`` is already on the movie/series object, so there is no
TMDB or IMDB lookup to configure and nothing to rate-limit.
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
        if not self.enabled:
            return []
        try:
            return self._call(self.item_ep, timeout=120) or []
        except API_ERRORS as err:
            log.warning("%s: could not fetch library (%s)", self.name, err)
            return []

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

        Returns True once the connection exists, points at ``url`` and
        carries a secret the listener will accept; False means the *arr was
        unreachable or rejected the save, and the call is safe to retry.

        The secret is only ever written here, never read back out of our own
        storage, which keeps a digest and nothing else. What the *arr already
        holds is checked against that digest instead, so the connection is
        left alone when it still verifies and given a freshly minted secret
        when it does not — after a wiped STATE_DIR, or a connection edited by
        hand in the *arr's UI.
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
            # listener must already be accepting connections.
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
        # Says "fresh secret" because reaching here always rotates one: a
        # restart that logs this every time means the *arr is not giving the
        # header back as it was saved, so nothing we store can ever match it.
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

    Without a secret this is the settings half alone, which is what an
    existing connection is compared against; with one it is the body to
    save. The credential is left out of the comparison because we keep only
    its digest, so there is nothing here to compare it with — it gets its
    own check in :func:`Arr.register_webhook`.
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

    Read back from a foreign API, so anything but the shape we saved has to
    degrade to a miss rather than an exception in the register thread.
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

    Driven by the payload so the desired state is declared exactly once: every
    event flag the payload turns on must be on, and every field must match.
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

    Where it lives is the key it is indexed under, not a field here.
    """

    lang: str | None
    item_id: int
    arr: Arr


def path_index(arrs: list[Arr]) -> dict[str, LibraryItem]:
    """Library titles with their original language, keyed by their folder.

    Built once per sweep and then asked about every file in the library, so
    it is a mapping rather than a list: see :func:`match_path`.
    """
    index: dict[str, LibraryItem] = {}
    for arr in arrs:
        for item in arr.all_items():
            # A folder that normalises to nothing is the filesystem root,
            # which would otherwise claim every file in the library.
            if base := (item.get("path") or "").rstrip("/"):
                # First wins: two titles claiming one folder keep the earlier
                # *arr's, which is the order the list this replaced also kept.
                index.setdefault(base, LibraryItem(original_of(item), item["id"], arr))
    log.info("indexed %d titles from the *arrs", len(index))
    return index


def match_path(index: dict[str, LibraryItem], path: str) -> LibraryItem | None:
    """The innermost indexed title containing ``path``, or None.

    Walks the file's own folders outwards, so the first hit is the most
    specific one — a title nested inside another's folder still wins. That
    is what a scan sorted longest-base-first gave, but at one dict lookup
    per directory level instead of a comparison against every title: a sweep
    asks this once per file, and a big library has thousands of both.

    Purely lexical: the *arrs report these folders, and they need not exist
    here.
    """
    while True:
        if found := index.get(path):
            return found
        parent = os.path.dirname(path)
        # dirname of a root is itself, absolute or relative, so this ends.
        if parent == path:
            return None
        path = parent
