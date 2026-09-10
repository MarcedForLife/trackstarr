"""Fixtures shared by the unit and integration suites.

The unit suite builds ffprobe-shaped dicts by hand. The integration suite
makes real files with ffmpeg, which :func:`pytest_configure` insists on.
"""

import http.client
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import types
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

from trackstarr import (
    config,
    covers,
    holds,
    jobs,
    library,
    processing,
    rewrites,
    runlog,
    runs,
    users,
    webhook,
)
from trackstarr.arr import Arr, radarr, sonarr
from trackstarr.executor import Outcome
from trackstarr.planner import Plan
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict


def _mp4_titles_round_trip() -> bool:
    """Whether this ffmpeg can store a per-stream title in MP4. The mov muxer
    learned the ``name`` atom in 8.1. Detected rather than version-compared,
    since distributions backport."""
    with tempfile.TemporaryDirectory() as work:
        sample = os.path.join(work, "probe.mp4")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=d=0.1",
                    "-c:a",
                    "aac",
                    "-metadata:s:a:0",
                    "title=Commentary",
                    sample,
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            probed = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream_tags",
                    "-of",
                    "json",
                    sample,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.SubprocessError, OSError:
            return False
    tags = (json.loads(probed.stdout).get("streams") or [{}])[0].get("tags", {})
    return "Commentary" in (tags.get("name", ""), tags.get("title", ""))


def pytest_configure() -> None:
    """Refuse to run without ffmpeg: an environment without one is
    unconfigured, and skipping a third of the suite would go unnoticed."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise pytest.UsageError("ffmpeg and ffprobe must be on PATH to run the tests")
    if not _mp4_titles_round_trip():
        raise pytest.UsageError(
            "this ffmpeg drops per-stream titles in MP4; 8.1 or newer is needed "
            "(distribution builds are usually older, including 8.0)"
        )


def set_config(**values) -> None:
    """Make these settings live for one test: ``set_config(MEDIA_DIRS=[root])``.

    Names and shapes are config.Settings', which is what the parsers left, so
    a layout list arrives as a tuple. Undone by _isolated_state.
    """
    config.apply(replace(config.current(), **values))


#: The services a developer's own environment sets, blanked for every test.
#: Each is enough on its own to put an "Open in" link on a title, so one would
#: show up in tests asserting a sheet offers none.
_SERVICES = (
    "RADARR_URL",
    "RADARR_API_KEY",
    "RADARR_PUBLIC_URL",
    "SONARR_URL",
    "SONARR_API_KEY",
    "SONARR_PUBLIC_URL",
    "PLEX_URL",
    "PLEX_TOKEN",
    "PLEX_PUBLIC_URL",
    "JELLYFIN_URL",
    "JELLYFIN_API_KEY",
    "JELLYFIN_PUBLIC_URL",
)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    """Point STATE_DIR at the test's tmp dir, so nothing reaches a real
    /config, and hand every test the settings a bare install has."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    set_config(file={}, WEB_DIR="", **dict.fromkeys(_SERVICES, ""))
    # Memoised on the file's mark, which two tmp dirs can share.
    holds.forget()
    rewrites.forget()
    yield
    config.reset()


#: Environment names settings_state scrubs, since a save reads both sources
#: back and would otherwise pick up a developer's real services.
_SAVE_SENSITIVE = (
    # Would point every read at a developer's real key instead of the one the
    # test's own state directory mints.
    "TRACKSTARR_KEY_FILE",
    # The developer's own zone, which config would otherwise apply to the
    # process running the suite.
    "TZ",
    *_SERVICES,
)


@pytest.fixture
def settings_state(monkeypatch, tmp_path):
    """For tests that run settings.update(): the save reads the environment
    and the file back, so the isolation has to be in the environment rather
    than in the snapshot the other tests patch."""
    for name in _SAVE_SENSITIVE:
        monkeypatch.delenv(name, raising=False)
    # What the deploy said about the zone, read once at package import: a
    # developer's own would pin TZ and refuse every write a test makes.
    monkeypatch.setattr(config, "STATED_TZ", "")
    config.apply(config.load())
    return tmp_path / "state"


@pytest.fixture(autouse=True)
def _reset_accounts():
    yield
    users.reset()


@pytest.fixture(autouse=True)
def _drain_queue():
    """Here rather than in one file: a POST to the listener queues too."""
    yield
    jobs.reset()


