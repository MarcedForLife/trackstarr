"""The library view: every title, and what the sweep made of its files.

The *arrs (Radarr and Sonarr) know the titles, years, posters and folders; the
sweep cache knows what each file needs. Titles are the spine and cached
verdicts hang off them by folder, using the sweep's own lexical match. Files
under no title are grouped by their top folder rather than dropped.

Nothing here probes or plans. Every verdict shown was written to the cache by
the sweep or by :func:`trackstarr.sweep.remember`; an unswept library is an
empty answer, not a reason to walk it inside a web request.
"""

import contextlib
import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NamedTuple

from . import config, ratings, state, sweep_cache
from .arr import Arr, all_arrs, innermost, original_of
from .client import API_ERRORS, fetch
from .policy import Policy

log = logging.getLogger(__name__)

#: The word the view puts under each *arr's titles.
KINDS = {"radarr": "movie", "sonarr": "series"}

#: Poster sizes the *arrs cache, best first; the original is the fallback for a
#: title whose resize never ran. Fetched under /api/v3, since the bare
#: /MediaCover path answers an API key with a redirect to the login page.
_COVER_NAMES = ("poster-500.jpg", "poster.jpg")

#: Artwork beside the files, for a title no *arr claims. The names Plex,
#: Jellyfin and Kodi write.
_LOCAL_COVERS = ("poster.jpg", "folder.jpg", "cover.jpg", "poster.png")

#: How long a fetched title list is reused. Listing a whole library is one
#: heavy request per *arr; short enough that a new title shows without a
#: restart.
_INDEX_TTL = 300.0

#: Worst first: a card shows the worst state any of its files is in. The last
#: two are the absence of a verdict, and "missing" (nothing downloaded) is not
#: work outstanding. "unsupported" sits below "skip" because a skip is usually
#: momentary and an unsupported container is permanent.
STATES = ("failed", "would-fix", "skip", "unsupported", "conform", "unchecked", "missing")

#: Where fetched posters are kept, under STATE_DIR. Written once per title:
#: a poster does not change under its id.
COVER_DIR = "covers"

#: How long a title with no poster is remembered as having none, so every load
#: does not re-ask the *arrs for every unclaimed folder.
_ABSENT_TTL = 3600.0

#: How many posters the warm-up fetches at once. About not opening a library's
#: worth of sockets rather than throughput.
_WARM_WORKERS = 4

#: Most files a title's detail returns. A 300-episode series with tracks and
#: plans is megabytes of JSON; actionable files come first, so the cut falls
#: on those with nothing to report.
MAX_FILES = 200


@dataclass(frozen=True)
class Title:
    """One movie or series.

    ``id`` is ``arr:radarr:12`` for a claimed title and ``dir:/path`` for an
    unclaimed folder. ``folder`` is in this container's paths, as the cache
    keys are.
    """

    id: str
    name: str
    folder: str
    kind: str
    year: int | None = None
    lang: str | None = None
    #: When the title joined the library, in epoch seconds; 0 when unknown.
    #: The *arr's ``added``, or the folder's mtime for an unclaimed title.
    added: float = 0.0
    #: Only on an *arr title, and only so the cover can be fetched.
    arr: Arr | None = None
    item_id: int = 0
    #: What the *arr's own pages route on: the TMDB id for Radarr, a name
    #: slug for Sonarr. Lets the sheet link back to it.
    slug: str = ""
    #: The IMDb id both *arrs carry, ``tt`` and digits.
    imdb_id: str = ""
    #: The IMDb score out of ten, or None. From :func:`trackstarr.ratings.scores`.
    rating: float | None = None
    #: Whether the *arr says anything is downloaded. False is a tracked title
    #: with no file to judge, distinct from one no sweep has reached; see
    #: :func:`_card`.
    on_disk: bool = True


@dataclass(frozen=True)
class Shelf:
    """Every title, and how much of the answer is trustworthy.

    ``complete`` False means an *arr could not be listed. ``current`` False
    means the verdicts were reached under rules that have since changed and
    the next sweep will drop them.
    """

    titles: list[Title]
    complete: bool = True
    current: bool = True
    #: The same titles keyed by id, since a grid asks for one cover per poster.
    index: dict[str, Title] = field(default_factory=dict)


_lock = threading.Lock()
_cached: tuple[float, Shelf] | None = None


