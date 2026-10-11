"""What each connection holds of one title, from the *arrs' own file lists.

The file endpoints decide which connection holds a file and which episodes it
contains, not folders or filenames. A connection that cannot be listed is
unavailable rather than empty, so a variant that is gone differs from one
nobody could ask about. Connection-local ids are locators, never identity.
"""

import logging
from collections import defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from . import paths
from .arr import Arr
from .client import API_ERRORS
from .planner import FileRevision

if TYPE_CHECKING:
    from .library import Title

log = logging.getLogger(__name__)

#: Why automatic sourcing leaves a variant alone, in the order they are checked.
#: Several connections list the folder holding it.
SHARED_FOLDER = "shared_folder"
#: Another variant names the same path or inode.
SAME_FILE = "same_file"
#: It holds more than one episode.
MULTI_EPISODE = "multi_episode"
#: It names no episode, an episode has no TVDB id, or another connection
#: numbers one of its episodes differently.
UNMATCHED_EPISODE = "unmatched_episode"

#: A variant's place in its connection: instance id and file id.
type Locator = tuple[str, int]


@dataclass(frozen=True, slots=True)
class ContentKey:
    """A film or episode as its metadata provider numbers it, the same in every
    connection. ``provider`` is tmdb or tvdb and ``kind`` movie or episode.
    TVDB episode ids are unique without their series."""

    provider: str
    kind: str
    provider_id: str


@dataclass(frozen=True)
class Variant:
    """One file a connection lists, and what it contains."""

    path: str
    instance_id: str
    #: The movie or series id in that connection, a locator only.
    item_id: int
    #: The movie or episode file id in that connection, a locator only.
    file_id: int
    #: One per film or episode the file holds whose provider id is known.
    content_keys: tuple[ContentKey, ...]
    #: Sonarr's numbering, ``S01E02`` per episode in file order. Empty for a film.
    episodes: tuple[str, ...] = ()
    #: Radarr's edition, such as "Director's Cut". Empty when none.
    edition: str = ""
    #: The file as it stood when listed, or None when it could not be read.
    revision: FileRevision | None = None
    #: Why automatic sourcing leaves it alone, or None.
    excluded: str | None = None

    @property
    def locator(self) -> Locator:
        return self.instance_id, self.file_id


@dataclass(frozen=True)
class Inventory:
    """Every variant of one title, and the connections that could not say."""

    title_id: str
    variants: tuple[Variant, ...]
    #: Instance ids whose listing failed. Their variants are unknown, not absent.
    unavailable: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unavailable


def take(title: Title) -> Inventory:
    """Ask every connection holding ``title`` for its files. An unclaimed
    folder has no *arr to answer for it and is left out."""
    listed: list[Variant] = []
    unavailable: list[str] = []
    shared: set[str] = set()
    for source in title.sources:
        if source.arr is None:
            continue
        try:
            listed.extend(_listed(source.arr, source.item_id, title.provider_id))
        except API_ERRORS as err:
            instance_id = source.arr.instance_id
            log.warning("%s: could not list the files of %s (%s)", instance_id, title.name, err)
            unavailable.append(instance_id)
            continue
        if source.conflicting_instances:
            shared.add(source.arr.instance_id)
    same = _same_files(listed)
    unmatched = _unmatched_episodes(listed) if title.kind == "series" else set()
    variants = tuple(
        replace(variant, excluded=_exclusion(variant, shared, same, unmatched))
        for variant in listed
    )
    return Inventory(title.id, variants, tuple(unavailable))


def _listed(arr: Arr, item_id: int, provider_id: str) -> list[Variant]:
    """One connection's files of a title. Raises API_ERRORS."""
    if arr.type == "sonarr":
        return _episode_files(arr, item_id)
    keys = (ContentKey("tmdb", "movie", provider_id),) if provider_id else ()
    return [
        _variant(arr, item_id, entry, keys, edition=str(entry.get("edition") or ""))
        for entry in arr.files(item_id)
        if entry.get("path")
    ]


def _episode_files(arr: Arr, series_id: int) -> list[Variant]:
    """A series' files, each with the episodes Sonarr says it holds."""
    held: defaultdict[int, list[dict]] = defaultdict(list)
    for episode in arr.episodes(series_id):
        if file_id := episode.get("episodeFileId"):
            held[file_id].append(episode)
    variants = []
    for entry in arr.files(series_id):
        if not entry.get("path"):
            continue
        episodes = sorted(held.get(entry["id"], []), key=_numbered)
        keys = tuple(
            ContentKey("tvdb", "episode", str(tvdb_id))
            for episode in episodes
            if (tvdb_id := episode.get("tvdbId"))
        )
        labels = tuple("S{:02d}E{:02d}".format(*_numbered(episode)) for episode in episodes)
        variants.append(_variant(arr, series_id, entry, keys, episodes=labels))
    return variants


def _numbered(episode: dict) -> tuple[int, int]:
    """Sonarr's season and episode number."""
    return episode.get("seasonNumber") or 0, episode.get("episodeNumber") or 0


def _variant(
    arr: Arr,
    item_id: int,
    entry: dict,
    keys: tuple[ContentKey, ...],
    *,
    episodes: tuple[str, ...] = (),
    edition: str = "",
) -> Variant:
    path = paths.canonical(entry["path"])
    try:
        revision = FileRevision.of(path)
    except OSError:
        revision = None
    return Variant(
        path, arr.instance_id, item_id, entry["id"], keys, episodes, edition, revision
    )


def _exclusion(
    variant: Variant, shared: set[str], same: set[Locator], unmatched: set[Locator]
) -> str | None:
    if variant.instance_id in shared:
        return SHARED_FOLDER
    if variant.locator in same:
        return SAME_FILE
    if len(variant.episodes) > 1:
        return MULTI_EPISODE
    if variant.locator in unmatched:
        return UNMATCHED_EPISODE
    return None


def _same_files(variants: Iterable[Variant]) -> set[Locator]:
    """Variants another variant also names, by path or by device and inode."""
    holders: defaultdict[Hashable, list[Locator]] = defaultdict(list)
    for variant in variants:
        holders[variant.path].append(variant.locator)
        if variant.revision is not None:
            holders[variant.revision.identity].append(variant.locator)
    return {locator for group in holders.values() if len(group) > 1 for locator in group}


def _unmatched_episodes(variants: list[Variant]) -> set[Locator]:
    """Episode files whose identity does not hold across connections."""
    numbering: defaultdict[ContentKey, set[str]] = defaultdict(set)
    identities: defaultdict[str, set[ContentKey]] = defaultdict(set)
    partial = {
        variant.locator
        for variant in variants
        if len(variant.content_keys) != len(variant.episodes)
    }
    for variant in variants:
        if variant.locator not in partial:
            for key, label in zip(variant.content_keys, variant.episodes, strict=True):
                numbering[key].add(label)
                identities[label].add(key)
    return {
        variant.locator
        for variant in variants
        if not variant.episodes
        or variant.locator in partial
        or any(len(numbering[key]) > 1 for key in variant.content_keys)
        or any(len(identities[label]) > 1 for label in variant.episodes)
    }
