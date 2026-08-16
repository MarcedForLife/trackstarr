"""The rules, tested against synthetic ffprobe output. No media required."""

from __future__ import annotations

import os

import pytest

from conftest import audio, probe_data, subtitle, video
from trackstarr import config
from trackstarr.layouts import resolved_layouts
from trackstarr.media import is_junk_title
from trackstarr.planner import (
    build_plan,
    channel_rank,
    config_errors,
    ffmpeg_args,
    new_plan,
    plan_from_probe,
)


def plan_for(*streams, original="eng", title="", path="/x/f.mkv"):
    return plan_from_probe(new_plan(path, original), probe_data(*streams, title=title))


def audio_out(plan):
    return [out for out in plan.streams if out.kind == "audio"]


# Rule 2: the downmix guarantee


def test_commentary_stereo_does_not_satisfy_the_downmix_rule():
    """The defect this tool exists to fix.

    A 2.0 commentary track alongside a 5.1 main track must still produce a
    real downmix, or the file has nothing a stereo client can direct play.
    """
    plan = plan_for(
        video(0),
        audio(1, 6, title="Surround 5.1"),
        audio(2, 2, title="Director's Commentary"),
    )
    assert plan.needed
    assert any("downmix from stream 1" in reason for reason in plan.reasons)
    generated = [out for out in audio_out(plan) if out.encode]
    assert len(generated) == 1
    assert generated[0].channels == 2


def test_isolated_score_also_does_not_count():
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Isolated Score"))
    assert any("downmix" in reason for reason in plan.reasons)


@pytest.mark.parametrize("flag", ["comment", "visual_impaired", "descriptions"])
def test_disposition_flags_mark_commentary(flag):
    stream = audio(2, 2, title="Untitled")
    stream["disposition"][flag] = 1
    plan = plan_for(video(0), audio(1, 6), stream)
    assert any("downmix" in reason for reason in plan.reasons)


def test_real_stereo_track_means_no_downmix():
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert not plan.needed


def test_no_downmix_added_when_only_mono_exists():
    """Upmixing mono to stereo would duplicate a channel for nothing."""
    plan = plan_for(video(0), audio(1, 1))
    assert not any("downmix" in reason for reason in plan.reasons)


def test_downmix_prefers_original_language_over_english():
    plan = plan_for(
        video(0),
        audio(1, 6, lang="eng"),
        audio(2, 6, lang="kor"),
        original="kor",
    )
    assert any("downmix from stream 2" in reason for reason in plan.reasons)
    assert next(out for out in audio_out(plan) if out.encode).lang == "kor"


def test_downmix_prefers_most_channels_within_a_language():
    plan = plan_for(video(0), audio(1, 6, lang="eng"), audio(2, 8, lang="eng"))
    assert any("add 2.0 downmix from stream 2" in reason for reason in plan.reasons)


def test_downmix_source_is_never_a_commentary_track():
    plan = plan_for(video(0), audio(1, 6, title="Commentary"), audio(2, 6))
    assert any("downmix from stream 2" in reason for reason in plan.reasons)


def test_mp4_style_name_tag_counts_as_the_title():
    """ffprobe reports MP4 track titles under ``name``, not ``title``."""
    commentary = audio(2, 2)
    commentary["tags"]["name"] = "Director's Commentary"
    plan = plan_for(video(0), audio(1, 6), commentary)
    assert any("downmix from stream 1" in reason for reason in plan.reasons)


def test_seven_one_only_gains_both_stereo_and_five_one():
    """The default layouts are 2.0 and 5.1, both made from the same 7.1."""
    plan = plan_for(video(0), audio(1, 8))
    assert any("add 2.0 downmix from stream 1" in reason for reason in plan.reasons)
    assert any("add 5.1 downmix from stream 1" in reason for reason in plan.reasons)
    generated = [out for out in audio_out(plan) if out.encode]
    assert [(out.channels, out.title) for out in generated] == [(2, "2.0"), (6, "5.1")]


def test_layouts_never_upmix():
    """A stereo-only file never gains a 5.1."""
    assert not plan_for(video(0), audio(1, 2)).needed