@pytest.fixture(autouse=True)
def _no_worker_threads(monkeypatch):
    """A real worker waits on the queue for ever, and a settings save brings the
    pool up. Left running, one drains the queue of every test that follows."""
    monkeypatch.setattr(jobs, "worker", lambda: None)
    monkeypatch.setattr(jobs, "_workers", 0)
    monkeypatch.setattr(jobs, "_worker_names", 0)


@pytest.fixture
def fast_scrypt(monkeypatch):
    """Interactive-cost scrypt would make this suite crawl. Each hash stores
    its own parameters, so tiny ones exercise the same paths; the one test of
    the real cost settings skips this."""
    monkeypatch.setattr(users, "_SCRYPT_N", 8)


@pytest.fixture(autouse=True)
def _restore_sigterm():
    """Put the SIGTERM disposition back after every test, since main() installs
    a handler."""
    original = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, original)


def set_rules(**modes: str) -> None:
    """Set named rules' modes, leaving the rest at their defaults:
    ``set_rules(remux="always", sdh="never")``."""
    set_config(RULE_MODES=config.current().RULE_MODES | modes)


def set_layouts(*entries: str) -> None:
    """AUDIO_LAYOUTS in order, each entry as the service reads it:
    ``set_layouts("2.0", "5.1:eac3:448k", "7.1:remove")``."""
    set_config(AUDIO_LAYOUTS=tuple(entries))


def set_langs(*entries: str) -> None:
    """LANGUAGES in order, each entry as the service reads it:
    ``set_langs("original", "eng:keep", "hin:remove")``."""
    set_config(LANGUAGES=tuple(entries))


def fake_run(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """A subprocess.run result double, for tests that monkeypatch it."""
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def read_events() -> list[dict]:
    """Every event recorded under the test's STATE_DIR, in written order."""
    try:
        with open(os.path.join(config.STATE_DIR, "events.jsonl")) as events_file:
            return [json.loads(line) for line in events_file if line.strip()]
    except FileNotFoundError:
        return []


#: Where each *arr really listens, so a test url reads like a real one.
_ARR_URLS = {"radarr": "http://radarr:7878", "sonarr": "http://sonarr:8989"}


def configured_arr(name: str = "radarr", key: str = "key") -> Arr:
    """A reachable-looking *arr, for the paths gated on ``Arr.enabled``."""
    arr = sonarr() if name == "sonarr" else radarr()
    return replace(arr, url=_ARR_URLS[name], key=key)


@pytest.fixture
def media(tmp_path) -> str:
    root = tmp_path / "media" / "movies"
    root.mkdir(parents=True)
    set_config(MEDIA_DIRS=[str(root)])
    return str(root)


def movie(
    item_id: int,
    title: str,
    folder: str,
    year: int = 2024,
    added: str = "2024-01-01T00:00:00Z",
) -> dict:
    return {
        "id": item_id,
        "title": title,
        "year": year,
        "path": folder,
        "added": added,
        "originalLanguage": {"name": "English"},
    }


def stub_arrs(monkeypatch, items: list[dict], name: str = "radarr") -> None:
    """One reachable *arr answering with these movies."""
    arr = configured_arr(name)
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])
    monkeypatch.setattr(type(arr), "all_items", lambda self: items)


