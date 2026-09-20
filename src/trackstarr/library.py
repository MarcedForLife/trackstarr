"""The library view: every title, and what the sweep made of its files.

The *arrs (Radarr and Sonarr) know the titles, years, posters and folders; the
sweep cache knows what each file needs. Titles are the spine and cached
verdicts hang off them by folder, using the sweep's own lexical match. Files
under no title are grouped by their top folder rather than dropped.

Nothing here probes or plans. Every verdict shown was written to the cache by
the sweep or by :func:`trackstarr.sweep.remember`; an unswept library is an
empty answer, not a reason to walk it inside a web request. What a rewrite of
ours left a file comes from :mod:`trackstarr.rewrites`, joined on by path.
"""

import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import NamedTuple

from . import config, notify, ratings, rewrites, state, sweep_cache
from .arr import Arr, all_arrs, innermost, original_of, source_name
from .client import API_ERRORS
from .planner import Changes, changes
from .policy import Policy
from .status import Status

log = logging.getLogger(__name__)

#: The word the view puts under each *arr's titles.
KINDS = {"radarr": "movie", "sonarr": "series"}

#: How long a fetched title list is reused. Listing a whole library is one
#: heavy request per *arr; short enough that a new title shows without a
#: restart.
_INDEX_TTL = 300.0

#: Files no sweep has reached, or a rewrite nothing has looked at since.
#: Computed here, so it is no :class:`trackstarr.status.Status`.
UNCHECKED = "unchecked"

#: A title the *arrs track with nothing downloaded. Computed here too.
MISSING = "missing"

#: Worst first: where a card ranks, and the word it leads with while anything is
#: outstanding. The last two are the absence of a verdict, and "missing"
#: (nothing downloaded) is not work outstanding. "unsupported" sits below "skip"
#: because a skip is usually momentary and an unsupported container is
#: permanent. Neither remaining Status is here on purpose: a modified or
#: deferred file is outside :data:`trackstarr.sweep.CACHEABLE_STATUSES`, so
#: nothing stores one for a card to count.
STATES = (
    Status.FAILED,
    Status.PENDING,
    Status.SKIP,
    Status.UNSUPPORTED,
    Status.CONFORM,
    UNCHECKED,
    MISSING,
)

#: The verdicts that mean work outstanding. One file in either decides its
#: card's word: the grid is a triage queue before it is a report.
ACTIONABLE = (Status.FAILED, Status.PENDING)

#: What a card reads once nothing is outstanding and its files still disagree.
#: Not a verdict: no file is ever in it, and no chip offers it, since a mixed
#: title is reachable under each state it holds.
MIXED = "mixed"

#: Titles a rewrite of ours has been through, counted from
#: :mod:`trackstarr.rewrites`. A rewritten file passes, so the count of them is
#: the only place a title says any of its files are ours.
MODIFIED = Status.MODIFIED

#: The card field holding that count, which :func:`summary` reads back. Spelt
#: like :data:`MODIFIED` and not the same thing: a wire key the frontend reads
#: as ``Card.modified``, so renaming the verdict must not move it. A file's own
#: ``modified`` record is a third key again; see :func:`_file`.
_MODIFIED_FIELD = "modified"

#: Titles holding an audio track with no language tag. Not a verdict, a file
#: that passes can carry one, and the languages and downmix rules cannot read
#: it until :mod:`trackstarr.retag` puts one on.
UNTAGGED = "untagged"

#: What a chip row cuts the grid by. Hiding reads :data:`STATES` instead, since
#: it goes on the word a card leads with and none of the three above is one.
FILTERS = (
    Status.FAILED,
    Status.PENDING,
    Status.SKIP,
    Status.UNSUPPORTED,
    Status.CONFORM,
    MODIFIED,
    UNTAGGED,
    UNCHECKED,
    MISSING,
)

#: The chips a card answers with a count instead of a verdict, as card field
#: and chip name. :func:`summary` counts these off the cards.
_COUNTED_FIELDS = ((_MODIFIED_FIELD, MODIFIED), (UNTAGGED, UNTAGGED))

