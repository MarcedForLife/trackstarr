"""Whole-episode signatures are validated like short ones and never left on disk."""

import shutil
import subprocess

import pytest
from episode_signatures import extract_episode, read_episode
from test_visual import xml


def test_streaming_reader_matches_the_short_format(tmp_path):
    path = tmp_path / "episode.xml"
    path.write_text(xml((0, 41_000, 83_000)))
    assert [f.time_us for f in read_episode(path)] == [0, 41_000, 83_000]


@pytest.mark.parametrize(
    "raw",
    [xml((100, 100)), xml((100, 0)), xml(unit=1000), "<!DOCTYPE foo><Mpeg7/>", xml((0,))],
)
def test_streaming_reader_rejects_malformed_exports(tmp_path, raw):
    path = tmp_path / "bad.xml"
    path.write_text(raw)
    with pytest.raises(ValueError):
        read_episode(path)


def test_extract_keeps_timestamps_and_removes_the_export(tmp_path):
    media = tmp_path / "offset.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=2",
            "-vf",
            "setpts=PTS+2/TB",
            "-c:v",
            "ffv1",
            str(media),
        ],
        check=True,
        timeout=20,
    )
    output = tmp_path / "signature.xml"
    frames = extract_episode(shutil.which("ffmpeg"), media, output)
    assert frames[0].time_us == 2_000_000
    assert len(frames) == 48
    assert not output.exists()
