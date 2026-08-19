"""Fixtures shared by the unit and integration suites.

The unit suite builds ffprobe-shaped dicts by hand. The integration suite
makes real files with ffmpeg, which :func:`pytest_configure` insists on.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import types
from dataclasses import replace

import pytest

from trackstarr import config, processing
from trackstarr.arr import Arr, radarr, sonarr
from trackstarr.executor import Outcome
from trackstarr.planner import Plan


def _mp4_titles_round_trip() -> bool:
    """Whether this ffmpeg can store a per-stream title in MP4.

    The mov muxer only learned the ``name`` atom in 8.1; before that it
    accepts the option and drops it. Two tests and their fixtures turn on it.

    Detected rather than compared against a version string, since
    distributions backport and rebuild. Costs a tenth of a second.
    """
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
    """Refuse to run at all without the ffmpeg the tests are written against.

    trackstarr is an ffmpeg wrapper, so an environment without one is
    unconfigured rather than limited. Saying so up front beats skipping a
    third of the suite where nobody looks.
    """
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
    /config."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _restore_sigterm():
    """Put the SIGTERM disposition back after every test.

    main() installs a handler and most of this suite calls it, so otherwise
    the first test to do so changes how pytest itself handles a signal.
    """
    original = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, original)


@pytest.fixture(autouse=True)
def _no_services(monkeypatch):
    """Keep locally set service env vars from leaking into any test's clients."""
    for name in (
        "RADARR_URL",
        "RADARR_API_KEY",
        "SONARR_URL",
        "SONARR_API_KEY",
        "PLEX_URL",
        "PLEX_TOKEN",
        "JELLYFIN_URL",
        "JELLYFIN_API_KEY",
    ):
        monkeypatch.setattr(config, name, "")


def fake_run(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """A subprocess.run result double, for tests that monkeypatch it."""
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def read_events() -> list[dict]:
    """Every event recorded under the test's STATE_DIR, in written order.

    The service only writes the history. This lives here because the tests
    are the only thing that reads it back.
    """
    try:
        with open(os.path.join(config.STATE_DIR, "events.jsonl")) as events_file:
            return [json.loads(line) for line in events_file if line.strip()]
    except FileNotFoundError:
        return []


#: Where each *arr really listens, so a test url reads like a real one.
_ARR_URLS = {"radarr": "http://radarr:7878", "sonarr": "http://sonarr:8989"}


def configured_arr(name: str = "radarr", key: str = "key") -> Arr:
    """A reachable-looking *arr, for the paths gated on ``Arr.enabled``.

    One with no url or key returns before it touches the network, so a test
    of a real call has to start here.
    """
    arr = sonarr() if name == "sonarr" else radarr()
    return replace(arr, url=_ARR_URLS[name], key=key)


def needed_plan(path: str = "/x.mkv", **overrides) -> Plan:
    """A plan with work to do, since ``Plan.needed`` is having a reason.

    The reason is a placeholder; most tests want *a* plan that would be
    rewritten. Pass ``reasons`` where it matters, and ``rules`` with it: the
    planner writes the two together, so setting one alone would let a test
    assert against a pairing the real thing cannot produce.
    """
    return Plan(path=path, reasons=["reorder streams"], rules={"order"}, **overrides)


@pytest.fixture
def stub_rewrite(monkeypatch):
    """Give process() a prepared plan and a canned apply_plan result.

    The outcome handling is what these tests are about, so the probe and the
    ffmpeg run are stubbed. ``stub_rewrite(plan)`` for the applied case, or
    pass an ``outcome`` and ``detail``.
    """

    def _stub(plan: Plan, outcome: Outcome = Outcome.APPLIED, detail: str = "") -> None:
        monkeypatch.setattr(processing, "build_plan", lambda path, lang: plan)
        monkeypatch.setattr(processing, "apply_plan", lambda plan: (outcome, detail))

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
    """An ffprobe-shaped audio stream.

    ``bitrate`` is the per-stream ``bit_rate`` MP4 reports and Matroska
    usually omits. Left off rather than defaulted, since "the container
    doesn't say" is a case the rules treat differently.
    """
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
