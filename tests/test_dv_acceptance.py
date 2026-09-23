"""Real DV samples supplied with DV_SAMPLE_DIR, kept outside the repository.

The directory holds hdr10.mkv and hdr10plus.mkv, short HEVC profile 8.1 clips.
Every output is a temporary copy. No source sample is replaced.
"""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import set_config, set_layouts, set_rules
from trackstarr.executor import Outcome, apply_plan
from trackstarr.media import dolby_vision, frame_sample, hdr_metadata, probe
from trackstarr.planner import build_plan
from trackstarr.policy import RULES


def run(*args):
    return subprocess.check_output(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", *args], timeout=60
    )


def nals(path):
    data = run(
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-c",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        "-",
    )
    units = [unit for unit in re.split(b"\x00\x00\x00?\x01", data) if unit]
    return [(unit[0] >> 1 & 63, hashlib.sha256(unit).hexdigest()) for unit in units]


def hashes(path, kind):
    return [
        line.split(b",")[-1].strip()
        for line in run(
            "-i", str(path), "-map", f"0:{kind}", "-c", "copy", "-f", "streamhash", "-"
        ).splitlines()
    ]


def chapter_offsets(info):
    chapters = info["chapters"]
    start = float(chapters[0]["start_time"])
    return [(float(c["start_time"]) - start, float(c["end_time"]) - start) for c in chapters]


@pytest.mark.parametrize("kind", ["hdr10", "hdr10plus"])
@pytest.mark.parametrize(
    "ext,remux", [("mkv", False), ("mp4", False), ("mp4", True), ("m4v", False), ("m4v", True)]
)
def test_real_dv_preservation(tmp_path, kind, ext, remux):
    sample_dir = os.environ.get("DV_SAMPLE_DIR")
    if not sample_dir:
        pytest.skip("set DV_SAMPLE_DIR to real profile 8.1 samples")
    sample = Path(sample_dir) / f"{kind}.mkv"
    assert sample.is_file()
    chapter_file = tmp_path / "chapters.txt"
    chapter_file.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=One\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Two\n"
    )
    subtitle = tmp_path / "sub.srt"
    subtitle.write_text("1\n00:00:00,100 --> 00:00:01,000\nPreserve this subtitle\n")
    source = tmp_path / f"input.{ext}"
    # FFmpeg requires unofficial mode to write the DV configuration into MP4.
    run(
        "-i",
        str(sample),
        "-i",
        str(chapter_file),
        "-i",
        str(subtitle),
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-map",
        "2:s",
        "-map_chapters",
        "1",
        "-c",
        "copy",
        "-c:s",
        "srt" if ext == "mkv" else "mov_text",
        "-strict",
        "unofficial",
        "-f",
        "matroska" if ext == "mkv" else "mp4",
        str(source),
    )
    before = probe(str(source))
    assert dolby_vision(before["streams"][0]).eligible
    original_nals = nals(source)
    assert any(kind == 62 for kind, _ in original_nals)
    before_audio = hashes(source, "a")
    before_subs = hashes(source, "s")
    before_text = run("-i", str(source), "-map", "0:s:0", "-f", "srt", "-")
    frames = frame_sample(str(source), 0)
    hdr_types = {side["side_data_type"] for frame in frames for side in hdr_metadata(frame)}
    assert "Mastering display metadata" in hdr_types
    assert ("HDR Dynamic Metadata SMPTE2094-40 (HDR10+)" in hdr_types) == (kind == "hdr10plus")
    set_layouts()
    set_config(WORK_DIR=str(tmp_path))
    set_rules(
        **(
            dict.fromkeys(RULES, "never")
            | {
                "dv_strip": "always",
                "stray_streams": "always",
                "remux": "always" if remux else "never",
            }
        )
    )
    plan = build_plan(str(source), "eng")
    assert plan.needed and "dv_strip" in plan.rules
    assert apply_plan(plan) == (Outcome.APPLIED, "")
    output = Path(plan.out_path)
    after = probe(str(output))
    assert dolby_vision(after["streams"][0]) is None
    assert nals(output) == [(kind, digest) for kind, digest in original_nals if kind != 62]
    assert hashes(output, "a") == before_audio
    if not remux:
        assert hashes(output, "s") == before_subs
    after_text = run("-i", str(output), "-map", "0:s:0", "-f", "srt", "-")
    stamps = rb"(\d+):(\d+):(\d+),(\d+)"
    assert re.sub(stamps, b"TIME", after_text) == re.sub(stamps, b"TIME", before_text)
    shift = float(after["streams"][0]["start_time"]) - float(before["streams"][0]["start_time"])
    for previous, current in zip(
        re.findall(stamps, before_text), re.findall(stamps, after_text), strict=True
    ):
        left, right = [
            sum(
                int(part) * scale
                for part, scale in zip(stamp, (3600, 60, 1, 0.001), strict=True)
            )
            for stamp in (previous, current)
        ]
        assert right == pytest.approx(left + shift, abs=0.002)
    for previous, current in zip(chapter_offsets(before), chapter_offsets(after), strict=True):
        assert current == pytest.approx(previous, abs=0.002)
    assert [chapter["tags"]["title"] for chapter in after["chapters"]] == ["One", "Two"]
    assert [hdr_metadata(frame) for frame in frame_sample(str(output), 0)] == [
        hdr_metadata(frame) for frame in frames
    ]
    assert not build_plan(str(output), "eng").needed
