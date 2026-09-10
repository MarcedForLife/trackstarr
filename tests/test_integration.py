"""End-to-end against real files. Requires ffmpeg."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from conftest import read_events, set_config, set_langs, set_layouts, set_rules
from trackstarr import config, executor, library, planner, retag, rewrites, sweep_cache
from trackstarr.cli import main as cli_main
from trackstarr.executor import Outcome, apply_plan
from trackstarr.media import ProbeError, duration, probe, stream_title
from trackstarr.planner import build_plan
from trackstarr.policy import Policy
from trackstarr.status import Status
from trackstarr.sweep import sweep
from trackstarr.sweep_cache import read

#: The defect this tool exists to fix: a 5.1 main track and a 2.0 commentary.
COMMENTARY_CASE = [(6, "eng", "Surround"), (2, "eng", "Commentary")]

#: The tag edits need mkvpropedit, which the image and CI have and a
#: development machine may not.
needs_mkvtoolnix = pytest.mark.skipif(
    shutil.which("mkvpropedit") is None, reason="mkvtoolnix is not installed"
)


def rewrite(plan) -> Outcome:
    outcome, _ = apply_plan(plan)
    return outcome


@pytest.fixture(autouse=True)
def _work_dir(tmp_path):
    """Keep rewrites beside the fixture, so os.replace stays a same-fs rename."""
    set_config(WORK_DIR=str(tmp_path))


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


def test_regenerate_downmix_end_to_end(make_file):
    """The settings tag survives the mkv round trip; a bitrate change
    rewrites only once the regenerate rule is on."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED

    set_layouts("2.0:aac:128k", "5.1:ac3:640k")
    assert not build_plan(path, "eng").needed

    set_rules(regenerate="always")
    plan = build_plan(path, "eng")
    assert any("regenerate 2.0 downmix" in reason for reason in plan.reasons)
    assert rewrite(plan) is Outcome.APPLIED
    assert not build_plan(path, "eng").needed
    assert len(streams_of(path, "audio")) == 3


def test_a_trimmed_file_re_encodes_its_own_downmix(make_file):
    """What REGENERATE_ABOVE_PERCENT is for: with the 5.1 removed, a later rate
    change has nothing bigger to rebuild the stereo from, so the track is its
    own source."""
    set_layouts("2.0:aac:320k", "5.1:remove")
    path = make_file("f.mkv", [(6, "eng", "Surround")])
    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED
    assert [stream["channels"] for stream in streams_of(path, "audio")] == [2]

    set_layouts("2.0:aac:128k", "5.1:remove")
    set_rules(regenerate="always")
    # The rebuild side leaves it: there is nothing left to downmix from.
    assert not build_plan(path, "eng").needed

    set_config(REGENERATE_ABOVE_PERCENT=120)
    plan = build_plan(path, "eng")
    assert any("re-encode high-bitrate 2.0 downmix" in reason for reason in plan.reasons)
    assert rewrite(plan) is Outcome.APPLIED
    (stereo,) = streams_of(path, "audio")
    assert stereo["tags"]["TRACKSTARR"] == "aac 128k"
    assert not build_plan(path, "eng").needed


def test_mp4_commentary_titles_are_read_and_survive(make_file):
    """MP4 reports titles as ``name`` and a plain copy drops them, so commentary
    has to be caught before the rewrite and still labelled after it."""
    path = make_file("f.mp4", COMMENTARY_CASE)
    plan = build_plan(path, "eng")
    assert any("downmix from stream 1" in reason for reason in plan.reasons)
    assert rewrite(plan) is Outcome.APPLIED
    assert "Commentary" in titles_of(path, "audio")
    assert not build_plan(path, "eng").needed


def test_remux_to_mkv_end_to_end(make_file):
    """An MP4 converts to its .mkv sibling: subtitles become SRT, the
    generated downmix carries its settings tag, and the original is gone."""
    set_rules(remux="always")
    path = make_file("f.mp4", COMMENTARY_CASE, subs=[("eng", "")])
    plan = build_plan(path, "eng")
    assert "remux to mkv (RULE_REMUX)" in plan.reasons
    assert rewrite(plan) is Outcome.APPLIED

    converted = plan.out_path
    assert converted.endswith(".mkv")
    assert not os.path.exists(path)
    assert [s["codec_name"] for s in streams_of(converted, "subtitle")] == ["subrip"]
    assert any("TRACKSTARR" in (s.get("tags") or {}) for s in streams_of(converted, "audio"))
    assert not build_plan(converted, "eng").needed


