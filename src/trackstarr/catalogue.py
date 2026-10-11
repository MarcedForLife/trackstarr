"""Which connections claim a file, and whether a read-only one does.

Claims come from each *arr's title folders, not from whoever asked. Only
read-only connections are listed, so an install without one asks nothing.
"""

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

from . import config
from .arr import client_for, innermost
from .client import API_ERRORS

log = logging.getLogger(__name__)

#: How long a listing is reused, as the library reuses its title list.
LISTING_TTL = 300.0

#: How soon a failed listing is asked again. The last answer stands in meanwhile.
RETRY_SECONDS = 15.0


@dataclass(frozen=True)
class _Listing:
    #: Title folders as keys, so :func:`trackstarr.arr.innermost` can walk them.
    folders: Mapping[str, bool]
    #: time.monotonic() past which it is asked for again.
    expires: float


_lock = threading.Lock()
#: Keyed by address and key too, so an edited connection never reuses old folders.
_listings: dict[tuple[str, str, str], _Listing] = {}
_retry_at: dict[tuple[str, str, str], float] = {}


def _folders(instance: config.ArrInstanceConfig) -> Mapping[str, bool] | None:
    """The connection's title folders, or None when it has never answered.
    Fetched under the lock, so a sweep's probe workers ask once between them."""
    identity = (instance.id, instance.url, instance.api_key)
    with _lock:
        now = time.monotonic()
        held = _listings.get(identity)
        if held is not None and now < held.expires:
            return held.folders
        if now < _retry_at.get(identity, 0.0):
            return held.folders if held else None
        try:
            items = client_for(instance).all_items()
        except API_ERRORS as err:
            log.warning("%s: could not list its titles (%s)", instance.id, err)
            _retry_at[identity] = now + RETRY_SECONDS
            return held.folders if held else None
        # An empty folder is the root, which would claim every file.
        folders = {
            base: True for item in items if (base := (item.get("path") or "").rstrip("/"))
        }
        _listings[identity] = _Listing(folders, now + LISTING_TTL)
        return folders


def claims(instance: config.ArrInstanceConfig, path: str) -> bool | None:
    """Whether a title folder the connection lists holds ``path``, or None
    when it has never answered."""
    folders = _folders(instance)
    if folders is None:
        return None
    return innermost(folders, path) is not None


def read_only(path: str, claimed_by: str = "") -> str | None:
    """Why ``path`` must not change, or None.

    ``claimed_by`` is the connection that delivered or matched the file, since
    an import lands before the next listing shows its folder. A read-only
    connection that never answered could claim anything, so it refuses all.
    """
    for instance in config.current().arr_instances:
        if not instance.read_only:
            continue
        label = config.instance_name(instance.id, instance.name)
        if instance.id == claimed_by:
            return f"{label} is read-only"
        claimed = claims(instance, path)
        if claimed is None:
            return f"{label} is read-only and could not be listed"
        if claimed:
            return f"{label} is read-only"
    return None


def forget() -> None:
    """Drop every listing, for tests."""
    with _lock:
        _listings.clear()
        _retry_at.clear()