def forget() -> None:
    """Drop every memo so the next read refetches.

    Called when the *arr settings change, and by tests. The parsed cache, the
    built grid and the missing-poster record go too: none is keyed on the
    rules, so a settings save must not serve an answer judged under the old
    ones.
    """
    global _built, _cached, _parsed
    with _lock:
        _built = None
        _cached = None
        _parsed = None
    with _absent_lock:
        _absent.clear()


#: Fractional seconds beyond the six :func:`datetime.fromisoformat` accepts.
#: .NET, which the *arrs are, writes up to seven.
_OVERLONG_FRACTION = re.compile(r"\.(\d{6})\d+")


def _epoch(stamp: object) -> float:
    """An *arr's timestamp in epoch seconds, or 0 for anything unreadable.

    A naive stamp is read as UTC, which is what the *arrs mean. Never raises.
    """
    if not isinstance(stamp, str) or not stamp.strip():
        return 0.0
    try:
        when = datetime.fromisoformat(_OVERLONG_FRACTION.sub(r".\1", stamp.strip()))
    except ValueError:
        return 0.0
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).timestamp()


def _folder_added(folder: str) -> float:
    """The folder's mtime: the nearest thing to an added date for an unclaimed
    title. Moves when trackstarr replaces a file there too."""
    try:
        return os.stat(folder).st_mtime
    except OSError:
        return 0.0


def _on_disk(item: dict) -> bool:
    """Whether the *arr says anything is downloaded for this title.

    Radarr sends ``hasFile``, Sonarr an episode count in ``statistics``, both
    in the list response. Neither present is taken as downloaded, since the
    other reading would hide a title on the strength of a missing field.
    """
    if isinstance(has_file := item.get("hasFile"), bool):
        return has_file
    stats = item.get("statistics")
    if isinstance(stats, dict):
        count = stats.get("episodeFileCount")
        if isinstance(count, int) and not isinstance(count, bool):
            return count > 0
    return True


def _title_of(arr: Arr, item: dict, scored: dict[str, float]) -> Title | None:
    """One *arr object as a Title, or None if it has no folder.

    A folder that normalises to nothing is the root, which would claim every
    file. ``scored`` is :func:`trackstarr.ratings.scores`, read once per shelf.
    """
    folder = (item.get("path") or "").rstrip("/")
    if not folder or not item.get("id"):
        return None
    imdb_id = str(item.get("imdbId") or "").strip()
    return Title(
        id=f"arr:{arr.name}:{item['id']}",
        name=item.get("title") or os.path.basename(folder),
        folder=folder,
        kind=KINDS.get(arr.name, "title"),
        year=item.get("year") or None,
        lang=original_of(item),
        added=_epoch(item.get("added")),
        arr=arr,
        item_id=item["id"],
        slug=str(item.get("titleSlug") or ""),
        imdb_id=imdb_id,
        rating=scored.get(imdb_id),
        on_disk=_on_disk(item),
    )


def _from_arrs() -> tuple[dict[str, Title], bool]:
    """Every *arr title keyed by folder, and whether all of them answered.

    First wins on a duplicate folder, matching :func:`trackstarr.arr.path_index`.
    """
    titles: dict[str, Title] = {}
    complete = True
    scored = ratings.scores()
    for arr in all_arrs():
        try:
            fetched = arr.all_items()
        except API_ERRORS as err:
            log.warning(
                "%s: could not list its titles for the library view (%s)", arr.name, err
            )
            complete = False
            continue
        for item in fetched:
            if title := _title_of(arr, item, scored):
                titles.setdefault(title.folder, title)
    return titles, complete


def imdb_ids() -> set[str] | None:
    """Every IMDb id the shelf holds, for the ratings refresh.

    None when an *arr could not be listed: a table built from half a library
    would leave the other half unscored until the next fetch.
    """
    shelf = _shelf(_read_cache())
    if not shelf.complete:
        return None
    return {title.imdb_id for title in shelf.titles if title.imdb_id}


def _unclaimed(path: str) -> str | None:
    """The title folder of an unclaimed file: the first directory under its
    MEDIA_DIR. A file loose in a media dir has none."""
    for media_dir in config.MEDIA_DIRS:
        root = media_dir.rstrip("/")
        if not root or not path.startswith(root + os.sep):
            continue
        rest = path[len(root) + 1 :]
        head = rest.split(os.sep)[0]
        return os.path.join(root, head) if os.sep in rest else None
    return None