def test_custom_layouts_replace_the_default(monkeypatch):
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0"})
    plan = plan_for(video(0), audio(1, 8))
    assert [out.channels for out in audio_out(plan) if out.encode] == [2]


def test_layouts_with_equal_channel_counts_generate_one_track(monkeypatch):
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"4.2", "5.1"})
    plan = plan_for(video(0), audio(1, 8))
    assert len([out for out in audio_out(plan) if out.encode]) == 1


def test_duplicate_channel_counts_are_refused_at_startup(monkeypatch):
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"5.1", "5.1:640k"})
    errors = config_errors()
    assert len(errors) == 1
    assert "5.1, 5.1:640k" in errors[0]


def test_invalid_layouts_catch_typos(monkeypatch):
    monkeypatch.setattr(
        config, "DOWNMIX_LAYOUTS", {"2.0", "surround", "5:1", "0.0", "5.1:640x"}
    )
    errors = config_errors()
    assert len(errors) == 1
    assert all(bad in errors[0] for bad in ("surround", "5:1", "0.0", "5.1:640x"))
    assert [(layout.name, layout.channels) for layout in resolved_layouts()] == [("2.0", 2)]


# The regenerate opt-in (REGENERATE_DOWNMIXES)


def tagged_downmix(index, channels, settings, title="2.0"):
    """An audio stream carrying the tag a generated downmix is written with."""
    stream = audio(index, channels, title=title)
    stream["tags"]["TRACKSTARR"] = settings
    return stream


def test_generated_mode_rebuilds_stale_downmixes(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert any(reason.startswith("regenerate 2.0 downmix") for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}
    assert [out.channels for out in audio_out(plan) if out.encode] == [2]


def test_generated_mode_leaves_matching_downmixes(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"), audio(2, 6))
    assert not plan.needed


def test_regeneration_is_off_by_default():
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert not plan.needed


def test_generated_mode_never_touches_untagged_tracks(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    weak = audio(1, 2)
    weak["bit_rate"] = "96000"
    plan = plan_for(video(0), weak, audio(2, 6))
    assert not plan.needed


def test_stale_downmix_kept_when_no_source_survives(monkeypatch):
    """A file must never lose a layout it had."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"))
    assert not plan.needed
    assert 1 in {out.src for out in plan.streams}


def test_all_mode_replaces_weak_stereo(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    weak = audio(1, 2)
    weak["bit_rate"] = "96000"
    plan = plan_for(video(0), weak, audio(2, 6))
    assert any("replace weak 2.0 track 1 (96k)" in reason for reason in plan.reasons)
    assert any("add 2.0 downmix from stream 2" in reason for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}


def test_all_mode_reads_mkvmerge_bps_tags(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    weak = audio(1, 2)
    weak["tags"]["BPS-eng"] = "96000"
    plan = plan_for(video(0), weak, audio(2, 6))
    assert any("replace weak" in reason for reason in plan.reasons)


def test_all_mode_leaves_unknown_bitrates_alone(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert not plan.needed


def test_all_mode_only_replaces_clearly_weak_tracks(monkeypatch):
    """Encoders emit what the content needs, not the nominal request, and
    codecs differ in efficiency, so only a track under half the layout's
    rate reads as weak. A decent 640k AC3 5.1 survives a 960k AAC target."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    stereo = audio(1, 2)
    stereo["bit_rate"] = "300000"
    surround = audio(2, 6)
    surround["bit_rate"] = "640000"
    plan = plan_for(video(0), stereo, surround, audio(3, 8))
    assert not plan.needed


def test_regeneration_is_matroska_only(monkeypatch):
    """MP4 drops the identifying tag, where regeneration deleted commentary
    and re-encoded its own tracks every sweep; it must drop nothing there."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    weak = audio(1, 2)
    weak["bit_rate"] = "96000"
    plan = plan_for(video(0), weak, audio(2, 6), path="/x/f.mp4")
    assert not plan.needed


def test_duplicate_layouts_regenerate_toward_one_target(monkeypatch):
    """The drop side must judge against the same layout the rebuild uses,
    or a 5.1,5.1:640k config regenerates forever."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"5.1", "5.1:640k"})
    plan = plan_for(video(0), tagged_downmix(1, 6, "aac 128k", title="5.1"), audio(2, 8))
    generated = [out for out in audio_out(plan) if out.encode]
    assert [out.bitrate for out in generated] == ["640k"]
    assert any("as aac 640k" in reason for reason in plan.reasons)


def test_all_mode_never_replaces_commentary(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    weak = audio(1, 2, title="Commentary")
    weak["bit_rate"] = "96000"
    plan = plan_for(video(0), weak, audio(2, 6))
    # The commentary survives; the missing real stereo is downmixed anyway.
    assert 1 in {out.src for out in plan.streams}
    assert any("add 2.0 downmix" in reason for reason in plan.reasons)


# The remux opt-in (REMUX_TO_MKV)


def test_remux_triggers_for_mp4(monkeypatch):
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), path="/x/f.mp4")
    assert plan.reasons == ["remux to mkv (REMUX_TO_MKV)"]
    assert plan.out_path == "/x/f.mkv"


def test_remux_leaves_matroska_alone(monkeypatch):
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert not plan.needed
    assert plan.out_path == plan.path


def test_remux_is_off_by_default():
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), path="/x/f.mp4")
    assert not plan.needed
    assert plan.out_path == plan.path


