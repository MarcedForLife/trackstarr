"""End-to-end against real files. Requires ffmpeg."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import requires_ffmpeg, requires_mp4_titles
from trackstarr import config, events, executor, planner
from trackstarr.cli import main as cli_main
from trackstarr.executor import Outcome, apply_plan
from trackstarr.media import ProbeError, duration, probe, stream_title
from trackstarr.planner import build_plan
from trackstarr.sweep import sweep

pytestmark = [requires_ffmpeg, pytest.mark.ffmpeg]

#: The defect this tool exists to fix: a 5.1 main track and a 2.0 commentary.
COMMENTARY_CASE = [(6, "eng", "Surround"), (2, "eng", "Commentary")]


def rewrite(plan) -> Outcome:
    outcome, _ = apply_plan(plan)
    return outcome


@pytest.fixture(autouse=True)
def _work_dir(tmp_path, monkeypatch):
    """Keep rewrites beside the fixture, so os.replace stays a same-fs rename."""
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))


def streams_of(path, *kinds):
    return [stream for stream in probe(path)["streams"] if stream["codec_type"] in kinds]


def langs_of(path, *kinds):
    return [(stream.get("tags") or {}).get("language") for stream in streams_of(path, *kinds)]


def titles_of(path, *kinds):
    return [stream_title(stream) for stream in streams_of(path, *kinds)]


def defaults(path):
    return [
        stream["index"]
        for stream in streams_of(path, "audio")
        if (stream.get("disposition") or {}).get("default")
    ]


def test_commentary_case_produces_a_real_stereo_track(make_file):
    path = make_file("f.mkv", COMMENTARY_CASE)
    plan = build_plan(path, "eng")
    assert plan.needed
    assert rewrite(plan) is Outcome.APPLIED

    tracks = streams_of(path, "audio")
    stereo = [
        stream
        for stream in tracks
        if stream["channels"] == 2
        and "commentary" not in (stream.get("tags") or {}).get("title", "").lower()
    ]
    assert len(stereo) == 1
    assert stereo[0]["tags"]["title"] == "2.0"
    # The commentary and the 5.1 both survive.
    assert len(tracks) == 3


def test_regenerate_downmix_end_to_end(make_file, monkeypatch):
    """The settings tag survives the mkv round trip; a bitrate change
    rewrites only under REGENERATE_DOWNMIXES."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED

    monkeypatch.setattr(config, "AUDIO_BITRATE", "128k")
    assert not build_plan(path, "eng").needed

    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    plan = build_plan(path, "eng")
    assert any("regenerate 2.0 downmix" in reason for reason in plan.reasons)
    assert rewrite(plan) is Outcome.APPLIED
    assert not build_plan(path, "eng").needed
    assert len(streams_of(path, "audio")) == 3


@requires_mp4_titles
def test_mp4_commentary_titles_are_read_and_survive(make_file):
    """MP4 reports track titles as ``name``, and a plain copy drops them;
    commentary must be caught before the rewrite and still be labelled
    after it, or the next pass is blind to it."""
    path = make_file("f.mp4", COMMENTARY_CASE)
    plan = build_plan(path, "eng")
    assert any("downmix from stream 1" in reason for reason in plan.reasons)
    assert rewrite(plan) is Outcome.APPLIED
    assert "Commentary" in titles_of(path, "audio")
    assert not build_plan(path, "eng").needed


@requires_mp4_titles
def test_remux_to_mkv_end_to_end(make_file, monkeypatch):
    """An MP4 converts to its .mkv sibling: subtitles become SRT, the
    generated downmix carries its settings tag, and the original is gone."""
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    path = make_file("f.mp4", COMMENTARY_CASE, subs=[("eng", "")])
    plan = build_plan(path, "eng")
    assert "remux to mkv (REMUX_TO_MKV)" in plan.reasons
    assert rewrite(plan) is Outcome.APPLIED

    converted = plan.out_path
    assert converted.endswith(".mkv")
    assert not os.path.exists(path)
    assert [s["codec_name"] for s in streams_of(converted, "subtitle")] == ["subrip"]
    assert any("TRACKSTARR" in (s.get("tags") or {}) for s in streams_of(converted, "audio"))
    assert not build_plan(converted, "eng").needed