def _shelf(stored: sweep_cache.Stored) -> Shelf:
    """The titles from the *arrs plus unclaimed folders the sweep found,
    memoised for :data:`_INDEX_TTL`.

    ``stored`` is passed in because every caller has already read it, and the
    read is the expensive half of this module.
    """
    global _cached
    with _lock:
        if _cached and time.monotonic() - _cached[0] < _INDEX_TTL:
            return _cached[1]
    # Outside the lock: better two fetches than a lock held through two
    # library-sized HTTP calls.
    claimed, complete = _from_arrs()
    folders = dict(claimed)
    for path in stored.files:
        if innermost(claimed, path) is not None:
            continue
        folder = _unclaimed(path)
        if folder and folder not in folders:
            folders[folder] = Title(
                id=f"dir:{folder}",
                name=os.path.basename(folder),
                folder=folder,
                kind="folder",
                added=_folder_added(folder),
            )
    titles = list(folders.values())
    shelf = Shelf(titles, complete, stored.current, {title.id: title for title in titles})
    with _lock:
        _cached = (time.monotonic(), shelf)
    return shelf


#: The verdicts as last read, keyed by the file's size and mtime and the
#: generation of the walk's view. Generation 0 is a read from the file.
_parsed: tuple[tuple[tuple[int, int], int], sweep_cache.Stored] | None = None


def _read_cache() -> sweep_cache.Stored:
    """Every stored verdict, parsed at most once per version.

    The cache is megabytes and a page load asks for it hundreds of times;
    parsing it per request held the GIL for tens of milliseconds each. Keyed
    on size, mtime and view generation rather than a TTL, so a checkpoint or a
    walk's fresh verdicts are picked up on the next request.

    A running walk's view replaces the file: :func:`trackstarr.sweep_cache.live_view`
    is the file as loaded plus everything judged since, which is what its next
    checkpoint writes.
    """
    global _parsed
    path = sweep_cache.cache_path()
    fingerprint = Policy.from_config().fingerprint()
    view = sweep_cache.live_view(fingerprint)
    mark = (state.stamp(path), view[0] if view else 0)
    with _lock:
        if _parsed and _parsed[0] == mark:
            return _parsed[1]
    # Always current: live_view refuses a walk loaded under other rules.
    stored = sweep_cache.Stored(view[1], True) if view else sweep_cache.read(path, fingerprint)
    with _lock:
        _parsed = (mark, stored)
    return stored


def clear() -> int:
    """Delete every stored verdict and return how many went.

    Only the cache file; the rest of STATE_DIR survives. The memos go too:
    `_cached` is on a timer, and a page still showing cleared verdicts gets
    pressed twice.
    """
    stored = _read_cache()
    dropped = len(stored.files)
    # Already gone is fine.
    with contextlib.suppress(FileNotFoundError):
        os.remove(sweep_cache.cache_path())
    forget()
    log.info("cleared %d stored verdict(s)", dropped)
    return dropped


@dataclass
class Rollup:
    """A title's files, tallied for its card."""

    files: int = 0
    bytes: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    #: Layouts the plans would add, layouts they would rebuild over an existing
    #: track, and how many tracks they would drop.
    adds: set[str] = field(default_factory=set)
    rebuilds: set[str] = field(default_factory=set)
    drops: int = 0
    #: How many of its files trackstarr has rewritten, as they now stand. Its
    #: own tally because a rewritten file passes, and a card saying only
    #: "Passed" cannot tell that from a file the rules never touched.
    fixed: int = 0
    #: When its most recently judged file was judged, in epoch seconds; 0 with
    #: no verdicts. See ``judged`` in :func:`trackstarr.sweep_cache._entry`.
    judged: float = 0.0

    @property
    def state(self) -> str:
        """The worst verdict any of its files reached."""
        return next((state for state in STATES if self.counts.get(state)), "unchecked")


class Changes(NamedTuple):
    """What one file's plan would add, rebuild and drop."""

    adds: list[str]
    rebuilds: list[str]
    drops: int


