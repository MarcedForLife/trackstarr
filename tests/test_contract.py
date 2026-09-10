"""The API as the browser reads it, written down and held.

Every type in ``web/src/lib`` is a hand copy of a dict built here, and both
suites stayed green through a renamed key. This stands the listener up, seeds
a library, runs and history, and compares each endpoint's answer with the JSON
under ``web/src/fixtures``, which ``fixtures.test.ts`` checks against the
types::

    UPDATE_FIXTURES=1 uv run pytest tests/test_contract.py

rewrites the files. The clock, zone, version and zone list are pinned below.
"""

import difflib
import itertools
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import trackstarr
from conftest import api, configured_arr, set_config, sign_in
from trackstarr import (
    config,
    connections,
    events,
    holds,
    library,
    notify,
    policy,
    rewrites,
    runs,
    settings,
    sweep,
    sweep_cache,
    users,
)
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "web" / "src" / "fixtures"
#: Wherever the package was imported from: the checkout here, site-packages
#: when CI runs the suite inside the image.
SOURCE = Path(trackstarr.__file__).resolve().parent

ZONE = "Pacific/Auckland"
#: Midday on 1 March 2026 in daylight time. Every stamp in the fixtures is a
#: fixed distance from it.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=ZoneInfo(ZONE)).timestamp()
DAY = 86400.0
#: Stands in for the package version wherever an answer carries it, so a
#: release does not rewrite every fixture.
VERSION = "0.0.0"

MOVIES = "/data/media/movies"
TV = "/data/media/tv"
DUNE = f"{MOVIES}/Dune (2024)"
DUNE_FILE = f"{DUNE}/Dune (2024) Bluray-2160p.mkv"
ARRIVAL_FILE = f"{MOVIES}/Arrival (2016)/Arrival (2016) Bluray-1080p.mkv"
SEVERANCE = f"{TV}/Severance"
EPISODE_ONE = f"{SEVERANCE}/Season 01/Severance - S01E01 - Good News About Hell.mkv"
EPISODE_TWO = f"{SEVERANCE}/Season 01/Severance - S01E02 - Half Loop.mkv"
BEAR = f"{TV}/The Bear"
HOLIDAY = f"{MOVIES}/Home Videos/Queenstown 2019.avi"
#: Beside it, and fine. The two together are the one card that reads "mixed".
WANAKA = f"{MOVIES}/Home Videos/Wanaka 2021.mkv"
CONTACT = f"{MOVIES}/Contact (1997)/Contact (1997).mkv"

#: The ids the *arr items below resolve to, for the holds placed on them.
SEVERANCE_ID = "arr:sonarr:12"
BEAR_ID = "arr:sonarr:31"

SWEEP_RUN = "2026-03-01T03:00:00+13:00#a1b2"
IMPORT_RUN = "2026-03-01T11:55:00+13:00#c3d4"
RECHECK_RUN = "2026-03-01T11:58:00+13:00#e5f6"
LAST_SWEEP = "2026-02-28T03:00:00+13:00#9f8e"
LAST_IMPORT = "2026-02-28T19:12:40+13:00#7d6c"
LAST_RECHECK = "2026-02-28T21:30:00+13:00#5b4a"


def track(index: int, kind: str, codec: str, **fields) -> dict:
    """One stream as media.track_summary spells it."""
    return {"index": index, "kind": kind, "codec": codec, **fields}


DUNE_TRACKS = [
    track(0, "video", "hevc"),
    track(1, "audio", "truehd", channels=8, lang="eng", flags=["default"]),
    track(2, "audio", "ac3", channels=6, lang="eng", bitrate=640000),
    track(3, "subtitle", "subrip", lang="eng"),
]
DUNE_PLANNED = [
    track(0, "video", "hevc", src=0),
    track(1, "audio", "truehd", channels=8, lang="eng", flags=["default"], src=1),
    track(2, "audio", "ac3", channels=6, lang="eng", bitrate=640000, src=2),
    track(
        3, "audio", "aac", channels=2, lang="eng", title="Stereo", flags=["generated"], src=1
    ),
    track(4, "subtitle", "subrip", lang="eng", src=3),
]
EPISODE_TRACKS = [
    track(0, "video", "h264"),
    track(1, "audio", "eac3", channels=6, lang="eng", flags=["default"]),
    track(2, "audio", "aac", channels=2, lang="eng", title="Stereo", flags=["generated"]),
    track(3, "subtitle", "subrip", lang="eng", flags=["forced"]),
]
#: The same episode before we rewrote it: an mp4 with a tagged 5.1 and
#: nothing stereo. The downmix lands at position 2 of what came out.
EPISODE_WAS = [
    track(0, "video", "h264"),
    track(1, "audio", "eac3", channels=6, lang="eng", title="Surround 5.1", flags=["default"]),
    track(2, "subtitle", "mov_text", lang="eng", flags=["forced"]),
]
EPISODE_ADDED = [2]


