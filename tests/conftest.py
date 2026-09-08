"""Fixtures shared by the unit and integration suites.

The unit suite builds ffprobe-shaped dicts by hand. The integration suite
makes real files with ffmpeg, which :func:`pytest_configure` insists on.
"""

import http.client
import importlib
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

import trackstarr
from trackstarr import config, holds, processing, runs, users, webhook
from trackstarr.arr import Arr, radarr, sonarr
from trackstarr.executor import Outcome
from trackstarr.planner import Plan


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


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    """Point STATE_DIR at the test's tmp dir, so nothing reaches a real
    /config, and drop whatever settings file a real one held at import."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "_SETTINGS", {})
    monkeypatch.setattr(config, "WEB_DIR", "")
    # Memoised on the file's mark, which two tmp dirs can share.
    holds.forget()


#: Environment names settings_state scrubs so a config reload cannot pick up
#: a developer's real services or state directory mid-test.
_RELOAD_SENSITIVE = (
    "STATE_DIR",
    # Would point every reload at a developer's real key instead of the one
    # the test's own state directory mints.
    "TRACKSTARR_KEY_FILE",
    # The developer's own zone, which config would otherwise apply to the
    # process running the suite.
    "TZ",
    "RADARR_URL",
    "RADARR_API_KEY",
    "SONARR_URL",
    "SONARR_API_KEY",
    "PLEX_URL",
    "PLEX_TOKEN",
    "JELLYFIN_URL",
    "JELLYFIN_API_KEY",
    # The browser-side addresses too: each is enough on its own to put an
    # "Open in" link on a title, so a developer's own would show up in tests
    # that assert a sheet offers none.
    "RADARR_PUBLIC_URL",
    "SONARR_PUBLIC_URL",
    "PLEX_PUBLIC_URL",
    "JELLYFIN_PUBLIC_URL",
)


@pytest.fixture
def settings_state(tmp_path):
    """STATE_DIR pinned through the environment, for tests that run
    settings.update(): its config reload discards attribute patches, so the
    isolation must be in the environment. Teardown reloads with the real one.
    """
    previous = {name: os.environ.get(name) for name in _RELOAD_SENSITIVE}
    for name in _RELOAD_SENSITIVE:
        os.environ.pop(name, None)
    os.environ["STATE_DIR"] = str(tmp_path / "state")
    # What the deploy said about the zone, read once at package import: a
    # developer's own would pin TZ and refuse every write a test makes.
    stated_tz, trackstarr.ENV_TZ = trackstarr.ENV_TZ, ""
    importlib.reload(config)
    yield tmp_path / "state"
    trackstarr.ENV_TZ = stated_tz
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    importlib.reload(config)


@pytest.fixture(autouse=True)
def _reset_accounts():
    """Login throttling and the timing dummy are module state; a lockout must
    not outlive the test that earned it."""
    yield
    with users._failures_lock:
        users._failures.clear()
    users._dummy_hash = None


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


@pytest.fixture(autouse=True)
def _no_services(monkeypatch):
    """Keep locally set service env vars from leaking into any test's clients."""
    for name in (
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
    ):
        monkeypatch.setattr(config, name, "")


def set_rules(monkeypatch, **modes: str) -> None:
    """Set named rules' modes, leaving the rest at their defaults:
    ``set_rules(monkeypatch, remux="always", sdh="never")``."""
    monkeypatch.setattr(config, "RULE_MODES", dict(config.RULE_MODES) | modes)


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


def sign_in(server, name: str, password: str = "right password") -> str:
    """A session cookie for an existing account, as the Cookie header value."""
    status, _, headers = api(
        server, "POST", "/api/auth/login", {"username": name, "password": password}
    )
    assert status == 200
    return headers["Set-Cookie"].split(";")[0]


@pytest.fixture
def clean_registry():
    """The activity registry and the pause are module state; neither may
    outlive the test that made it, least of all a pause."""
    runs._runs.clear()
    runs._running.set()
    runs.forget_logs()
    yield runs
    runs._runs.clear()
    runs._running.set()
    runs.forget_logs()
