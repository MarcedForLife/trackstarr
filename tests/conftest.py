"""Fixtures shared by the unit and integration suites.

The unit suite builds ffprobe-shaped dicts by hand so the rules can be tested
without media. The integration suite generates real files with ffmpeg and is
skipped when ffmpeg is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from trackstarr import config

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

requires_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg and ffprobe not on PATH")


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
) -> dict:
    """An ffprobe-shaped audio stream."""
    return {
        "index": index,
        "codec_type": "audio",
        "codec_name": "ac3",
        "channels": channels,
        "tags": _tags(lang, title),
        "disposition": {"default": default, "comment": comment},
    }


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
        # One map entry per input added so far, so len(maps) is always the
        # index of the input about to be appended.
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