class Clock:
    """The time module with ``time()`` pinned; everything else is real so
    sockets and locks keep their timeouts. ``tick`` moves it on."""

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name: str):
        return getattr(time, name)


def stamp(when: float) -> str:
    """A moment as every ``ts`` spells it, in the pinned zone."""
    return datetime.fromtimestamp(when, ZoneInfo(ZONE)).isoformat(timespec="seconds")


@pytest.fixture
def pinned(monkeypatch, clean_registry):
    """Everything that would make two runs differ, held still. Yields the
    clock, which starts nine hours back and reaches NOW for the requests."""
    clock = Clock(NOW - 9 * 3600)
    for module in (runs, sweep, sweep_cache):
        monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(runs, "_UP", NOW - 3 * DAY)
    monkeypatch.setattr(events, "__version__", VERSION)
    monkeypatch.setattr(policy, "__version__", VERSION)
    # The history's stamps, a minute apart from an hour before NOW. Its own
    # clock rather than the registry's: the lines are written in the order
    # the page reads them back, whatever the runs are doing.
    stamps = (NOW - 3600 + 60 * minute for minute in itertools.count())
    monkeypatch.setattr(events, "timestamp", lambda: stamp(next(stamps)))
    monkeypatch.setattr(settings, "zones", lambda: [ZONE, "UTC"])
    monkeypatch.setattr(settings, "env_pinned", lambda name: name == "MEDIA_DIRS")
    monkeypatch.setattr(library, "_folder_added", lambda folder: NOW - 400 * DAY)
    set_config(TZ=ZONE)
    set_config(MEDIA_DIRS=[MOVIES, TV])
    set_config(SWEEP_AT="0 3 * * *")
    set_config(RADARR_URL="http://radarr:7878")
    set_config(SONARR_URL="http://sonarr:8989")
    set_config(PLEX_URL="http://plex:32400")
    set_config(PLEX_TOKEN="token")
    # runs.stamp() asks libc for the zone, and config left the process in the
    # machine's own at import. Put back by hand: monkeypatch restores the
    # variable after this fixture has finished, which is too late for tzset.
    previous = os.environ.get("TZ")
    os.environ["TZ"] = ZONE
    time.tzset()
    yield clock
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


@pytest.fixture
def seeded(monkeypatch, pinned):
    """A library, three runs and a page of history, as one morning might leave
    them: a sweep since three with a rewrite under way, a delivery waiting on
    it, a re-check somebody has just stopped, and last night in the history."""
    seed_library(monkeypatch, pinned)
    seed_history()
    seed_runs(pinned)
    pinned.now = NOW
    yield
    library.forget()


