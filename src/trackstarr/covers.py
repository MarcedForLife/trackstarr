"""Poster art for the grid: fetched from the title's *arr (Radarr or Sonarr)
or found beside its files, then kept under STATE_DIR.

Served through us rather than linked, which keeps the *arr's API key out of
the browser.
"""

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, library
from .client import API_ERRORS, fetch

log = logging.getLogger(__name__)

#: Poster sizes the *arrs cache, best first; the original is the fallback for a
#: title whose resize never ran. Fetched under /api/v3, since the bare
#: /MediaCover path answers an API key with a redirect to the login page.
_COVER_NAMES = ("poster-500.jpg", "poster.jpg")

#: Artwork beside the files, for a title no *arr claims. The names Plex,
#: Jellyfin and Kodi write.
_LOCAL_COVERS = ("poster.jpg", "folder.jpg", "cover.jpg", "poster.png")

#: Where fetched posters are kept, under STATE_DIR. Written once per title:
#: a poster does not change under its id.
COVER_DIR = "covers"

#: How long a title with no poster is remembered as having none, so every load
#: does not re-ask the *arrs for every unclaimed folder.
_ABSENT_TTL = 3600.0

#: How many posters the warm-up fetches at once. About not opening a library's
#: worth of sockets rather than throughput.
_WARM_WORKERS = 4

#: Titles nothing has a poster for, and when we last looked.
_absent: dict[str, float] = {}
_absent_lock = threading.Lock()


def forget() -> None:
    """Look again for the posters nothing had, since an *arr that was
    unreachable or unset may answer now."""
    with _absent_lock:
        _absent.clear()


def cover(title_id: str) -> tuple[bytes, str] | None:
    """The title's poster and content type, or None.

    From our copy when we have one, otherwise fetched from the *arr or from
    beside the files and kept.
    """
    body = _stored_cover(title_id) or _fetch_cover(title_id)
    return (body, _image_type(body)) if body else None


def _image_type(body: bytes) -> str:
    """The poster's content type, sniffed from its first bytes."""
    return "image/png" if body.startswith(b"\x89PNG") else "image/jpeg"


def _cover_file(title_id: str) -> str:
    """Where a title's poster is kept. Hashed, since an id may hold a path."""
    name = hashlib.blake2s(title_id.encode(), digest_size=16).hexdigest()
    return os.path.join(config.STATE_DIR, COVER_DIR, name)


def _stored_cover(title_id: str) -> bytes | None:
    try:
        with open(_cover_file(title_id), "rb") as stored:
            return stored.read() or None
    except OSError:
        return None


def _keep_cover(title_id: str, body: bytes) -> None:
    """Store the poster atomically. Never raises.

    Staged and replaced, since the warm-up and a request for the same title
    can race and a half-written poster is a broken image.
    """
    path = _cover_file(title_id)
    partial = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(partial, "wb") as out_file:
            out_file.write(body)
        os.replace(partial, path)
    except OSError as err:
        log.warning("could not keep the poster for %s: %s", title_id, err)


def _known_absent(title_id: str) -> bool:
    with _absent_lock:
        looked = _absent.get(title_id)
    return looked is not None and time.monotonic() - looked < _ABSENT_TTL


def _fetch_cover(title_id: str) -> bytes | None:
    """Ask the *arr, then the folder, and keep whatever answers."""
    if _known_absent(title_id):
        return None
    found = library.known().index.get(title_id)
    body = _from_arr(found) or _local_cover(found.folder) if found else None
    if body:
        _keep_cover(title_id, body)
    else:
        with _absent_lock:
            _absent[title_id] = time.monotonic()
    return body


def _from_arr(found: library.Title) -> bytes | None:
    if not (found.arr and found.arr.enabled):
        return None
    for name in _COVER_NAMES:
        try:
            body, kind = fetch(
                f"{found.arr.url}/api/v3/mediacover/{found.item_id}/{name}",
                {"X-Api-Key": found.arr.key},
            )
        except API_ERRORS:
            continue
        if body and kind.startswith("image/"):
            return body
    return None


def _local_cover(folder: str) -> bytes | None:
    """Artwork beside the files, for a title no *arr could answer for."""
    for name in _LOCAL_COVERS:
        try:
            with open(os.path.join(folder, name), "rb") as art:
                if body := art.read():
                    return body
        except OSError:
            continue
    return None


_warming = threading.Lock()


def warm() -> None:
    """Fetch missing posters in the background, returning at once.

    Called when the grid is handed over, so the *arrs see a few connections
    from us rather than hundreds from the grid. A warm-up already running is
    left to finish.
    """
    if not _warming.acquire(blocking=False):
        return
    threading.Thread(target=_warm_once, name="cover-warm", daemon=True).start()


def _warm_once() -> None:
    """The warm-up thread's body: one pass, errors logged."""
    try:
        _warm_all()
    except Exception:
        # The grid still works; it fetches its own posters one at a time.
        log.exception("could not warm the poster cache")
    finally:
        _warming.release()


def _warm_all() -> None:
    """Fetch every poster we have not got, a few at a time."""
    wanted = [
        title.id
        for title in library.known().titles
        if not _known_absent(title.id) and not os.path.exists(_cover_file(title.id))
    ]
    if not wanted:
        return
    log.info("fetching %d posters the library view has not seen before", len(wanted))
    with ThreadPoolExecutor(_WARM_WORKERS, thread_name_prefix="cover") as pool:
        # list() so an exception surfaces here rather than in an undrained
        # generator.
        list(pool.map(_fetch_cover, wanted))
