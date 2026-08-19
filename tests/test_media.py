"""probe's failure paths, and the tag readers that meet junk values.

A sweep points ffprobe at a whole library, so it will eventually meet a
truncated download, a file on a stalled mount, and a container whose tags say
something ridiculous. Each has to become a ProbeError or a None the planner
already knows how to skip, never a traceback that abandons the walk.
"""

import json
import subprocess

import pytest

from conftest import audio, fake_run
from trackstarr import config, media
from trackstarr.media import ProbeError, duration, probe, stream_bitrate


def test_probe_returns_the_parsed_json(monkeypatch):
    payload = {"streams": [audio(1, 6)], "format": {"duration": "60.0"}}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_run(stdout=json.dumps(payload)))
    assert probe("f.mkv") == payload


def test_a_probe_timeout_is_a_probe_error(monkeypatch):
    """A file on a stalled mount must not hang the sweep for ever."""

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=config.PROBE_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ProbeError, match="timed out"):
        probe("f.mkv")


def test_a_failing_probe_carries_its_stderr(monkeypatch):
    """The stderr text is the only clue about what is wrong with the file, so
    it has to survive into the message the sweep reports."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: fake_run(returncode=1, stderr="moov atom not found\n"),
    )
    with pytest.raises(ProbeError, match="moov atom not found"):
        probe("f.mkv")


def test_a_long_probe_failure_is_truncated(monkeypatch):
    """Whole files have been known to come back on stderr; the event log and
    the report are line oriented, so the message is capped."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: fake_run(returncode=1, stderr="x" * 5000)
    )
    with pytest.raises(ProbeError) as caught:
        probe("f.mkv")
    assert len(str(caught.value)) < 300


def test_unparseable_probe_output_is_a_probe_error(monkeypatch):
    """ffprobe can exit 0 and still write nothing usable."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_run(stdout="not json"))
    with pytest.raises(ProbeError, match="unparseable"):
        probe("f.mkv")


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"format": None},
        {"format": {}},
        {"format": {"duration": "N/A"}},
        {"format": {"duration": None}},
    ],
)
def test_duration_of_anything_unusable_is_zero(info):
    """Zero disables the length check on the rewrite rather than failing it."""
    assert duration(info) == 0.0


def test_duration_reads_a_real_one():
    assert duration({"format": {"duration": "1234.5"}}) == 1234.5


@pytest.mark.parametrize("value", ["N/A", "", None, "eleven"])
def test_stream_bitrate_ignores_junk(value):
    assert stream_bitrate({"bit_rate": value, "tags": {}}) is None


def test_stream_bitrate_falls_back_to_the_mkvmerge_tag():
    """Matroska rarely reports bit_rate, but mkvmerge writes BPS."""
    assert stream_bitrate({"tags": {"BPS": "640000"}}) == 640000


def test_a_junk_bps_tag_does_not_mask_a_real_bit_rate():
    assert stream_bitrate({"bit_rate": "448000", "tags": {"BPS": "N/A"}}) == 448000


def test_generated_settings_only_reads_our_own_tag():
    assert media.generated_settings({"tags": {media.GENERATED_TAG: "aac 192k"}}) == "aac 192k"
    assert media.generated_settings({"tags": {"title": "aac 192k"}}) is None
    assert media.generated_settings({}) is None