#: Groups per detail page. Variants of a film or numbered episode stay together.
#: Latest seasons and episodes come first. Unnumbered files have their own group.
MAX_FILES = 200


@dataclass(frozen=True)
class Source:
    """One folder holding a title, and the *arr claiming it.

    A title in two *arr instances has two, primary first. ``arr`` is None for a
    folder no *arr claims. ``conflicting_instances`` names the other instances
    claiming this same folder. The first configured one owns it.
    """

    folder: str
    arr: Arr | None = None
    item_id: int = 0
    slug: str = ""
    conflicting_instances: tuple[str, ...] = ()


@dataclass(frozen=True)
class Title:
    """One movie or series, wherever its variants live.

    Both *arrs and the sweep's unclaimed folders end up as one of these. The
    same title in several instances is one Title with several ``sources``. The
    first configured instance is primary and supplies ``id``, ``folder``,
    ``arr``, ``item_id`` and ``slug``. ``folder`` is in this container's paths,
    as the cache keys are. Primary-source fields are derived from ``sources[0]``.
    """

    id: str
    name: str
    sources: tuple[Source, ...]
    kind: str
    year: int | None = None
    lang: str | None = None
    #: When the title joined the library, in epoch seconds; 0 when unknown.
    #: The *arr's ``added``, or the folder's mtime for an unclaimed title.
    added: float = 0.0
    #: The IMDb id both *arrs carry, ``tt`` and digits.
    imdb_id: str = ""
    #: The IMDb score out of ten, or None. From :func:`trackstarr.ratings.scores`.
    rating: float | None = None
    #: Whether any source says something is downloaded. False is a tracked
    #: title with no file to judge, distinct from one no sweep has reached; see
    #: :func:`_card`.
    on_disk: bool = True
    #: The TMDB id for a film, the TVDB id for a series: what one title is
    #: called in every instance, so variants merge on it. Empty when unknown.
    provider_id: str = ""

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("a title needs at least one source")

    @property
    def folder(self) -> str:
        return self.sources[0].folder

    @property
    def arr(self) -> Arr | None:
        return self.sources[0].arr

    @property
    def item_id(self) -> int:
        return self.sources[0].item_id

    @property
    def slug(self) -> str:
        return self.sources[0].slug

    @property
    def folders(self) -> tuple[str, ...]:
        """Every folder holding the title, primary first."""
        return tuple(source.folder for source in self.sources)


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
    #: The same titles keyed by id, since a grid asks for one cover per poster,
    #: and by each source's own id, so an id a secondary instance minted still
    #: finds its title.
    index: dict[str, Title] = field(default_factory=dict)


_lock = threading.Lock()
_cached: tuple[float, Shelf] | None = None


class Identity(NamedTuple):
    """The immutable activity projection, without verdicts or service clients."""

    id: str
    name: str