def test_remux_converts_mov_text_subtitles(monkeypatch):
    """Matroska has no mov_text, so the text converts to SRT; other
    subtitle codecs stream copy as usual."""
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    mov_text = subtitle(3, "eng")
    mov_text["codec_name"] = "mov_text"
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), mov_text, path="/x/f.mp4")
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-c:s:0") + 1] == "srt"


# Rule 1: language filtering


def test_foreign_audio_and_subs_are_dropped():
    plan = plan_for(
        video(0),
        audio(1, 2, lang="eng"),
        audio(2, 2, lang="ger"),
        subtitle(3, "eng"),
        subtitle(4, "ger"),
    )
    kept = {out.src for out in plan.streams}
    assert kept == {0, 1, 3}
    assert any("drop audio 2 (ger)" in reason for reason in plan.reasons)
    assert any("drop subtitle 4 (ger)" in reason for reason in plan.reasons)


def test_original_language_is_kept():
    plan = plan_for(
        video(0),
        audio(1, 2, lang="kor"),
        audio(2, 2, lang="fre"),
        original="kor",
    )
    assert {out.src for out in plan.streams} == {0, 1}


def test_untagged_tracks_are_kept():
    plan = plan_for(video(0), audio(1, 2, lang=None), audio(2, 2, lang="und"))
    assert not plan.needed


def test_file_is_skipped_rather_than_left_silent():
    """Dropping every audio track would be worse than doing nothing."""
    plan = plan_for(video(0), audio(1, 6, lang="ger"), audio(2, 2, lang="fre"))
    assert plan.skip == "would remove every audio track"
    assert not plan.needed


def test_commentary_in_a_foreign_language_is_dropped_by_the_language_rule():
    plan = plan_for(
        video(0), audio(1, 2, lang="eng"), audio(2, 2, lang="ger", title="Commentary")
    )
    assert {out.src for out in plan.streams} == {0, 1}


# Rule 3: cover art


def test_cover_art_is_dropped():
    plan = plan_for(video(0), video(1, codec="png", attached_pic=1), audio(2, 2))
    assert plan.needed
    assert any("cover art" in reason for reason in plan.reasons)
    assert {out.src for out in plan.streams} == {0, 2}


def test_image_codec_without_the_flag_is_still_cover_art():
    plan = plan_for(video(0), video(1, codec="mjpeg"), audio(2, 2))
    assert any("cover art" in reason for reason in plan.reasons)


# Rule 4: ordering


def test_streams_are_ordered_video_audio_subtitles():
    plan = plan_for(video(0), subtitle(1, "eng"), audio(2, 6), audio(3, 2))
    kinds = [out.kind for out in plan.streams]
    assert kinds == ["video", "audio", "audio", "subtitle"]