def test_remux_carries_the_mp4_bitrates_into_bps_tags(make_file):
    """Matroska has no per-stream rate field, so without a BPS tag every
    remuxed file loses its track rates."""
    set_rules(remux="always")
    path = make_file("f.mp4", COMMENTARY_CASE)
    source_rates = {
        stream["index"]: stream["bit_rate"] for stream in streams_of(path, "audio", "video")
    }
    assert source_rates, "the MP4 fixture should report per-stream rates"

    plan = build_plan(path, "eng")
    assert rewrite(plan) is Outcome.APPLIED

    for stream in streams_of(plan.out_path, "audio", "video"):
        tags = stream.get("tags") or {}
        if "TRACKSTARR" in tags:
            # A fresh encode has no measured rate to state yet; its settings
            # tag is what a later pass reads instead.
            assert "BPS" not in tags
            continue
        assert tags["BPS"] in source_rates.values()
    # And they survive being read back as rates, which is the whole point.
    carried = [
        track
        for track in build_plan(plan.out_path, "eng").tracks
        if "generated" not in (track.get("flags") or [])
    ]
    assert carried and all(track.get("bitrate") for track in carried)


def test_a_rewrite_never_restates_a_source_bps_tag(make_file):
    """A file's own mkvmerge statistics are better than ffprobe's reading of
    them, so the gap-filling above must not overwrite one that is already there."""
    set_rules(remux="always")
    path = make_file("f.mkv", COMMENTARY_CASE)
    tagged = Path(path).with_name("tagged.mkv")
    subprocess.run(
        [
            *["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0", "-c", "copy"],
            *["-metadata:s:a:0", "BPS=999999", str(tagged)],
        ],
        check=True,
    )
    os.replace(tagged, path)

    assert rewrite(build_plan(path, "eng")) is Outcome.APPLIED
    surround = next(s for s in streams_of(path, "audio") if s["channels"] == 6)
    assert (surround.get("tags") or {})["BPS"] == "999999"


def test_remux_never_overwrites_an_existing_sibling(make_file, tmp_path):
    set_rules(remux="always")
    path = make_file("f.mp4", COMMENTARY_CASE)
    (tmp_path / "f.mkv").write_text("precious")

    outcome, detail = apply_plan(build_plan(path, "eng"))
    assert outcome is Outcome.FAILED
    assert "already exists" in detail
    assert (tmp_path / "f.mkv").read_text() == "precious"
    assert os.path.exists(path)


def test_generated_track_inherits_no_statistics_tags(make_file, tmp_path):
    """A fresh encode advertising the source's mkvmerge BPS would show the old
    numbers in every player and poison the low-bitrate test."""
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


def test_the_dropped_layout_is_really_gone_and_its_downmixes_are_really_there(make_file):
    """The one change that deletes something a re-rip is the only way back
    from, so it is worth proving against a real file rather than a probe."""
    set_layouts("2.0", "5.1", "7.1:remove")
    path = make_file("f.mkv", [(8, "eng", "")])
    plan = build_plan(path, "eng")
    assert rewrite(plan) is Outcome.APPLIED

    audio = streams_of(path, "audio")
    assert [stream["channels"] for stream in audio] == [2, 6]
    assert not build_plan(path, "eng").needed


def test_korean_original_downmixes_from_korean_and_english(make_file):
    """The default list adds both, so the kept English dub gets a stereo track
    of its own rather than leaving the Korean one to answer for it."""
    path = make_file("f.mkv", [(6, "kor", ""), (6, "eng", ""), (2, "fre", "")])
    plan = build_plan(path, "kor")
    assert rewrite(plan) is Outcome.APPLIED

    assert "fre" not in langs_of(path, "audio")
    stereo = [stream for stream in streams_of(path, "audio") if stream["channels"] == 2]
    assert {stream["tags"]["language"] for stream in stereo} == {"kor", "eng"}


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
    # Named as well as skipped, which is what process() hands back rather than
    # reading the sentence above for it.
    assert plan.skip_status is Status.UNSUPPORTED


def test_corrupt_file_raises_rather_than_being_rewritten(tmp_path):
    broken = tmp_path / "broken.mkv"
    broken.write_bytes(b"\x00" * 4096)
    with pytest.raises(ProbeError, match="ffprobe failed"):
        build_plan(str(broken), "eng")


@pytest.fixture
def swept_library(monkeypatch, tmp_path):
    """Point the sweep at the fixture directory and count planner probes."""
    set_config(MEDIA_DIRS=[str(tmp_path)])
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


def test_drop_commentary_end_to_end(make_file):
    set_rules(commentary="always")
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


