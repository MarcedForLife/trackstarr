"""Whole-episode fingerprints, extracted at the short samples' width and read as a stream."""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from visual import Frame, append_frame, check_timeline

# Four hours at 30 fps. The export is about 1.6 KB per frame, so it is never held whole.
EPISODE_SETTINGS: dict = {"width": 320, "max_frames": 432_000, "timeout_seconds": 7200}


def read_episode(path: Path) -> list[Frame]:
    frames: list[Frame] = []
    unit = None
    with path.open("rb") as stream:
        if b"<!" in stream.read(4096):
            raise ValueError("unexpected XML declaration")
        stream.seek(0)
        # Bounded local FFmpeg output with no declarations, as for the short reader.
        for _, element in ET.iterparse(stream):  # noqa: S314
            name = element.tag.rsplit("}", 1)[-1]
            if name == "MediaTimeUnit":
                unit = element.text
            elif name == "VideoFrame":
                if unit != "1000000":
                    raise ValueError("expected microsecond signature time base")
                append_frame(frames, element)
                element.clear()
                if len(frames) > EPISODE_SETTINGS["max_frames"]:
                    raise ValueError("signature frame count outside research bound")
    check_timeline(frames, EPISODE_SETTINGS["max_frames"])
    return frames


def extract_episode(binary: str, media: Path, output: Path) -> list[Frame]:
    """Decode with every thread, scale down, then keep the parsed frames, not the XML."""
    try:
        subprocess.run(
            [
                binary,
                "-nostdin",
                "-v",
                "error",
                "-copyts",
                "-i",
                str(media),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                (
                    f"scale={EPISODE_SETTINGS['width']}:-2,settb=1/1000000,"
                    f"signature=detectmode=off:format=xml:filename={output.name}"
                ),
                "-fps_mode",
                "passthrough",
                "-enc_time_base",
                "filter",
                "-f",
                "null",
                "-",
            ],
            cwd=output.parent,
            check=True,
            timeout=EPISODE_SETTINGS["timeout_seconds"],
        )
        return read_episode(output)
    finally:
        output.unlink(missing_ok=True)