def test_audio_is_ordered_by_ascending_channel_count():
    plan = plan_for(video(0), audio(1, 8), audio(2, 6), audio(3, 2))
    assert [out.channels for out in audio_out(plan)] == [2, 6, 8]
    assert plan.reasons == ["reorder streams"]


def test_reorder_alone_triggers_a_rewrite():
    plan = plan_for(video(0), audio(1, 6), audio(2, 2))
    assert plan.needed
    assert plan.reasons == ["reorder streams"]


def test_generated_downmix_sorts_ahead_of_a_commentary_stereo_track():
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    order = audio_out(plan)
    assert order[0].encode is True
    assert order[1].encode is False
    assert order[1].src == 2


def test_channel_rank_ordering():
    """Ascending by count so generated 4.0 or 6.1 tracks slot in correctly;
    mono and unknown sort last, never becoming the first-track fallback."""
    ranks = [channel_rank(n) for n in (2, 4, 6, 7, 8, None, 1)]
    assert ranks == sorted(ranks)


# Incidental changes


def test_data_stream_alone_does_not_trigger_a_rewrite():
    """Rewriting a 60GB remux to drop a timecode track is not worth it."""
    data = {
        "index": 2,
        "codec_type": "data",
        "codec_name": "bin_data",
        "tags": {},
        "disposition": {},
    }
    plan = plan_for(video(0), audio(1, 2), data)
    assert not plan.needed
    assert plan.incidental == ["drop data stream 2"]


def test_data_stream_rides_along_when_something_else_triggers():
    data = {
        "index": 3,
        "codec_type": "data",
        "codec_name": "bin_data",
        "tags": {},
        "disposition": {},
    }
    plan = plan_for(video(0), audio(1, 2, lang="eng"), audio(2, 2, lang="ger"), data)
    assert plan.needed
    assert 3 not in {out.src for out in plan.streams}


def test_attachments_are_always_carried_over():
    """Fonts, without which styled ASS subtitles render wrong."""
    font = {
        "index": 3,
        "codec_type": "attachment",
        "codec_name": "ttf",
        "tags": {"filename": "x.ttf"},
        "disposition": {},
    }
    plan = plan_for(video(0), audio(1, 2, lang="eng"), audio(2, 2, lang="ger"), font)
    assert 3 in {out.src for out in plan.streams}


# Rule opt-outs


def test_disabled_languages_keeps_foreign_tracks(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"languages"})
    plan = plan_for(
        video(0), audio(1, 2, lang="eng"), audio(2, 2, lang="ger"), subtitle(3, "ger")
    )
    assert {out.src for out in plan.streams} == {0, 1, 2, 3}
    assert not plan.needed


def test_disabled_downmix_adds_no_tracks(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"downmix"})
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    assert not any("downmix" in reason for reason in plan.reasons)
    assert not any(out.encode for out in plan.streams)


def test_disabled_cover_art_keeps_the_poster(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"cover_art"})
    plan = plan_for(video(0), video(1, codec="mjpeg"), audio(2, 2))
    assert not any("cover art" in reason for reason in plan.reasons)
    assert 1 in {out.src for out in plan.streams}


def test_disabled_order_never_reorders(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"order"})
    plan = plan_for(video(0), audio(1, 8), audio(2, 6), audio(3, 2))
    assert not plan.needed


def test_disabled_order_preserves_input_order_when_rewriting(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"order"})
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    # The downmix still triggers the rewrite, but streams keep the input
    # order and the generated track rides directly after its source.
    assert [out.src for out in plan.streams] == [0, 1, 1, 2]
    assert plan.streams[2].encode is True


def test_drop_commentary_removes_the_track(monkeypatch):
    monkeypatch.setattr(config, "DROP_COMMENTARY", True)
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Director's Commentary"))
    assert 2 not in {out.src for out in plan.streams}
    assert any("drop commentary audio 2" in reason for reason in plan.reasons)
    # The 5.1 still gets its downmix; the commentary never counted anyway.
    assert any(out.encode for out in plan.streams)