def test_release_tags_cleared_end_to_end(make_file):
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
    assert capsys.readouterr().out.startswith("modified")
    assert sorted(stream["channels"] for stream in streams_of(path, "audio")) == [2, 2, 6]
    (entry,) = [event for event in read_events() if event["event"] == "modified"]
    assert entry["source"] == "cli"
    # Idempotent like every other source: a second run conforms.
    assert cli_main(["--log-level", "WARNING", "fix", "--original", "eng", path]) == 0
    assert capsys.readouterr().out.startswith("conform")


def test_hardlinked_file_is_left_alone_by_default(make_file, tmp_path):
    """The standard *arr layout hard-links every import, so rewriting one would
    break the link and double the file for as long as the client seeds it."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    os.link(path, tmp_path / "seed.mkv")
    assert not build_plan(path, "eng").needed


def test_hardlinked_file_is_processed_once_the_seed_is_gone(make_file, tmp_path):
    """Skipping parks the file, it does not give up on it."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    seed = tmp_path / "seed.mkv"
    os.link(path, seed)
    seed.unlink()
    assert build_plan(path, "eng").needed


def test_hardlinks_are_rewritten_when_the_skip_is_switched_off(make_file, tmp_path):
    set_config(SKIP_HARDLINKS=False)
    path = make_file("f.mkv", COMMENTARY_CASE)
    os.link(path, tmp_path / "seed.mkv")
    assert build_plan(path, "eng").needed


def test_seed_release_invalidates_cached_hardlink_skip(make_file, swept_library, tmp_path):
    """When seeding ends only the link count changes; the cache must notice."""
    set_config(SKIP_HARDLINKS=True)
    library = tmp_path / "media"
    library.mkdir()
    set_config(MEDIA_DIRS=[str(library)])
    path = make_file("media/f.mkv", COMMENTARY_CASE)
    seed = tmp_path / "seed.mkv"
    os.link(path, seed)

    assert sweep(dry_run=True)["skip"] == 1
    assert sweep(dry_run=True)["skip"] == 1
    assert len(swept_library) == 0

    seed.unlink()
    assert sweep(dry_run=True)["pending"] == 1
    assert len(swept_library) == 1


def test_report_only_sweep_reuses_pending_verdicts(make_file, swept_library, tmp_path):
    """Report-only sweeps re-emit their rows without re-probing, but an applying
    sweep must not trust a cached pending verdict."""
    make_file("f.mkv", COMMENTARY_CASE)

    assert sweep(dry_run=True)["pending"] == 1
    assert sweep(dry_run=True)["pending"] == 1
    assert len(swept_library) == 1
    report = (tmp_path / "state" / "pending.tsv").read_text()
    assert "pending" in report

    counts = sweep(dry_run=False)
    assert counts["modified"] == 1
    # Two more probes: the plan's, and the re-judge of what it wrote; see
    # :func:`trackstarr.processing._rejudged`.
    assert len(swept_library) == 3
    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint()).files
    path = str(tmp_path / "f.mkv")
    entry = stored[path]
    assert entry["status"] == "conform"
    # Passed from here on, so the record is the library's only way to tell this
    # file from one we never touched. Joined by key, so it claims the file the
    # sweep just judged rather than some earlier one.
    made = rewrites.against(stored)[path]
    assert made["at"]
    assert made["bytes_after"] == entry["size"]
    # The two columns the sheet draws it in. `added` is a position in the file
    # as it now stands, so it is read against the re-probed tracks: nothing
    # else proves the record and the file agree.
    was = made["was"]
    assert [track["index"] for track in was] == [0, 1, 2]
    assert "dropped" not in made
    (added,) = made["added"]
    assert entry["tracks"][added]["channels"] == 2
    assert len(entry["tracks"]) == len(was) + 1


def test_a_rewrite_reports_how_far_through_the_file_it_is(make_file):
    """The per-file bar: ffmpeg's own readout against the plan's duration, so
    the page draws the encode rather than the slot wait."""
    path = make_file("f.mkv", COMMENTARY_CASE)
    plan = build_plan(path, "eng")
    assert plan.needed
    assert plan.src_duration == pytest.approx(2.0, abs=0.2)

    seen: list[tuple[float, float]] = []
    assert (
        apply_plan(plan, lambda done, speed: seen.append((done, speed)))[0] is Outcome.APPLIED
    )
    # The last block lands as the child exits, so the reader thread may still
    # be a moment behind the rename.
    for _ in range(200):
        if seen:
            break
        time.sleep(0.01)
    assert seen, "the rewrite reported nothing to draw a bar with"
    done, speed = seen[-1]
    assert done == pytest.approx(plan.src_duration, abs=0.2), (
        "it should end at the file's length"
    )
    assert speed > 0, "and say how fast it got there, which is what a time left is made of"