class Catalogue:
    """One bounded acquisition worker shared by library and activity reads.

    No network or verdict-cache work runs under this lock. A reset fences an
    old response but retains its future until it finishes, so repeated settings
    changes cannot queue workers. Only full-library callers wait for acquisition.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="title-labels")
        self.future: Future | None = None
        self.generation = 0
        self.connection: tuple = ()
        self.labels: Mapping[str, Identity] = MappingProxyType({})
        self.unclaimed: dict[str, Identity] = {}
        self.services: dict[str, dict[str, Title]] = {}
        self.index: Mapping[str, Title] = MappingProxyType({})
        self.result: tuple[dict[str, Title], bool] | None = None
        self.expires = 0.0
        self.failures = 0
        self.closed = False

    def invalidate(self) -> None:
        """Invalidate full titles (including ratings), retaining same-server labels."""
        with self.lock:
            self.generation += 1
            self.result = None
            self.expires = 0.0

    def request(self) -> tuple[Mapping[str, Identity], Future | None]:
        with self.lock:
            arrs = all_arrs()
            connection = tuple((arr.instance_id, arr.url, arr.key) for arr in arrs)
            if connection != self.connection:
                unchanged = set(self.connection) & set(connection)
                self.services = {
                    name: self.services[name]
                    for name, url, key in connection
                    if (name, url, key) in unchanged and name in self.services
                }
                self.connection = connection
                self.generation += 1
                self.unclaimed = {}
                self.labels, self.index = self._identities()
                self.result = None
                self.expires = 0.0
                self.failures = 0
            if (
                not self.closed
                and time.monotonic() >= self.expires
                and (self.future is None or self.future.done())
            ):
                self.future = self.executor.submit(self.refresh, self.generation, arrs)
            return self.labels, self.future

    def refresh(self, generation: int, arrs: list[Arr]) -> None:
        acquired = _from_arrs(arrs)
        complete = all(fetched is not None for fetched in acquired.values())
        with self.lock:
            if generation != self.generation or self.closed:
                return
            # Retain the last successful source catalogue during an outage.
            # Removal/reconfiguration prunes it in request(); a healthy empty
            # response removes its titles. Reads and actions share this view.
            for name, fetched in acquired.items():
                if fetched is not None:
                    self.services[name] = fetched
            titles = self._merged()
            self.result = titles, complete
            labels, self.index = self._identities(titles)
            changed = self.labels != labels
            self.labels = labels
            if complete:
                self.failures = 0
                self.expires = time.monotonic() + _INDEX_TTL
            else:
                self.failures = min(self.failures + 1, 6)
                self.expires = time.monotonic() + min(15 * 2 ** (self.failures - 1), 300)
        if changed:
            notify.publish(notify.RUNS)

    def _merged(self) -> dict[str, Title]:
        return _merged(
            title
            for name, _, _ in self.connection
            for title in self.services.get(name, {}).values()
        )

    def _identities(
        self, titles: dict[str, Title] | None = None
    ) -> tuple[Mapping[str, Identity], Mapping[str, Title]]:
        """Publish labels and aliases from the same source ownership decisions."""
        titles = self._merged() if titles is None else titles
        labels = {folder: Identity(title.id, title.name) for folder, title in titles.items()}
        local = [
            Title(label.id, label.name, (Source(folder),), "folder")
            for folder, label in self.unclaimed.items()
            if folder not in titles
        ]
        return (
            MappingProxyType({**self.unclaimed, **labels}),
            MappingProxyType(_title_index([*titles.values(), *local])),
        )

    def resolve(self, title_ids: list[str]) -> dict[str, Title]:
        """Resolve aliases locally without waiting for acquisition or verdict reads."""
        self.request()
        with self.lock:
            return {key: self.index[key] for key in title_ids if key in self.index}

    def read(self) -> tuple[dict[str, Title], bool]:
        while True:
            _, future = self.request()
            if future is not None:
                future.result()
            with self.lock:
                if self.result is not None:
                    return self.result
                if self.closed:
                    return {}, False

    def include_folders(self, claimed: dict[str, Title], titles: list[Title]) -> None:
        """Copy locally discovered folders while the full view already has them."""
        with self.lock:
            if self.result is None or self.result[0] is not claimed or self.closed:
                return
            self.unclaimed = {
                title.folder: Identity(title.id, title.name)
                for title in titles
                if title.arr is None
            }
            labels, self.index = self._identities()
            changed = labels != self.labels
            self.labels = labels
        if changed:
            notify.publish(notify.RUNS)

    def stop(self) -> None:
        """Fence publication and join the worker, bounded by client HTTP timeouts."""
        with self.lock:
            self.closed = True
            self.generation += 1
            self.labels = MappingProxyType({})
            self.index = MappingProxyType({})
            self.result = None
        self.executor.shutdown(wait=True)


_catalogue = Catalogue()


def start_refresh() -> None:
    """Warm activity identities even when only the overview is open."""
    _catalogue.request()


def stop_refresh() -> None:
    _catalogue.stop()


def reset_refresh() -> None:
    """Join the previous worker before resetting lifecycle state (tests/restart)."""
    global _catalogue
    stop_refresh()
    _catalogue = Catalogue()


def forget() -> None:
    """Drop full-library memos so the next read refetches.

    Called when the *arr settings change, and by tests. The parsed cache and
    the built grid go too: neither is keyed on the rules, so a settings save
    must not serve an answer judged under the old ones. Activity identities
    survive policy/ratings changes. request() fences changed connections.
    """
    global _built, _cached, _parsed
    _catalogue.invalidate()
    with _lock:
        _built = None
        _cached = None
        _parsed = None


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


def _arr_id(arr: Arr, item_id: int) -> str:
    """The id an *arr title is known by: instance and item, so two instances
    numbering from one never collide."""
    return f"arr:{arr.instance_id}:{item_id}"


def _title_of(arr: Arr, item: dict, scored: dict[str, float]) -> Title | None:
    """One *arr object as a Title, or None if it has no folder.

    A folder that normalises to nothing is the root, which would claim every
    file. ``scored`` is :func:`trackstarr.ratings.scores`, read once per shelf.
    """
    folder = (item.get("path") or "").rstrip("/")
    if not folder or not item.get("id"):
        return None
    imdb_id = str(item.get("imdbId") or "").strip()
    slug = str(item.get("titleSlug") or "")
    return Title(
        id=_arr_id(arr, item["id"]),
        name=item.get("title") or os.path.basename(folder),
        sources=(Source(folder, arr, item["id"], slug),),
        kind=KINDS.get(arr.type, "title"),
        year=item.get("year") or None,
        lang=original_of(item),
        added=_epoch(item.get("added")),
        imdb_id=imdb_id,
        rating=scored.get(imdb_id),
        on_disk=_on_disk(item),
        provider_id=str(item.get("tmdbId" if arr.type == "radarr" else "tvdbId") or ""),
    )


def _instance(source: Source) -> str:
    """The instance claiming a source, empty for an unclaimed folder."""
    return source.arr.instance_id if source.arr else ""


def _merge_key(title: Title) -> tuple[str, str] | None:
    """What variants of one title across instances share: the provider id, else
    the IMDb id, with the kind. None for a title carrying neither, which stands
    alone. Matching names would merge the wrong films silently."""
    if key := title.provider_id or title.imdb_id:
        return title.kind, key
    return None


def _merged(titles: Iterable[Title]) -> dict[str, Title]:
    """Every folder to its title, one Title per provider id across instances.

    First wins on a duplicate folder, matching :func:`trackstarr.arr.path_index`,
    and the later claimant is recorded on the folder's source. Titles from
    different instances sharing a merge key become one Title with its sources
    in connection order, so the first configured instance is primary and
    decides the id. Every source folder then maps to that one Title.
    """
    by_folder: dict[str, Title] = {}
    for title in titles:
        source = title.sources[0]
        if owner := by_folder.get(source.folder):
            claimed = replace(
                owner.sources[0],
                conflicting_instances=(
                    *owner.sources[0].conflicting_instances,
                    _instance(source),
                ),
            )
            by_folder[source.folder] = replace(owner, sources=(claimed,))
        else:
            by_folder[source.folder] = title
    merged: dict[tuple[str, str], Title] = {}
    for title in by_folder.values():
        if (key := _merge_key(title)) is None:
            continue
        if head := merged.get(key):
            merged[key] = replace(
                head,
                sources=(*head.sources, *title.sources),
                on_disk=head.on_disk or title.on_disk,
            )
        else:
            merged[key] = title
    for title in merged.values():
        for source in title.sources:
            by_folder[source.folder] = title
    return by_folder


def _folders(titles: Iterable[Title]) -> dict[str, Title]:
    """Every folder to its title, the shape :func:`trackstarr.arr.innermost` reads."""
    return {folder: title for title in titles for folder in title.folders}


def _from_arrs(arrs: list[Arr]) -> dict[str, dict[str, Title] | None]:
    """Titles per service. None distinguishes a failure from an empty response.

    First wins on a duplicate folder, matching :func:`trackstarr.arr.path_index`.
    """
    services: dict[str, dict[str, Title] | None] = {}
    scored = ratings.scores()
    for arr in arrs:
        try:
            fetched = arr.all_items()
        except API_ERRORS as err:
            log.warning(
                "%s: could not list its titles for the library view (%s)", arr.instance_id, err
            )
            services[arr.instance_id] = None
            continue
        titles: dict[str, Title] = {}
        for item in fetched:
            if title := _title_of(arr, item, scored):
                titles.setdefault(title.folder, title)
        services[arr.instance_id] = titles
    return services


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
    for media_dir in config.current().MEDIA_DIRS:
        root = media_dir.rstrip("/")
        if not root or not path.startswith(root + os.sep):
            continue
        rest = path[len(root) + 1 :]
        head = rest.split(os.sep)[0]
        return os.path.join(root, head) if os.sep in rest else None
    return None


def _title_index(titles: Iterable[Title]) -> dict[str, Title]:
    """Canonical IDs and source aliases, shared by shelf reads and local actions."""
    index: dict[str, Title] = {}
    for title in titles:
        index[title.id] = title
        for source in title.sources:
            if source.arr:
                index.setdefault(_arr_id(source.arr, source.item_id), title)
    return index


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
    # Catalogue acquisition is shared with the activity refresh worker.
    claimed, complete = _catalogue.read()
    folders = dict(claimed)
    for path in stored.files:
        if innermost(claimed, path) is not None:
            continue
        folder = _unclaimed(path)
        if folder and folder not in folders:
            folders[folder] = Title(
                id=f"dir:{folder}",
                name=os.path.basename(folder),
                sources=(Source(folder),),
                kind="folder",
                added=_folder_added(folder),
            )
    # A merged title sits under each of its folders. The grid draws it once.
    titles = list({id(title): title for title in folders.values()}.values())
    shelf = Shelf(titles, complete, stored.current, _title_index(titles))
    _catalogue.include_folders(claimed, titles)
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

    Only the cache file; the rest of STATE_DIR survives, the rewrite records
    included. The memos go too: `_cached` is on a timer, and a page still
    showing cleared verdicts gets pressed twice.
    """
    dropped = sweep_cache.clear()
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
    modified: int = 0
    #: How many hold an audio track with no language tag. Its own tally because
    #: such a file usually passes, so no verdict marks it.
    untagged: int = 0
    #: When its most recently judged file was judged, in epoch seconds; 0 with
    #: no verdicts. See ``judged`` in :func:`trackstarr.sweep_cache._entry`.
    judged: float = 0.0

    @property
    def worst(self) -> str:
        """The worst verdict any of its files reached."""
        return next((state for state in STATES if self.counts.get(state)), UNCHECKED)

    @property
    def state(self) -> str:
        """The word its card leads with.

        The worst verdict while anything is outstanding, since one pending file
        among thirty passes is still a rewrite owed. Once nothing is, a title
        whose files disagree reads :data:`MIXED` rather than taking the name of
        its one skipped file.
        """
        worst = self.worst
        if worst in ACTIONABLE:
            return worst
        judged = sum(1 for state in STATES if self.counts.get(state))
        return MIXED if judged > 1 else worst


