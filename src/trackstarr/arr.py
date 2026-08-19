"""Radarr and Sonarr clients.

Three things are wanted from them: a title's ``originalLanguage``, a nudge to
rescan a file after it has been rewritten, and a webhook connection pointing
back here so nothing has to be clicked together in their UIs.
``originalLanguage`` is already on the movie/series object, so there is no
TMDB or IMDB lookup to configure and nothing to rate-limit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import config
from .client import API_ERRORS, request
from .langs import ARR_NON_LANGUAGES, from_name
from .paths import path_within

log = logging.getLogger(__name__)

#: What the connection is called inside Radarr and Sonarr.
WEBHOOK_NAME = "trackstarr"


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

        Returns True once the connection exists and points at ``url``; a
        False means the *arr was unreachable or rejected the save, and the
        call is safe to retry.
        """
        if not self.enabled:
            return True
        payload = {
            "name": WEBHOOK_NAME,
            "implementation": "Webhook",
            "configContract": "WebhookSettings",
            # Import and upgrade are the only events the listener acts on.
            "onDownload": True,
            "onUpgrade": True,
            "fields": [{"name": "url", "value": url}, {"name": "method", "value": 1}],
        }
        try:
            existing = self._call("/api/v3/notification") or []
            ours = next(
                (entry for entry in existing if entry.get("name") == WEBHOOK_NAME), None
            )
            if ours and _webhook_current(ours, payload):
                return True
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
            log.info("%s: webhook connection registered -> %s", self.name, url)
            return True
        except API_ERRORS as err:
            log.warning("%s: webhook registration failed (%s), will retry", self.name, err)
            return False

    def rescan(self, item_id: int) -> None:
        """Re-read the file, so the *arr's size and media info stay true."""
        if not self.enabled:
            return
        try:
            self._call("/api/v3/command", {"name": self.rescan_cmd, self.rescan_key: item_id})
        except API_ERRORS as err:
            log.warning("%s: rescan of id %s failed (%s)", self.name, item_id, err)


def _webhook_current(notification: dict, payload: dict) -> bool:
    """Whether an existing connection already matches what we would save.

    Driven by the payload so the desired state is declared exactly once: every
    event flag the payload turns on must be on, and every field must match.
    """
    current = {
        entry.get("name"): entry.get("value") for entry in notification.get("fields") or []
    }
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
    """A title the *arrs manage: its folder, original language and id."""

    base: str
    lang: str | None
    item_id: int
    arr: Arr


def path_index(arrs: list[Arr]) -> list[LibraryItem]:
    """Library titles with their original language, longest path first.

    Built once per sweep. Sorting by length means the first prefix match is
    always the most specific one.
    """
    index = [
        LibraryItem(item["path"].rstrip("/"), original_of(item), item["id"], arr)
        for arr in arrs
        for item in arr.all_items()
        if item.get("path")
    ]
    index.sort(key=lambda entry: -len(entry.base))
    log.info("indexed %d titles from the *arrs", len(index))
    return index


def match_path(index: list[LibraryItem], path: str) -> LibraryItem | None:
    return next((entry for entry in index if path_within(path, entry.base)), None)