def cache(*entries: tuple[str, Verdict], size: int = 100) -> None:
    """Write a sweep cache holding these verdicts, as a sweep would."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    store = SweepCache(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), Policy.from_config().fingerprint()
    )
    for path, verdict in entries:
        store.record(path, FileKey(size, 1, 1, "eng"), verdict)
    store.save()


#: What a rewrite of ours leaves behind; see
#: :func:`trackstarr.processing._modified`.
REWROTE = {
    "at": "2026-03-01T12:00:00+13:00",
    "bytes_before": 2_000,
    "bytes_after": 1_800,
    "added": [1],
}


def rewrote(*paths: str, made: dict | None = None, size: int = 100) -> None:
    """Book a rewrite of ours against these paths, keyed as :func:`cache` keys
    its verdicts so the two join."""
    for path in paths:
        rewrites.record(path, FileKey(size, 1, 1, "eng"), made or REWROTE)


def pending() -> Verdict:
    return Verdict(
        Status.PENDING,
        "add 2.0 downmix from stream 1 (6ch eng)",
        tracks=[
            {"index": 0, "kind": "video", "codec": "h264"},
            {"index": 1, "kind": "audio", "codec": "eac3", "channels": 6, "lang": "eng"},
        ],
        planned=[
            {"index": 0, "src": 0, "kind": "video", "codec": "h264"},
            {
                "index": 1,
                "src": 1,
                "kind": "audio",
                "codec": "aac",
                "channels": 2,
                "title": "2.0",
                "flags": ["generated"],
            },
            {"index": 2, "src": 1, "kind": "audio", "codec": "eac3", "channels": 6},
        ],
        why={"reasons": ["add 2.0 downmix from stream 1 (6ch eng)"], "rules": ["downmix"]},
    )


def needed_plan(path: str = "/x.mkv", **overrides) -> Plan:
    """A plan with work to do. Pass ``reasons`` where it matters, and ``rules``
    with it, since the planner writes the two together."""
    return Plan(path=path, reasons=["reorder streams"], rules={"order"}, **overrides)


@pytest.fixture
def stub_rewrite(monkeypatch):
    """Give process() a prepared plan and a canned apply_plan result.
    ``stub_rewrite(plan)`` for the applied case, or pass ``outcome`` and
    ``detail``."""

    def _stub(plan: Plan, outcome: Outcome = Outcome.APPLIED, detail: str = "") -> None:
        monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
        monkeypatch.setattr(
            processing,
            "apply_plan",
            lambda plan, on_progress=None, on_encoded=None: (outcome, detail),
        )

    return _stub


def _tags(lang: str | None, title: str) -> dict:
    tags = {}
    if lang:
        tags["language"] = lang
    if title:
        tags["title"] = title
    return tags


def audio(
    index: int,
    channels: int,
    lang: str | None = "eng",
    title: str = "",
    default: int = 0,
    comment: int = 0,
    bitrate: str | None = None,
) -> dict:
    """An ffprobe-shaped audio stream. ``bitrate`` is left off rather than
    defaulted, since an unreported rate is a case the rules treat differently."""
    stream = {
        "index": index,
        "codec_type": "audio",
        "codec_name": "ac3",
        "channels": channels,
        "tags": _tags(lang, title),
        "disposition": {"default": default, "comment": comment},
    }
    if bitrate is not None:
        stream["bit_rate"] = bitrate
    return stream


def video(index: int = 0, codec: str = "h264", attached_pic: int = 0) -> dict:
    return {
        "index": index,
        "codec_type": "video",
        "codec_name": codec,
        "disposition": {"attached_pic": attached_pic},
    }


def subtitle(
    index: int,
    lang: str | None = "eng",
    title: str = "",
    forced: int = 0,
    hearing_impaired: int = 0,
) -> dict:
    return {
        "index": index,
        "codec_type": "subtitle",
        "codec_name": "subrip",
        "tags": _tags(lang, title),
        "disposition": {"forced": forced, "hearing_impaired": hearing_impaired},
    }


def probe_data(*streams: dict, duration: float = 1000.0, title: str = "") -> dict:
    fmt: dict = {"duration": str(duration)}
    if title:
        fmt["tags"] = {"title": title}
    return {"streams": list(streams), "format": fmt}


@pytest.fixture
def make_file(tmp_path):
    """Build a real mkv with the given audio layout. Returns its path."""

    def _make(
        name: str,
        audio_specs: list[tuple[int, str, str]],
        subs: list[tuple[str, str]] | None = None,
        cover_art: bool = False,
    ) -> str:
        dest = tmp_path / name
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=24:duration=2",
        ]
        # One entry per input so far, so len(maps) is the index of the next.
        maps = ["0:v"]
        for _ in audio_specs:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000"]
            maps.append(f"{len(maps)}:a")
        for i in range(len(subs or [])):
            srt = tmp_path / f"sub{i}.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n")
            cmd += ["-i", str(srt)]
            maps.append(str(len(maps)))
        if cover_art:
            png = tmp_path / "cover.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:size=32x32:duration=1",
                    "-frames:v",
                    "1",
                    str(png),
                ],
                check=True,
            )
            cmd += ["-i", str(png)]
            maps.append(f"{len(maps)}:v")

        for spec in maps:
            cmd += ["-map", spec]

        cmd += ["-c:v:0", "libx264", "-preset", "ultrafast"]
        for i, (channels, lang, title) in enumerate(audio_specs):
            # ac3 tops out at 5.1, so bigger layouts get flac.
            codec = "aac" if channels <= 2 else "ac3" if channels <= 6 else "flac"
            cmd += [
                f"-c:a:{i}",
                codec,
                f"-ac:a:{i}",
                str(channels),
                f"-metadata:s:a:{i}",
                f"language={lang}",
            ]
            if title:
                cmd += [f"-metadata:s:a:{i}", f"title={title}"]
        # MP4 carries text subtitles as mov_text; everything else takes srt.
        sub_codec = "mov_text" if name.endswith((".mp4", ".m4v")) else "srt"
        for i, (lang, sub_title) in enumerate(subs or []):
            cmd += [f"-c:s:{i}", sub_codec, f"-metadata:s:s:{i}", f"language={lang}"]
            if sub_title:
                cmd += [f"-metadata:s:s:{i}", f"title={sub_title}"]
        if cover_art:
            cmd += ["-c:v:1", "png", "-disposition:v:1", "attached_pic"]

        cmd.append(str(dest))
        subprocess.run(cmd, check=True, capture_output=True)
        return str(dest)

    return _make


@pytest.fixture
def listener():
    """A live Handler on a loopback socket."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), webhook.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def api(server, method: str, path: str, body=None, cookie: str = "", headers=None):
    """One API request, returning (status, JSON payload, response headers). A
    dict or list body goes as JSON unless the test supplies a content type."""
    all_headers = dict(headers or {})
    if cookie:
        all_headers["Cookie"] = cookie
    payload = None
    if body is not None:
        all_headers.setdefault("Content-Type", "application/json")
        payload = json.dumps(body).encode()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        conn.request(method, path, payload, all_headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        conn.close()


def request(server, method: str, path: str, body: bytes | None = None, headers=None):
    """One HTTP request against a listener, returning (status code, JSON body)."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def get_raw(server, path: str) -> tuple[int, dict, bytes]:
    """One GET, returning (status, headers, body) with the body unparsed."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def keep_alive(server):
    """A connection the caller drives request by request, so a test can see
    whether the listener leaves it usable."""
    return http.client.HTTPConnection("127.0.0.1", server.server_address[1])


def sign_in(server, name: str, password: str = "right password") -> str:
    """A session cookie for an existing account, as the Cookie header value."""
    status, _, headers = api(
        server, "POST", "/api/auth/login", {"username": name, "password": password}
    )
    assert status == 200
    return headers["Set-Cookie"].split(";")[0]


@pytest.fixture
def clean_registry():
    """Cleared both sides: an assertion that fails mid-test still has to hand
    the suite back a running service."""
    runs.reset()
    runlog.forget()
    yield runs
    runs.reset()
    runlog.forget()


@pytest.fixture
def web_dir(tmp_path):
    """A built UI: the shell plus one hashed asset, with WEB_DIR pointing at it."""
    root = tmp_path / "webui"
    (root / "_app" / "immutable").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>trackstarr</title>")
    (root / "_app" / "immutable" / "app.abc123.js").write_text("console.log('hi')")
    set_config(WEB_DIR=str(root))
    return root


@pytest.fixture
def one_title(monkeypatch, tmp_path):
    """One film, swept once: the *arr answering for the title and a cached
    verdict under it. Yields the title's folder."""
    root = tmp_path / "media" / "movies"
    folder = root / "Dune (2024)"
    folder.mkdir(parents=True)
    set_config(MEDIA_DIRS=[str(root)])
    arr = configured_arr()
    monkeypatch.setattr(library, "all_arrs", lambda: [arr])
    monkeypatch.setattr(
        type(arr),
        "all_items",
        lambda self: [
            {
                "id": 7,
                "title": "Dune",
                "year": 2024,
                "path": str(folder),
                # What Radarr's own pages route on, which is its TMDB id.
                "titleSlug": "693134",
            }
        ],
    )
    library.forget()
    os.makedirs(config.STATE_DIR, exist_ok=True)
    swept = SweepCache(
        os.path.join(config.STATE_DIR, "sweep-cache.json"), Policy.from_config().fingerprint()
    )
    swept.record(
        str(folder / "Dune.mkv"),
        FileKey(10, 1, 1, "eng"),
        Verdict(Status.PENDING, "add 2.0 downmix", tracks=[{"index": 0, "kind": "video"}]),
    )
    swept.save()
    yield str(folder)
    library.forget()
    covers.forget()