def _dropped(entry: dict) -> list[dict]:
    """The file's streams the rewrite would not carry over."""
    kept = {track.get("src") for track in entry.get("planned") or []}
    return [track for track in entry.get("tracks") or [] if track.get("index") not in kept]


def _claim(dropped: list[dict], generated: dict) -> bool:
    """Remove from ``dropped`` the track this generated one replaces.

    Mirrors :func:`trackstarr.planner._claim_replacement`, so a regenerated
    downmix is one change on a card rather than a layout gained and a track
    lost. Matched by language first: a German 2.0 dropped for a French one is
    two changes. An untagged drop is claimed by any match in its layout.
    """
    for lang in (generated.get("lang"), None):
        for at, track in enumerate(dropped):
            if (
                track.get("kind") == "audio"
                and track.get("channels") == generated.get("channels")
                and track.get("lang") == lang
            ):
                del dropped[at]
                return True
    return False


def _changes(entry: dict) -> Changes:
    """The file's plan as the three tallies a card carries.

    A generated track claims its drop before its name is checked, so an unnamed
    rebuild still keeps its predecessor out of the drop count.
    """
    dropped = _dropped(entry)
    adds: list[str] = []
    rebuilds: list[str] = []
    for track in entry.get("planned") or []:
        if "generated" not in (track.get("flags") or []):
            continue
        named = rebuilds if _claim(dropped, track) else adds
        if name := track.get("title"):
            named.append(name)
    return Changes(adds, rebuilds, len(dropped))


def _tally(entries: dict[str, dict], folders: dict[str, Title]) -> dict[str, Rollup]:
    """Every cached verdict counted under the title whose folder holds it."""
    rollups: dict[str, Rollup] = {}
    for path, entry in entries.items():
        title = innermost(folders, path)
        if title is None:
            continue
        rollup = rollups.setdefault(title.id, Rollup())
        rollup.files += 1
        rollup.bytes += entry.get("size") or 0
        stamp = entry.get("judged")
        if isinstance(stamp, int | float) and not isinstance(stamp, bool):
            rollup.judged = max(rollup.judged, stamp)
        status = str(entry.get("status") or "unchecked")
        rollup.counts[status] = rollup.counts.get(status, 0) + 1
        if entry.get("fixed"):
            rollup.fixed += 1
        if entry.get("planned"):
            changes = _changes(entry)
            rollup.adds.update(changes.adds)
            rollup.rebuilds.update(changes.rebuilds)
            rollup.drops += changes.drops
    return rollups


def _weight(rollup: Rollup) -> float:
    """How much a rewrite would change a typical file of this title.

    Layouts added or rebuilt, plus drops per file. Divided by file count, or a
    60-episode series would outscore every film on episode count alone. On the
    card because both the grid and the strip sort on it.
    """
    return len(rollup.adds) + len(rollup.rebuilds) + rollup.drops / max(rollup.files, 1)


def _card(title: Title, rollup: Rollup | None) -> dict:
    """One title as the grid draws it: enough to sort, filter and label a
    poster, nothing a sheet would show."""
    rollup = rollup or Rollup()
    # Empty optional fields are dropped; the four the grid keys on are always
    # present.
    optional = {
        "year": title.year,
        "lang": title.lang,
        # Only the sheet draws it, but its header is the card until the fetch
        # lands, and a score arriving later would shift the line.
        "rating": title.rating,
        # Whole seconds: sorted on, never counted with.
        "added": int(title.added),
        "files": rollup.files,
        "bytes": rollup.bytes,
        "counts": rollup.counts,
        "adds": sorted(rollup.adds),
        "rebuilds": sorted(rollup.rebuilds),
        "drops": rollup.drops,
        "fixed": rollup.fixed,
        "weight": round(_weight(rollup), 2),
        # The newest verdict, which a rewrite stamps by re-judging what it
        # wrote; see :func:`trackstarr.processing._rejudged`.
        "processed": int(rollup.judged),
    }
    # No verdict means either nothing downloaded (a wishlist entry) or files
    # no sweep has reached (work outstanding). Only the *arr can tell them
    # apart. Verdicts on disk beat what the *arr believes.
    state = rollup.state
    if state == "unchecked" and not title.on_disk:
        state = "missing"
    return {
        "id": title.id,
        "name": title.name,
        "kind": title.kind,
        "state": state,
        **{name: value for name, value in optional.items() if value not in (None, 0, {}, [])},
    }