def seed_library(monkeypatch, clock: Clock) -> None:
    """Two *arrs answering for three titles, a folder none of them claims, and
    a cached verdict of each kind a card can say, including the two clips that
    leave their folder mixed."""
    radarr = configured_arr("radarr")
    sonarr = configured_arr("sonarr")
    items = {
        "radarr": [
            {
                "id": 7,
                "title": "Dune",
                "year": 2024,
                "path": DUNE,
                "titleSlug": "693134",
                "added": "2024-03-01T09:30:00Z",
                "hasFile": True,
                "originalLanguage": {"name": "English"},
            }
        ],
        "sonarr": [
            {
                "id": 12,
                "title": "Severance",
                "year": 2022,
                "path": SEVERANCE,
                "titleSlug": "severance",
                "added": "2025-01-17T20:00:00Z",
                "statistics": {"episodeFileCount": 2},
                "originalLanguage": {"name": "English"},
            },
            # Tracked and never downloaded: the one card with no file behind it.
            {
                "id": 31,
                "title": "The Bear",
                "year": 2022,
                "path": BEAR,
                "titleSlug": "the-bear",
                "added": "2026-02-27T08:15:00Z",
                "statistics": {"episodeFileCount": 0},
                "originalLanguage": {"name": "English"},
            },
        ],
    }
    monkeypatch.setattr(library, "all_arrs", lambda: [radarr, sonarr])
    monkeypatch.setattr(type(radarr), "all_items", lambda self: items[self.name])
    library.forget()

    os.makedirs(config.STATE_DIR, exist_ok=True)
    cache = SweepCache(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    clock.now = NOW - 2 * DAY
    cache.record(
        DUNE_FILE,
        FileKey(58_720_256_000, 1_709_284_200_000_000_000, 1, "eng"),
        Verdict(
            Status.PENDING,
            "add 2.0 downmix",
            tracks=DUNE_TRACKS,
            planned=DUNE_PLANNED,
            why={"reasons": ["add 2.0 downmix (from track 1)"], "rules": ["downmix"]},
            duration=9330.0,
        ),
    )
    clock.tick(DAY)
    episode_one = FileKey(2_251_799_813, 1_740_704_400_000_000_000, 1, "eng")
    cache.record(
        EPISODE_ONE,
        episode_one,
        Verdict(Status.CONFORM, "", tracks=EPISODE_TRACKS, duration=3420.0),
    )
    # Passed because we rewrote it, which is the one thing the verdict cannot
    # say. The same rewrite the history's "modified" line records, which is
    # where the reasons in prose stay.
    rewrites.record(
        EPISODE_ONE,
        episode_one,
        {
            "at": stamp(clock.now),
            "bytes_before": 2_147_483_648,
            "bytes_after": 2_251_799_813,
            "was": EPISODE_WAS,
            "added": EPISODE_ADDED,
        },
    )
    clock.tick(300)
    cache.record(
        EPISODE_TWO,
        FileKey(2_147_483_648, 1_740_704_700_000_000_000, 1, "eng"),
        Verdict(
            Status.FAILED,
            "ffmpeg exited 1",
            tracks=EPISODE_TRACKS[:2] + EPISODE_TRACKS[3:],
            why={
                "failed": "ffmpeg exited 1: Conversion failed!",
                "reasons": ["add 2.0 downmix (from track 1)"],
                "rules": ["downmix"],
            },
            duration=3305.0,
            failures=2,
        ),
    )
    cache.record(
        HOLIDAY,
        FileKey(734_003_200, 1_561_939_200_000_000_000, 1, None),
        Verdict(
            Status.UNSUPPORTED,
            ".avi is not a container the rules rewrite",
            why={"skip": ".avi is not in ALLOWED_EXTS"},
        ),
    )
    cache.record(
        WANAKA,
        FileKey(1_073_741_824, 1_625_097_600_000_000_000, 1, None),
        Verdict(Status.CONFORM, "", duration=612.0),
    )
    cache.save()


def seed_history() -> None:
    """One line of every event the service records, in the order a day writes
    them, with the fields each call site passes. The pause and the resume go
    through the registry, which is what records them."""
    in_force = Policy.from_config()
    digest = in_force.digest()
    events.record_config(in_force)
    events.record(
        "sweep",
        run=LAST_SWEEP,
        dry_run=False,
        files=1180,
        stopped=None,
        library_bytes=24_189_255_811_072,
        config=in_force.fingerprint(),
        config_id=digest,
        cached=1174,
        counts={"conform": 1177, "modified": 1, "failed": 1, "unsupported": 1},
        seconds=5423.5,
    )
    events.record(
        "modified",
        run=LAST_SWEEP,
        source="sweep",
        config_id=digest,
        reasons=["remux to mkv (RULE_REMUX)", "add 2.0 downmix (from track 1)"],
        rules=["downmix", "remux"],
        seconds=1834.2,
        waited=12.5,
        duration=3420.0,
        path=EPISODE_ONE,
        from_path=EPISODE_ONE.removesuffix(".mkv") + ".mp4",
        incidental=["clear release tags on audio 1 ('Surround 5.1')"],
        incidental_rules=["release_tags"],
        downmixed=["2.0"],
        bytes_before=2_147_483_648,
        bytes_after=2_251_799_813,
    )
    events.record(
        "deferred",
        run=LAST_SWEEP,
        source="sweep",
        config_id=digest,
        reasons=["add 2.0 downmix (from track 1)"],
        rules=["downmix"],
        seconds=0.4,
        waited=None,
        duration=3305.0,
        path=EPISODE_TWO,
        detail="hard-linked 2 times; a download client still has it",
    )
    events.record(
        "failed",
        run=LAST_SWEEP,
        source="sweep",
        config_id=digest,
        reasons=["add 2.0 downmix (from track 1)"],
        rules=["downmix"],
        seconds=95.3,
        waited=None,
        duration=3305.0,
        path=EPISODE_TWO,
        detail="ffmpeg exited 1: Conversion failed!",
    )
    events.record("webhook", run=LAST_IMPORT, arr="radarr", files=1, paths=[DUNE_FILE])
    events.record(
        "pending",
        run=LAST_IMPORT,
        source="webhook",
        config_id=digest,
        path=DUNE_FILE,
        reasons=["add 2.0 downmix (from track 1)"],
        rules=["downmix"],
        incidental=[],
        incidental_rules=[],
        downmixed=["2.0"],
    )
    events.record(
        "recheck",
        run=LAST_RECHECK,
        dry_run=True,
        titles=1,
        files=2,
        stopped=None,
        config_id=digest,
        counts={"conform": 1, "failed": 1},
        seconds=41.7,
    )
    # A title held for an evening and let go the same night: both lines, and
    # nothing left standing from this pass. The one still on is in seed_runs.
    holds.place(
        SEVERANCE,
        4 * 3600,
        by="admin",
        reason="watching it",
        title=SEVERANCE_ID,
        name="Severance",
    )
    holds.lift(SEVERANCE, by="admin")
    events.record("skipped", run=LAST_SWEEP, path=EPISODE_TWO, by="admin")
    # A track edited in place: the stream, and both sides of each tag moved.
    events.record(
        "retagged",
        path=EPISODE_TWO,
        index=1,
        kind="audio",
        changed={
            "lang": {"from": "und", "to": "jpn"},
            "commentary": {"from": False, "to": True},
        },
        by="admin",
    )
    assert runs.pause(by="admin")
    assert runs.resume(by="admin")
    # Both sides on both names: a name set for the first time moves from "",
    # which is what settings._changes writes and all it can write.
    events.record(
        "settings",
        changed={
            "PLEX_URL": {"from": "", "to": "http://plex:32400"},
            "RULE_COMMENTARY": {"from": "never", "to": "always"},
        },
        by="admin",
    )
    events.record("webhook", run=IMPORT_RUN, arr="radarr", files=1, paths=[ARRIVAL_FILE])


def seed_runs(clock: Clock) -> None:
    """A sweep nine hours in with one file encoding and two queued behind it,
    a delivery waiting on the slot, and a re-check asked to stop."""
    clock.now = NOW - 9 * 3600
    runs.open_run(SWEEP_RUN, runs.SWEEP)
    runs.set_total(SWEEP_RUN, 1180)
    runs.walking(SWEEP_RUN, False)
    for _ in range(1174):
        runs.tally(SWEEP_RUN, "conform", cached=True)
    clock.now = NOW - 5400
    runs.begin(SWEEP_RUN, EPISODE_TWO)
    clock.tick(95.3)
    runs.finish(SWEEP_RUN, EPISODE_TWO)
    runs.tally(SWEEP_RUN, "failed", path=EPISODE_TWO, detail="ffmpeg exited 1")
    runs.begin(SWEEP_RUN, EPISODE_ONE)
    clock.tick(1834.2)
    runs.finish(SWEEP_RUN, EPISODE_ONE)
    runs.tally(SWEEP_RUN, "modified", path=EPISODE_ONE, detail="add 2.0 downmix")
    runs.queue(SWEEP_RUN, DUNE_FILE, 9330.0)
    runs.queue(SWEEP_RUN, f"{MOVIES}/Blade Runner (1982)/Blade Runner (1982).mkv", 7020.0)
    runs.queue(SWEEP_RUN, CONTACT, 0.0)
    # One of the queued files taken off this sweep, and a title held past it.
    assert runs.skip(SWEEP_RUN, CONTACT) == "waiting"
    holds.place(BEAR, 0, by="admin", reason="not until I say", title=BEAR_ID, name="The Bear")
    clock.now = NOW - 1500
    runs.begin(SWEEP_RUN, DUNE_FILE)
    runs.stage(SWEEP_RUN, DUNE_FILE, runs.ENCODING, 9330.0)
    clock.tick(1200)
    runs.progress(SWEEP_RUN, DUNE_FILE, 1800.0, 1.5)

    clock.now = NOW - 300
    runs.open_run(IMPORT_RUN, runs.IMPORT, label="radarr", filling=True)
    runs.add_file(IMPORT_RUN)
    runs.begin(IMPORT_RUN, ARRIVAL_FILE)
    runs.stage(IMPORT_RUN, ARRIVAL_FILE, runs.WAITING)
    runs.seal(IMPORT_RUN)

    clock.now = NOW - 120
    runs.open_run(RECHECK_RUN, runs.RECHECK, dry_run=True, label="Severance")
    runs.set_total(RECHECK_RUN, 2)
    assert runs.stop(RECHECK_RUN)


@pytest.fixture
def ask(listener, fast_scrypt, seeded):
    """One signed-in request, answering the JSON and insisting on a 200."""
    users.add("admin", "right password", "admin")
    cookie = sign_in(listener, "admin")

    def _ask(method: str, path: str, body: dict | None = None) -> dict:
        status, answer, _ = api(listener, method, path, body, cookie=cookie)
        assert status == 200, answer
        return answer

    return _ask


def hold(name: str, answer: dict) -> None:
    """Compare an answer with its committed fixture, or rewrite the fixture
    under UPDATE_FIXTURES=1. Fails with the diff, values only: the order the
    service spells its keys in is not part of the contract."""
    path = FIXTURES / f"{name}.json"
    if os.environ.get("UPDATE_FIXTURES"):
        path.write_text(json.dumps(answer, indent="\t", ensure_ascii=False) + "\n")
        return
    if not path.exists():
        pytest.fail(f"{path.relative_to(ROOT)} is missing; UPDATE_FIXTURES=1 writes it")
    committed = json.loads(path.read_text())
    if committed == answer:
        return
    diff = difflib.unified_diff(
        json.dumps(committed, indent=2, sort_keys=True).splitlines(),
        json.dumps(answer, indent=2, sort_keys=True).splitlines(),
        fromfile=str(path.relative_to(ROOT)),
        tofile="the service's answer",
        lineterm="",
    )
    pytest.fail(
        f"{name}.json no longer matches the service. UPDATE_FIXTURES=1 rewrites it, "
        "after which `npm run check` in web/ says whether the types still fit.\n"
        + "\n".join(diff)
    )


def test_runs(ask):
    hold("runs", ask("GET", "/api/runs"))


def test_library(ask):
    hold("library", ask("GET", "/api/library"))


def test_summary(ask):
    hold("summary", ask("GET", "/api/library/summary?sort=processed"))


def test_title(ask):
    hold("title", ask("GET", "/api/library/title?id=arr%3Asonarr%3A12"))


def test_events(ask):
    hold("events", ask("GET", "/api/events"))


def test_holds(ask):
    hold("holds", ask("GET", "/api/holds"))


def test_settings(ask):
    hold("settings", ask("GET", "/api/settings"))


def test_connection(ask, monkeypatch):
    """The check itself calls the service named, so it is answered here; the
    shape of the answer is the contract."""
    monkeypatch.setattr(
        connections,
        "check",
        lambda name, url, key: connections.Result(
            True,
            "Radarr 5.14.0.9383, 312 films",
            hint="Map /data to /media in PLEX_PATH_MAP so Plex finds the rewrites.",
            webhook="connected",
        ),
    )
    body = {"service": "radarr", "url": "http://radarr:7878", "key": "typed"}
    hold("connection", ask("POST", "/api/connections/test", body))


def recorded_events() -> set[str]:
    """Every name the package passes to events.record, read off the source, so
    a new event without a seeded line fails and so does a stale one."""
    names: set[str] = set()
    for module in SOURCE.glob("*.py"):
        names.update(re.findall(r'\brecord\(\s*"([a-z-]+)"', module.read_text()))
    return names


def test_the_history_says_every_event_the_service_records(ask):
    page = ask("GET", "/api/events")
    assert {entry["event"] for entry in page["events"]} == recorded_events()


def test_vocabulary(ask, monkeypatch):
    """The lists both sides spell out by hand, for the frontend to compare
    with its own. The kinds published during the run are checked against the
    list here too, so a publish of a kind nothing lists fails on this side."""
    published: set[str] = set()
    monkeypatch.setattr(notify, "publish", published.add)
    runs.pause(by="admin")
    runs.resume(by="admin")
    runs.progress(SWEEP_RUN, DUNE_FILE, 1810.0, 1.5)
    assert published <= set(notify.KINDS)
    hold(
        "vocabulary",
        {
            "states": list(library.STATES),
            # The word a card leads with when nothing is outstanding and its
            # files disagree. Apart from the states: no file is ever in it.
            "mixed": library.MIXED,
            # What the grid's chips offer: the states, plus one thing a file is
            # rather than a state it is in.
            "filters": list(library.FILTERS),
            "services": [
                {"name": service.name, "label": service.label}
                for service in connections.SERVICES
            ],
            "kinds": list(notify.KINDS),
        },
    )