def test_remux_never_overwrites_an_existing_sibling(make_file, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    path = make_file("f.mp4", COMMENTARY_CASE)
    (tmp_path / "f.mkv").write_text("precious")

    outcome, detail = apply_plan(build_plan(path, "eng"))
    assert outcome is Outcome.FAILED
    assert "already exists" in detail
    assert (tmp_path / "f.mkv").read_text() == "precious"
    assert os.path.exists(path)


def test_generated_track_inherits_no_statistics_tags(make_file, tmp_path):
    """A fresh encode advertising the source's mkvmerge BPS would show the
    old track's numbers in every player and poison the weak-track test."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    remuxed = tmp_path / "remux.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            path,
            "-map",
            "0",
            "-c",
            "copy",
            "-metadata:s:a:0",
            "BPS=640000",
            str(remuxed),
        ],
        check=True,
    )
    os.replace(remuxed, path)

    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED
    tracks = streams_of(path, "audio")
    generated = next(s for s in tracks if "TRACKSTARR" in (s.get("tags") or {}))
    assert "BPS" not in (generated.get("tags") or {})
    copied = next(s for s in tracks if s["channels"] == 6)
    assert (copied.get("tags") or {}).get("BPS") == "640000"


def test_seven_one_only_gains_five_one_and_stereo(make_file):
    path = make_file("f.mkv", [(8, "eng", "")])
    plan = build_plan(path, "eng")
    assert rewrite(plan) is Outcome.APPLIED

    tracks = streams_of(path, "audio")
    assert sorted(stream["channels"] for stream in tracks) == [2, 6, 8]
    assert not build_plan(path, "eng").needed


def test_exactly_one_default_audio_track_after_a_downmix(make_file):
    path = make_file("f.mkv", COMMENTARY_CASE)
    plan = build_plan(path, "eng")
    assert rewrite(plan) is Outcome.APPLIED
    assert len(defaults(path)) <= 1


def test_foreign_tracks_are_removed(make_file):
    path = make_file("f.mkv", [(6, "eng", ""), (6, "ger", "")], subs=[("eng", ""), ("ger", "")])
    plan = build_plan(path, "eng")
    assert rewrite(plan) is Outcome.APPLIED

    langs = set(langs_of(path, "audio", "subtitle"))
    assert "ger" not in langs
    assert "eng" in langs


def test_cover_art_is_removed(make_file):
    path = make_file("f.mkv", [(2, "eng", "")], cover_art=True)
    plan = build_plan(path, "eng")
    assert plan.needed
    assert rewrite(plan) is Outcome.APPLIED
    assert [stream["codec_name"] for stream in streams_of(path, "video")] == ["h264"]


def test_korean_original_downmixes_from_korean(make_file):
    path = make_file("f.mkv", [(6, "kor", ""), (6, "eng", ""), (2, "fre", "")])
    plan = build_plan(path, "kor")
    assert rewrite(plan) is Outcome.APPLIED

    assert "fre" not in langs_of(path, "audio")
    stereo = [stream for stream in streams_of(path, "audio") if stream["channels"] == 2]
    assert len(stereo) == 1
    assert stereo[0]["tags"]["language"] == "kor"


def test_rewrite_is_idempotent(make_file):
    """The sweep re-plans every file every night; it must settle."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED
    assert not build_plan(path, "eng").needed
    assert not build_plan(path, "eng").needed


def test_source_changed_mid_rewrite_is_not_overwritten(make_file, monkeypatch):
    """An *arr upgrade landing during a rewrite must not be reverted."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    plan = build_plan(path, "eng")
    real_args = executor.ffmpeg_args

    def touch_source_then_build(plan, dest):
        # apply_plan stats the source before calling this, so the utime lands
        # inside its pre-flight-to-replace window, as an upgrade would.
        os.utime(plan.path, (0, 0))
        return real_args(plan, dest)

    monkeypatch.setattr(executor, "ffmpeg_args", touch_source_then_build)
    before = Path(path).read_bytes()
    outcome, detail = apply_plan(plan)
    assert outcome is Outcome.DEFERRED
    assert "source changed" in detail
    assert Path(path).read_bytes() == before


def test_conforming_file_is_untouched(make_file):
    path = make_file("f.mkv", [(2, "eng", ""), (6, "eng", "")])
    before = Path(path).read_bytes()
    plan = build_plan(path, "eng")
    assert not plan.needed
    assert Path(path).read_bytes() == before


def test_duration_is_preserved(make_file):
    path = make_file("f.mkv", [(6, "eng", ""), (2, "eng", "Commentary")])
    before = duration(probe(path))
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED
    after = duration(probe(path))
    assert abs(after - before) < 0.5


def test_unsupported_container_is_skipped(tmp_path):
    stale = tmp_path / "old.avi"
    stale.write_bytes(b"not really an avi")
    plan = build_plan(str(stale), "eng")
    assert plan.skip is not None
    assert "ALLOWED_EXTS" in plan.skip


def test_corrupt_file_raises_rather_than_being_rewritten(tmp_path):
    broken = tmp_path / "broken.mkv"
    broken.write_bytes(b"\x00" * 4096)
    with pytest.raises(ProbeError, match="ffprobe failed"):
        build_plan(str(broken), "eng")


@pytest.fixture
def swept_library(monkeypatch, tmp_path):
    """Point the sweep at the fixture directory and count planner probes."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))

    probed = []
    real_probe = planner.probe

    def counting_probe(path):
        probed.append(path)
        return real_probe(path)

    monkeypatch.setattr(planner, "probe", counting_probe)
    return probed


def test_second_sweep_skips_probing_unchanged_files(make_file, swept_library):
    """The sweep cache: an unchanged library costs stats, not probes."""
    make_file("f.mkv", [(2, "eng", ""), (6, "eng", "")])

    assert sweep(dry_run=True)["conform"] == 1
    assert len(swept_library) == 1

    assert sweep(dry_run=True)["conform"] == 1
    assert len(swept_library) == 1


def test_drop_commentary_end_to_end(make_file, monkeypatch):
    monkeypatch.setattr(config, "DROP_COMMENTARY", True)
    path = make_file("f.mkv", COMMENTARY_CASE)
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED

    tracks = streams_of(path, "audio")
    assert not any("commentary" in title.lower() for title in titles_of(path, "audio"))
    # The 5.1 survives and gained a real stereo downmix.
    assert sorted(stream["channels"] for stream in tracks) == [2, 6]
    assert not build_plan(path, "eng").needed


def test_sdh_subtitle_dropped_end_to_end(make_file):
    path = make_file(
        "f.mkv",
        COMMENTARY_CASE,
        subs=[("eng", ""), ("eng", "English (SDH)")],
    )
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED

    assert titles_of(path, "subtitle") == [""]
    assert not build_plan(path, "eng").needed


def test_junk_titles_cleared_end_to_end(make_file):
    path = make_file(
        "f.mkv", [(6, "eng", "DTS-HD MA 5.1 @ 1509kbps"), (2, "eng", "Commentary")]
    )
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED

    titles = titles_of(path, "audio")
    assert not any("DTS" in title for title in titles)
    assert "Commentary" in titles

    again = build_plan(path, "eng")
    assert not again.needed
    assert again.incidental == []


def test_fix_command_end_to_end(make_file, capsys):
    """fix runs the whole pipeline for real: startup checks, the plan, the
    rewrite under the slot locks, and the cli-sourced event."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    assert cli_main(["--log-level", "WARNING", "fix", "--original", "eng", path]) == 0
    assert capsys.readouterr().out.startswith("fixed")
    assert sorted(stream["channels"] for stream in streams_of(path, "audio")) == [2, 2, 6]
    (entry,) = [event for event in events.read() if event["event"] == "fixed"]
    assert entry["source"] == "cli"
    # Idempotent like every other source: a second run conforms.
    assert cli_main(["--log-level", "WARNING", "fix", "--original", "eng", path]) == 0
    assert capsys.readouterr().out.startswith("conform")


def test_hardlinked_file_is_processed_by_default(make_file, tmp_path):
    path = make_file("f.mkv", COMMENTARY_CASE)
    os.link(path, tmp_path / "seed.mkv")
    assert build_plan(path, "eng").needed


def test_seed_release_invalidates_cached_hardlink_skip(
    make_file, swept_library, monkeypatch, tmp_path
):
    """When seeding ends only the link count changes; the cache must notice."""
    monkeypatch.setattr(config, "SKIP_HARDLINKS", True)
    library = tmp_path / "media"
    library.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(library)])
    path = make_file("media/f.mkv", COMMENTARY_CASE)
    seed = tmp_path / "seed.mkv"
    os.link(path, seed)

    assert sweep(dry_run=True)["skip"] == 1
    assert sweep(dry_run=True)["skip"] == 1
    assert len(swept_library) == 0

    seed.unlink()
    assert sweep(dry_run=True)["would-fix"] == 1
    assert len(swept_library) == 1


def test_report_only_sweep_reuses_would_fix_verdicts(make_file, swept_library, tmp_path):
    """Nightly report-only sweeps re-emit their rows without re-probing, but
    an applying sweep must not trust a cached would-fix verdict."""
    make_file("f.mkv", COMMENTARY_CASE)

    assert sweep(dry_run=True)["would-fix"] == 1
    assert sweep(dry_run=True)["would-fix"] == 1
    assert len(swept_library) == 1
    report = (tmp_path / "state" / "pending.tsv").read_text()
    assert "would-fix" in report

    counts = sweep(dry_run=False)
    assert counts["fixed"] == 1
    assert len(swept_library) == 2