class _Built(NamedTuple):
    """A built grid and the two memoised reads it came from.

    Compared by identity: both hold a library's worth of verdicts, and
    comparing those by value is the cost this exists to avoid. The answer is
    shared across threads, so callers must not mutate it.
    """

    stored: sweep_cache.Stored
    found: Shelf
    answer: dict

    def came_from(self, stored: sweep_cache.Stored, found: Shelf) -> bool:
        return self.stored is stored and self.found is found


_built: _Built | None = None


def shelf() -> dict:
    """The whole grid: every title with its rollup, worst first, then by name.

    Memoised on its inputs (see :class:`_Built`), since building it walks
    every cached verdict. :func:`_shelf` runs first because it refetches the
    *arr titles on its own timer.
    """
    global _built
    stored = _read_cache()
    found = _shelf(stored)
    with _lock:
        built = _built
    if built is not None and built.came_from(stored, found):
        return built.answer
    folders = {title.folder: title for title in found.titles}
    rollups = _tally(stored.files, folders)
    cards = [_card(title, rollups.get(title.id)) for title in found.titles]
    cards.sort(key=lambda card: (STATES.index(card["state"]), card["name"].lower()))
    answer = {
        "titles": cards,
        "complete": found.complete,
        # Whether the verdicts were reached under the rules in force.
        "current": stored.current,
        "swept": len(stored.files),
    }
    with _lock:
        _built = _Built(stored, found, answer)
    return answer


#: How many posters the overview's strip carries.
HEAD = 12


#: Sort keys by the name the browser uses. The grid sorts its own copy; the
#: strip is a dozen cut from the whole library, so only the service can order
#: it.
_ORDERS: dict[str, Callable[[dict], tuple]] = {
    "name": lambda card: (),
    "added": lambda card: (-card.get("added", 0),),
    "processed": lambda card: (-card.get("processed", 0),),
    "year": lambda card: (-(card.get("year") or 0),),
    "size": lambda card: (-card.get("bytes", 0),),
    "changes": lambda card: (-card.get("weight", 0.0),),
    "worst": lambda card: (STATES.index(card["state"]),),
}

#: The strip's default order. Not worst-first, which showed the same stuck
#: titles every day, and not added date, which says nothing about what has
#: happened since.
DEFAULT_ORDER = "processed"


def _strip_key(order: str) -> Callable[[dict], tuple]:
    """The strip's sort key for one of :data:`_ORDERS`.

    Titles with no files come last in every order, and the name breaks ties so
    the order is stable between polls. An unknown order falls back to the
    default.
    """
    chosen = _ORDERS.get(order) or _ORDERS[DEFAULT_ORDER]
    return lambda card: (0 if card.get("files") else 1, *chosen(card), card["name"].lower())


def summary(head: int = HEAD, order: str = DEFAULT_ORDER) -> dict:
    """A tally per state and the first few posters in ``order``, for the
    overview.

    Cut from :func:`shelf` so it cannot disagree with the grid. The saving is
    on the wire, not in compute.
    """
    full = shelf()
    counts: dict[str, int] = {}
    for card in full["titles"]:
        counts[card["state"]] = counts.get(card["state"], 0) + 1
    return {
        "titles": len(full["titles"]),
        "counts": counts,
        "head": sorted(full["titles"], key=_strip_key(order))[:head],
        "complete": full["complete"],
        "current": full["current"],
        "swept": full["swept"],
    }


def _find(title_id: str, stored: sweep_cache.Stored) -> Title | None:
    return _shelf(stored).index.get(title_id)


#: Files with a verdict first, in :data:`STATES` order, then the ones a rewrite
#: of ours left, then by path. Rewrites lead their verdict because they are all
#: a settled title has to read, and :data:`MAX_FILES` would otherwise cut them
#: off a long series entirely.
def _file_rank(entry: dict) -> tuple[int, int, str]:
    status = str(entry.get("status") or "unchecked")
    rank = STATES.index(status) if status in STATES else len(STATES)
    return (rank, 0 if entry.get("fixed") else 1, entry.get("path", ""))