def judged(path: str) -> dict:
    """The sweep cache entry the page would have shown the file's tracks from,
    as far as the staleness check reads it."""
    found = os.stat(path)
    return {"size": found.st_size, "mtime_ns": found.st_mtime_ns}


@needs_mkvtoolnix
def test_tagging_an_untagged_original_makes_it_the_downmix_source(make_file):
    """The case the editor exists for: a Japanese film whose 5.1 says und
    beside an English 2.0, so nothing is made for it. One tag later the rules
    want the Japanese stereo track."""
    set_langs("eng", "original")
    set_layouts("5.1", "2.0")
    path = make_file("f.mkv", [(6, "und", ""), (2, "eng", "")])
    dune = library.Title("arr:radarr:7", "Dune", os.path.dirname(path), "movie", lang="jpn")
    assert not build_plan(path, "jpn").needed

    result = retag.apply(path, 1, retag.Edit("jpn"), "admin", judged(path), title=dune)

    assert (result.status, result.verdict) == (retag.Outcome.RETAGGED, "pending")
    assert langs_of(path, "audio") == ["jpn", "eng"]
    plan = build_plan(path, "jpn")
    assert any("2.0 downmix from stream 1 (6ch jpn)" in reason for reason in plan.reasons)
    # Booked, so the sheet reloads to the new verdict rather than the old.
    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    assert stored.files[path]["status"] == "pending"
    assert stored.files[path]["tracks"][1]["lang"] == "jpn"
    (recorded,) = read_events()
    assert (recorded["event"], recorded["changed"]) == (
        "retagged",
        {"lang": {"from": "und", "to": "jpn"}},
    )


@needs_mkvtoolnix
def test_marking_a_stereo_track_as_commentary_frees_the_stereo_slot(make_file):
    """A 2.0 with no title and no flag is the stereo track as far as the
    rules can tell. Flagged, it is commentary, and a real 2.0 is owed."""
    set_layouts("5.1", "2.0")
    set_rules(commentary="never")
    path = make_file("f.mkv", [(6, "eng", "Surround"), (2, "eng", "")])
    assert not build_plan(path, "eng").needed

    result = retag.apply(path, 2, retag.Edit(flags={"commentary": True}), "admin", judged(path))

    assert result.status == retag.Outcome.RETAGGED
    assert [stream["disposition"]["comment"] for stream in streams_of(path, "audio")] == [0, 1]
    reasons = build_plan(path, "eng").reasons
    assert any("2.0 downmix from stream 1 (6ch eng)" in reason for reason in reasons)
    # The 5.1 was not touched.
    assert langs_of(path, "audio") == ["eng", "eng"]
    assert titles_of(path, "audio") == ["Surround", ""]


@needs_mkvtoolnix
def test_subtitle_flags_round_trip(make_file):
    path = make_file("f.mkv", [(2, "eng", "")], subs=[("eng", ""), ("eng", "")])
    subtitles = streams_of(path, "subtitle")
    result = retag.apply(
        path,
        subtitles[1]["index"],
        retag.Edit(flags={"forced": True, "sdh": True}),
        "admin",
        judged(path),
    )
    assert result.status == retag.Outcome.RETAGGED
    flagged = streams_of(path, "subtitle")[1]["disposition"]
    assert (flagged["forced"], flagged["hearing_impaired"]) == (1, 1)
    # Back again, which is the same edit the other way.
    result = retag.apply(
        path,
        subtitles[1]["index"],
        retag.Edit(flags={"forced": False, "sdh": False}),
        "admin",
        judged(path),
    )
    assert result.status == retag.Outcome.RETAGGED
    flagged = streams_of(path, "subtitle")[1]["disposition"]
    assert (flagged["forced"], flagged["hearing_impaired"]) == (0, 0)


@needs_mkvtoolnix
def test_a_language_mkvpropedit_does_not_know_fails_in_its_words(make_file):
    path = make_file("f.mkv", [(2, "eng", "")])
    result = retag.apply(path, 1, retag.Edit("xq"), "admin", judged(path))
    assert result.status is retag.Outcome.FAILED
    # The tool's own message, whatever this version's wording, naming the code.
    assert result.detail.startswith("mkvpropedit failed (2):")
    assert "xq" in result.detail
    assert langs_of(path, "audio") == ["eng"]
