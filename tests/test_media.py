"""probe's failure paths, and the tag readers that meet junk values.

A sweep points ffprobe at a whole library, so it eventually meets a
truncated download, a stalled mount, and tags saying something ridiculous.
Each has to become a ProbeError or a None, never a traceback.
"""

import json
import subprocess

import pytest

from conftest import audio, fake_run, subtitle, video
from trackstarr import config, media
from trackstarr.media import ProbeError, duration, probe, stream_bitrate
from trackstarr.policy import Policy


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
    """The stderr text is the only clue about what is wrong, so it has to survive
    into the message the sweep reports."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: fake_run(returncode=1, stderr="moov atom not found\n"),
    )
    with pytest.raises(ProbeError, match="moov atom not found"):
        probe("f.mkv")


def test_a_long_probe_failure_is_truncated(monkeypatch):
    """Whole files have been known to come back on stderr, and the report is line
    oriented."""
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


def test_track_summary_distils_a_stream():
    stream = audio(2, 6, title="Director's Commentary", default=1, bitrate="640000")
    assert media.track_summary(stream, Policy.from_config()) == {
        "index": 2,
        "kind": "audio",
        "codec": "ac3",
        "channels": 6,
        "lang": "eng",
        "title": "Director's Commentary",
        "bitrate": 640000,
        "flags": ["default", "commentary"],
    }


def test_track_summary_drops_what_it_does_not_know():
    """Absence means unknown, the same contract the history's fields use."""
    assert media.track_summary(video(), Policy.from_config()) == {
        "index": 0,
        "kind": "video",
        "codec": "h264",
    }


def test_track_summary_flags_subtitles_the_way_the_rules_do():
    """The flags are the classifications, not the raw dispositions, so a view
    reads "sdh" for a title-only SDH track exactly as the rules would."""
    policy = Policy.from_config()
    assert media.track_summary(subtitle(3, title="English SDH"), policy)["flags"] == ["sdh"]
    assert media.track_summary(subtitle(4, forced=1), policy)["flags"] == ["forced"]