def _changes(entry: dict) -> Changes:
    """The tally for one cache entry."""
    return changes(entry.get("planned") or [], entry.get("tracks") or [])


def _untagged(entry: dict) -> bool:
    """Whether the probe found an audio track with no language tag.

    ``track_summary`` drops an empty field and ``und`` is already None by then,
    so an absent key is the whole test. Video streams carry no language the
    rules read, so only audio counts.
    """
    return any(
        track.get("kind") == "audio" and not track.get("lang")
        for track in entry.get("tracks") or []
    )


def _tally(
    entries: dict[str, dict], folders: dict[str, Title], made: dict[str, dict]
) -> dict[str, Rollup]:
    """Every cached verdict counted under the title whose folder holds it.
    ``made`` is :func:`trackstarr.rewrites.against`."""
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
        status = str(entry.get("status") or UNCHECKED)
        rollup.counts[status] = rollup.counts.get(status, 0) + 1
        if path in made:
            rollup.modified += 1
        if _untagged(entry):
            rollup.untagged += 1
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


def _verdict(title: Title, rollup: Rollup) -> str:
    """The word a title leads with.

    No verdict means either nothing downloaded (a wishlist entry) or files no
    sweep has reached (work outstanding). Only the *arr can tell them apart.
    Verdicts on disk beat what the *arr believes.
    """
    verdict = rollup.state
    if verdict == UNCHECKED and not title.on_disk:
        return MISSING
    return verdict