def test_drop_commentary_declines_rather_than_silencing(monkeypatch):
    monkeypatch.setattr(config, "DROP_COMMENTARY", True)
    plan = plan_for(video(0), audio(1, 2, title="Commentary"))
    assert 1 in {out.src for out in plan.streams}
    assert not any("commentary" in reason for reason in plan.reasons)


def test_commentary_is_kept_by_default():
    plan = plan_for(video(0), audio(1, 2), audio(2, 2, title="Commentary"))
    assert 2 in {out.src for out in plan.streams}


# SDH subtitles (ride along with a rewrite, never trigger one)


def test_sdh_dropped_when_a_full_subtitle_remains():
    plan = plan_for(
        video(0),
        audio(1, 2),
        subtitle(2, "eng"),
        subtitle(3, "eng", title="English (SDH)"),
    )
    assert 3 not in {out.src for out in plan.streams}
    assert any("SDH subtitle 3" in note for note in plan.incidental)
    assert not plan.needed


def test_sdh_disposition_flag_counts():
    plan = plan_for(
        video(0),
        audio(1, 2),
        subtitle(2, "eng"),
        subtitle(3, "eng", hearing_impaired=1),
    )
    assert 3 not in {out.src for out in plan.streams}


def test_lone_sdh_subtitle_is_kept():
    plan = plan_for(video(0), audio(1, 2), subtitle(2, "eng", title="English SDH"))
    assert 2 in {out.src for out in plan.streams}


def test_forced_subtitle_neither_drops_nor_counts_as_full():
    plan = plan_for(
        video(0),
        audio(1, 2),
        subtitle(2, "eng", forced=1),
        subtitle(3, "eng", title="English (SDH)"),
    )
    # No full subtitle exists, so the SDH stays; the forced track stays too.
    assert {out.src for out in plan.streams} == {0, 1, 2, 3}


def test_sdh_rule_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"sdh"})
    plan = plan_for(
        video(0),
        audio(1, 2),
        subtitle(2, "eng"),
        subtitle(3, "eng", title="English (SDH)"),
    )
    assert 3 in {out.src for out in plan.streams}


# Junk titles (ride along with a rewrite, never trigger one)


@pytest.mark.parametrize(
    ("title", "junk"),
    [
        ("AC3 5.1 @ 640kbps", True),
        ("Movie.2024.1080p.WEB-DL.x264-GRP", True),
        # A pure pattern test: load-bearing protection is the planner's job.
        ("Commentary @ 192kbps", True),
        # The default is conservative: bare codec names are not junk.
        ("DTS-HD MA", False),
        ("AAC 2.0", False),
        ("Director's Commentary", False),
        ("Surround", False),
        ("", False),
    ],
)
def test_is_junk_title(title, junk):
    assert is_junk_title(title) is junk


def test_load_bearing_titles_are_never_cleared():
    """Clearing a title the planner reads would break idempotence: the next
    plan would classify the track differently and make a different decision.
    """
    plan = plan_for(
        video(0),
        audio(1, 6),
        audio(2, 2, title="Commentary @ 192kbps"),
        subtitle(3, "eng", title="English (Forced) BluRay"),
        subtitle(4, "eng", title="English SDH 1080p"),
    )
    assert not any("junk title" in note for note in plan.incidental)
    # The forced track never counts as the full subtitle, so the SDH stays.
    assert {out.src for out in plan.streams} >= {3, 4}


def test_junk_title_alone_does_not_trigger():
    plan = plan_for(video(0), audio(1, 2, title="AAC 2.0 @ 192kbps"))
    assert not plan.needed
    assert any("junk title" in note for note in plan.incidental)


def test_junk_titles_cleared_when_rewriting_anyway():
    plan = plan_for(
        video(0),
        audio(1, 6, title="DTS-HD MA 5.1 @ 1509kbps"),
        audio(2, 2, lang="ger"),
        subtitle(3, "eng", title="English [SRT 1080p]"),
    )
    assert plan.needed  # the German track and the missing stereo trigger it
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    # Audio 0 is the generated downmix; the junk-titled 5.1 copies as audio 1.
    assert args[args.index("-metadata:s:a:1") + 1] == "title="
    assert args[args.index("-metadata:s:s:0") + 1] == "title="