def _file(entry: dict) -> dict:
    """One file as the sheet reads it: its verdict, what the probe saw and what
    a rewrite would leave.

    ``fixed`` is only on a file trackstarr has rewritten, and is resolved by
    :func:`_fixed` before the sort. The verdict says what the file is now,
    which for a rewritten one is Passed like any other, so without this the
    sheet could not tell the two apart.
    """
    fixed = entry.get("fixed")
    return {
        "path": entry["path"],
        "name": os.path.basename(entry["path"]),
        "status": entry.get("status") or "unchecked",
        "bytes": entry.get("size") or 0,
        "lang": entry.get("lang"),
        "tracks": entry.get("tracks") or [],
        "planned": entry.get("planned") or [],
        "why": entry.get("why") or {},
        **({"fixed": fixed} if fixed else {}),
    }


def title(title_id: str) -> dict | None:
    """One title with its files, or None for an unknown id.

    Each file carries its tracks, its planned tracks and the reasons, all from
    the cache; nothing is probed.
    """
    stored = _read_cache()
    found = _find(title_id, stored)
    if found is None:
        return None
    folders = {found.folder: found}
    entries = [
        {**entry, "path": path}
        for path, entry in stored.files.items()
        if innermost(folders, path) is not None
    ]
    entries.sort(key=_file_rank)
    files = [_file(entry) for entry in entries[:MAX_FILES]]
    return {
        "id": found.id,
        "name": found.name,
        "kind": found.kind,
        "year": found.year,
        "lang": found.lang,
        "folder": found.folder,
        "current": stored.current,
        "files": files,
        # So a series past MAX_FILES can say it is showing part of itself.
        "total": len(entries),
    }


def selected(title_ids: list[str]) -> list[Title]:
    """The titles behind a list of ids, in the order asked.

    Ids, never paths: a re-check walks what comes back, and resolving through
    the index keeps it inside the library. Unknown ids are dropped, not
    refused.
    """
    index = _shelf(_read_cache()).index
    return [found for title_id in title_ids if (found := index.get(title_id))]


def cards_for_paths(paths: Iterable[str]) -> tuple[dict[str, str], dict[str, dict]]:
    """Which title holds each path, and a card per title found.

    For the history, whose events name only a file. Two answers because a page
    of a hundred events is usually a dozen titles. A path under no title is
    absent, not an error.
    """
    wanted = {path for path in paths if path}
    if not wanted:
        return {}, {}
    stored = _read_cache()
    found = _shelf(stored)
    folders = {title.folder: title for title in found.titles}
    owners = {
        path: title.id for path in wanted if (title := innermost(folders, path)) is not None
    }
    if not owners:
        return {}, {}
    # The grid's own rollups, so a poster raised from the feed matches the
    # library's.
    rollups = _tally(stored.files, folders)
    cards = {
        title_id: _card(found.index[title_id], rollups.get(title_id))
        for title_id in set(owners.values())
    }
    return owners, cards


def cover(title_id: str) -> tuple[bytes, str] | None:
    """The title's poster and content type, or None.

    From our copy when we have one; otherwise fetched from the *arr or from
    beside the files and kept. Proxying keeps the *arr's API key out of the
    browser.
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


#: Titles nothing has a poster for, and when we last looked.
_absent: dict[str, float] = {}
_absent_lock = threading.Lock()


def _known_absent(title_id: str) -> bool:
    with _absent_lock:
        looked = _absent.get(title_id)
    return looked is not None and time.monotonic() - looked < _ABSENT_TTL


def _fetch_cover(title_id: str) -> bytes | None:
    """Ask the *arr, then the folder, and keep whatever answers."""
    if _known_absent(title_id):
        return None
    found = _find(title_id, _read_cache())
    body = _from_arr(found) or _local_cover(found.folder) if found else None
    if body:
        _keep_cover(title_id, body)
    else:
        with _absent_lock:
            _absent[title_id] = time.monotonic()
    return body


def _from_arr(found: Title) -> bytes | None:
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
        for title in _shelf(_read_cache()).titles
        if not _known_absent(title.id) and not os.path.exists(_cover_file(title.id))
    ]
    if not wanted:
        return
    log.info("fetching %d posters the library view has not seen before", len(wanted))
    with ThreadPoolExecutor(_WARM_WORKERS, thread_name_prefix="cover") as pool:
        # list() so an exception surfaces here rather than in an undrained
        # generator.
        list(pool.map(_fetch_cover, wanted))