def _card(title: Title, rollup: Rollup | None) -> dict:
    """One title as the grid draws it: enough to sort, filter and label a
    poster, nothing a sheet would show."""
    rollup = rollup or Rollup()
    # Empty optional fields are dropped; the four the grid keys on are always
    # present.
    # A named instance is worth a word on the card; the label is read here, so
    # a rename reaches every card without a resweep.
    named = title.arr if title.arr and "-" in title.arr.instance_id else None
    optional = {
        "source": source_name(named.instance_id) if named else None,
        # How many instances hold it. One is the ordinary case and says nothing.
        "source_count": len(title.sources) if len(title.sources) > 1 else None,
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
        _MODIFIED_FIELD: rollup.modified,
        UNTAGGED: rollup.untagged,
        "weight": round(_weight(rollup), 2),
        # The newest verdict, which a rewrite stamps by re-judging what it
        # wrote; see :func:`trackstarr.processing._rejudged`.
        "processed": int(rollup.judged),
    }
    return {
        "id": title.id,
        "name": title.name,
        "kind": title.kind,
        "state": _verdict(title, rollup),
        **{name: value for name, value in optional.items() if value not in (None, 0, {}, [])},
    }


def _rank(card: dict) -> int:
    """Where a card sits in the worst-first order.

    On the worst verdict its files reached rather than the word it leads with,
    so a mixed title sits with the state that made it one, not in a block of its
    own.
    """
    counts = card.get("counts") or {}
    if not counts:
        # Nothing to rank on: whichever of the two _card settled on.
        state = card["state"]
        return STATES.index(state) if state in STATES else len(STATES)
    return next((at for at, state in enumerate(STATES) if counts.get(state)), len(STATES))