def test_junk_container_title_is_cleared():
    plan = plan_for(video(0), audio(1, 2), title="Movie.2024.1080p.BluRay.x264-GRP")
    assert plan.clear_container_title
    assert not plan.needed
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-metadata") + 1] == "title="


# Containers


def test_conforming_file_is_left_alone():
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), subtitle(3, "eng"))
    assert not plan.needed
    assert plan.reasons == []


def test_file_with_no_video_is_skipped():
    plan = plan_for(audio(0, 2))
    assert plan.skip == "no video stream"


def test_hardlinked_file_is_skipped_when_configured(tmp_path, monkeypatch):
    """A file the download client still seeds is left alone.

    The skip happens before the probe, so no ffmpeg is needed here.
    """
    monkeypatch.setattr(config, "SKIP_HARDLINKS", True)
    library_file = tmp_path / "f.mkv"
    library_file.write_bytes(b"x")
    os.link(library_file, tmp_path / "seed.mkv")

    plan = build_plan(str(library_file), "eng")
    assert plan.skip is not None
    assert "SKIP_HARDLINKS" in plan.skip


# Command construction


def test_downmix_command_clears_the_default_disposition():
    """Without this the file ends up with two default audio tracks."""
    plan = plan_for(video(0), audio(1, 6, default=1), audio(2, 2, title="Commentary"))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert "-disposition:a:0" in args
    assert args[args.index("-disposition:a:0") + 1] == "0"


def test_command_copies_everything_it_does_not_encode():
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-c") + 1] == "copy"
    assert "-c:a:0" in args
    assert args[args.index("-ac:a:0") + 1] == "2"
    # Only the generated track is re-encoded.
    assert "-c:a:1" not in args


def test_command_maps_streams_in_plan_order():
    plan = plan_for(video(0), audio(1, 8), audio(2, 2))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    maps = [args[i + 1] for i, arg in enumerate(args) if arg == "-map"]
    assert maps == [f"0:{out.src}" for out in plan.streams]


def test_command_tags_the_downmix_with_its_source_language():
    plan = plan_for(video(0), audio(1, 6, lang="kor"), original="kor")
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert "language=kor" in args
    assert "title=2.0" in args


def test_bitrate_scales_with_the_generated_layout(monkeypatch):
    """AUDIO_BITRATE names the stereo rate, so the 5.1 downmix gets triple."""
    monkeypatch.setattr(config, "AUDIO_BITRATE", "192k")
    plan = plan_for(video(0), audio(1, 8))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-b:a:0") + 1] == "192k"
    assert args[args.index("-b:a:1") + 1] == "576k"
    assert "title=5.1" in args


def test_unparseable_bitrate_is_passed_through(monkeypatch):
    monkeypatch.setattr(config, "AUDIO_BITRATE", "0.2M")
    plan = plan_for(video(0), audio(1, 8))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-b:a:1") + 1] == "0.2M"


def test_plain_and_uppercase_rates_scale_too(monkeypatch):
    """320K once resolved to an unparseable 960K, silently disabling the
    weak-track comparison; one bitrate grammar keeps every form scalable."""
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320K")
    plan = plan_for(video(0), audio(1, 8))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-b:a:1") + 1] == "960k"

    monkeypatch.setattr(config, "AUDIO_BITRATE", "192000")
    plan = plan_for(video(0), audio(1, 8))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-b:a:1") + 1] == "576k"


def test_per_layout_bitrate_overrides_the_scaled_default(monkeypatch):
    monkeypatch.setattr(config, "AUDIO_BITRATE", "320k")
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0", "5.1:640k"})
    plan = plan_for(video(0), audio(1, 8))
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    assert args[args.index("-b:a:0") + 1] == "320k"
    assert args[args.index("-b:a:1") + 1] == "640k"