class _Built(NamedTuple):
    """A built grid and the memoised reads it came from.

    Compared by identity: each holds a library's worth of records, and
    comparing those by value is the cost this exists to avoid. The answer is
    shared across threads, so callers must not mutate it.
    """

    stored: sweep_cache.Stored
    found: Shelf
    made: dict[str, rewrites.Rewrite]
    answer: dict

    def came_from(
        self, stored: sweep_cache.Stored, found: Shelf, made: dict[str, rewrites.Rewrite]
    ) -> bool:
        return self.stored is stored and self.found is found and self.made is made


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
    made = rewrites.records()
    with _lock:
        built = _built
    if built is not None and built.came_from(stored, found, made):
        return built.answer
    folders = _folders(found.titles)
    rollups = _tally(stored.files, folders, rewrites.against(stored.files))
    cards = [_card(title, rollups.get(title.id)) for title in found.titles]
    cards.sort(key=lambda card: (_rank(card), card["name"].lower()))
    answer = {
        "titles": cards,
        "complete": found.complete,
        # Whether the verdicts were reached under the rules in force.
        "current": stored.current,
        "swept": len(stored.files),
    }
    conflicts = [
        {
            "folder": source.folder,
            "owner": {"id": _instance(source), "name": source_name(_instance(source))},
            "others": [
                {"id": identity, "name": source_name(identity)}
                for identity in source.conflicting_instances
            ],
        }
        for title in found.titles
        for source in title.sources
        if source.conflicting_instances
    ]
    if conflicts:
        answer["conflicts"] = conflicts
    with _lock:
        _built = _Built(stored, found, made, answer)
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
    "worst": lambda card: (_rank(card),),
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

    Counted by membership, as the grid's chips are: a title with files in two
    states falls under each, so the tally sums past ``titles``. A count promises
    what pressing its chip lands on.
    """
    full = shelf()
    counts: dict[str, int] = {}
    for card in full["titles"]:
        held = card.get("counts") or {}
        for verdict in STATES:
            if held.get(verdict) if held else card["state"] == verdict:
                counts[verdict] = counts.get(verdict, 0) + 1
        for field_name, chip in _COUNTED_FIELDS:
            if card.get(field_name):
                counts[chip] = counts.get(chip, 0) + 1
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


def _file_rank(entry: dict) -> tuple[int, int, str]:
    """Latest season and episode first, with unnumbered files last."""
    path = entry.get("path", "")
    episode = re.search(r"\bS(\d{1,3})E(\d{1,3})", os.path.basename(path), re.IGNORECASE)
    if episode:
        return (-int(episode[1]), -int(episode[2]), path)
    season = re.search(r"/Season[ ._-]*(\d{1,3})/", path, re.IGNORECASE)
    return (-int(season[1]) if season else 1, 0, path)


def _file(entry: dict, source: Source | None = None) -> dict:
    """One file as the sheet reads it: its verdict, what the probe saw and what
    a rewrite would leave.

    ``source`` is the folder holding it. An *arr-owned one labels the file, so
    a title held in two instances can say which file is whose.

    ``modified`` is only on a file trackstarr has rewritten and still holds a
    claim on, and is merged in by :func:`title` before the sort. The verdict
    says what the file is now, which for a rewritten one is Passed like any
    other, so without this the sheet could not tell the two apart.

    ``seconds`` is the running time, which is what turns a track's rate into
    the space it takes.
    """
    modified = entry.get("modified")
    return {
        "path": entry["path"],
        "name": os.path.basename(entry["path"]),
        **({"source": source_name(source.arr.instance_id)} if source and source.arr else {}),
        "status": entry.get("status") or UNCHECKED,
        "bytes": entry.get("size") or 0,
        "seconds": entry.get("duration") or 0,
        "lang": entry.get("lang"),
        "tracks": entry.get("tracks") or [],
        "planned": entry.get("planned") or [],
        "why": entry.get("why") or {},
        **({"modified": modified} if modified else {}),
    }


def file_detail(path: str, *, card: bool = False) -> dict:
    """The saved verdict and plan for one exact path, without reading or probing it.

    The card means tallying every verdict under the title, which is the whole
    cost of the answer, so only a caller opening the title asks for one.
    """
    stored = _read_cache()
    entry = stored.files.get(path)
    owners, cards = cards_for_paths([path]) if card else ({}, {})
    return {
        "card": cards.get(owners.get(path, "")),
        "current": stored.current,
        "file": _file({**entry, "path": path}) if entry is not None else None,
    }


def _variant_key(path: str, kind: str) -> str:
    """Match frontend grouping, enforced by shared variant-groups.json fixtures."""
    if kind == "movie":
        return "film"
    episode = re.search(
        r"\bS\d{1,3}E\d{1,3}(?:(?:-?E|-)\d{1,3})*\b",
        os.path.basename(path),
        re.IGNORECASE | re.ASCII,
    )
    return episode[0].upper() if kind == "series" and episode else path


def title(title_id: str, *, pages: int = 1) -> dict | None:
    """One title with its files, or None for an unknown id.

    Each file carries its tracks, its planned tracks and the reasons, all from
    the cache; nothing is probed.
    """
    stored = _read_cache()
    found = _find(title_id, stored)
    if found is None:
        return None
    folders = _folders([found])
    sources = {source.folder: source for source in found.sources}
    made = rewrites.against(stored.files)
    rollup = _tally(stored.files, folders, made).get(found.id, Rollup())
    entries = [
        {**entry, "path": path, **({"modified": made[path]} if path in made else {})}
        for path, entry in stored.files.items()
        if innermost(folders, path) is not None
    ]
    entries.sort(key=_file_rank)
    # Keep every alternative with its episode across page boundaries.
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(_variant_key(entry["path"], found.kind), []).append(entry)
    files = [
        _file(entry, innermost(sources, entry["path"]))
        for group in list(groups.values())[: MAX_FILES * pages]
        for entry in group
    ]
    return {
        "id": found.id,
        "name": found.name,
        "kind": found.kind,
        # The card's word, read again here: a sheet left open through a
        # rewrite has only the card it opened with.
        "state": _verdict(found, rollup),
        "counts": rollup.counts,
        "year": found.year,
        "lang": found.lang,
        "folder": found.folder,
        "current": stored.current,
        "files": files,
        # So a series past MAX_FILES can say it is showing part of itself.
        "total": len(entries),
        # Every folder holding it and whose it is, primary first. An unclaimed
        # folder has no source to name.
        "folders": [
            {
                "source": source_name(_instance(source)) if source.arr else "",
                "folder": source.folder,
            }
            for source in found.sources
        ],
    }


def known() -> Shelf:
    """Every title, in order and keyed by id. The way in for anything outside
    this module wanting a :class:`Title` rather than a card."""
    return _shelf(_read_cache())


def selected(title_ids: list[str]) -> list[Title]:
    """The titles behind a list of ids, in the order asked.

    Ids, never paths: a re-check walks what comes back, and resolving through
    the index keeps it inside the library. Unknown ids are dropped, not
    refused.
    """
    index = known().index
    return [found for title_id in title_ids if (found := index.get(title_id))]


def pause_targets(title_ids: list[str], *, strict: bool = False) -> list[tuple[str, str, str]]:
    """Every source folder behind canonical IDs or aliases, resolved locally.

    Unknown IDs are dropped unless strict is requested. Then the entire request
    fails before any pause changes. Persisted labels always use the canonical ID.
    """
    found = _catalogue.resolve(title_ids)
    if strict and set(found) != set(title_ids):
        raise KeyError("no such title")
    titles = {title.id: title for title in found.values()}
    return [
        (folder, title.id, title.name) for title in titles.values() for folder in title.folders
    ]


def covers_for_paths(paths: Iterable[str]) -> dict[str, dict]:
    """Small title identities for live rows, without calculating library verdicts."""
    # Deduped in the order given, so the answer does not reshuffle per process.
    wanted = dict.fromkeys(path for path in paths if path)
    if not wanted:
        return {}
    folders, _ = _catalogue.request()
    return {
        path: {"id": title.id, "name": title.name}
        for path in wanted
        if (title := innermost(folders, path)) is not None
    }


def plans_for_paths(paths: Iterable[str]) -> tuple[dict[str, dict], bool]:
    """What a rewrite would do to each path, and whether the rules behind those
    answers still stand.

    For rows that name a file before anything opens it. A path with no stored
    verdict is absent, which is how a queue row says it has yet to be checked.
    Both halves come off one read, so freshness cannot describe a capture the
    tallies were not taken from.
    """
    wanted = dict.fromkeys(path for path in paths if path)
    if not wanted:
        return {}, True
    stored = _read_cache()
    plans: dict[str, dict] = {}
    for path in wanted:
        entry = stored.files.get(path)
        if entry is None:
            continue
        changes = _changes(entry)
        why = entry.get("why") or {}
        plans[path] = {
            "status": entry.get("status") or UNCHECKED,
            # Every line the open panel lists, including the ride-alongs.
            "changes": len(why.get("reasons") or []) + len(why.get("incidental") or []),
            "adds": changes.adds,
            "rebuilds": changes.rebuilds,
            "drops": changes.drops,
        }
    return plans, stored.current


def plan_fields(path: str) -> dict:
    """What a rewrite of the path would do, in the fields a history line
    carries. Empty where nothing is stored."""
    entry = _read_cache().files.get(path)
    if entry is None:
        return {}
    why = entry.get("why") or {}
    tally = _changes(entry)
    told = {
        "reasons": why.get("reasons"),
        "incidental": why.get("incidental"),
        "rules": why.get("rules"),
        "incidental_rules": why.get("incidental_rules"),
        "adds": tally.adds,
        "rebuilds": tally.rebuilds,
        "drops": tally.drops,
    }
    return {name: value for name, value in told.items() if value}


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
    folders = _folders(found.titles)
    owners = {
        path: title.id for path in wanted if (title := innermost(folders, path)) is not None
    }
    if not owners:
        return {}, {}
    # The grid's own rollups, so a poster raised from the feed matches the
    # library's.
    rollups = _tally(stored.files, folders, rewrites.against(stored.files))
    cards = {
        title_id: _card(found.index[title_id], rollups.get(title_id))
        for title_id in set(owners.values())
    }
    return owners, cards
