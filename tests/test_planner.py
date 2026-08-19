"""The rules, tested against synthetic ffprobe output. No media required."""

import os

import pytest

from conftest import audio, probe_data, subtitle, video
from trackstarr import config
from trackstarr.command import ffmpeg_args
from trackstarr.media import is_junk_title
from trackstarr.planner import (
    OutStream,
    Plan,
    _downmix_rank,
    build_plan,
    channel_rank,
    new_plan,
    plan_from_probe,
)
from trackstarr.policy import Policy


def plan_for(*streams, original="eng", title="", path="/x/f.mkv"):
    return plan_from_probe(new_plan(path, original), probe_data(*streams, title=title))


def audio_out(plan):
    return [out for out in plan.streams if out.kind == "audio"]


def args_for(*streams):
    """The command a hand-built plan of these streams would run."""
    plan = Plan(path="f.mkv")
    plan.streams.extend(streams)
    return ffmpeg_args(plan, "OUT.mkv")


def other_stream(index: int, kind: str, codec: str) -> dict:
    """A stream of a kind the planner has no rule for: data, attachment."""
    return {
        "index": index,
        "codec_type": kind,
        "codec_name": codec,
        "tags": {},
        "disposition": {},
    }


# Rule 2: the downmix guarantee


def test_commentary_stereo_does_not_satisfy_the_downmix_rule():
    """The defect this tool exists to fix: a 2.0 commentary beside a 5.1 main
    track still needs a real downmix, or nothing stereo can direct play."""
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


@pytest.mark.parametrize(
    "marked",
    [
        {"tags": {"language": "eng", "title": "Isolated Score"}},
        # ffprobe reports MP4 track titles under ``name``, not ``title``.
        {"tags": {"language": "eng", "name": "Director's Commentary"}},
        {"disposition": {"comment": 1}},
        {"disposition": {"visual_impaired": 1}},
        {"disposition": {"descriptions": 1}},
    ],
    ids=["isolated score", "mp4 name tag", "comment", "visual impaired", "descriptions"],
)
def test_commentary_is_recognised_however_it_is_marked(marked):
    """Each of these has to keep the 2.0 from counting as the stereo track. The
    flags win when a muxer set them; most rips don't, so the title is next."""
    plan = plan_for(video(0), audio(1, 6), audio(2, 2) | marked)
    assert any("downmix from stream 1" in reason for reason in plan.reasons)


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


# The regenerate opt-in (REGENERATE_DOWNMIXES)


def tagged_downmix(index, channels, settings, title="2.0"):
    """An audio stream carrying the tag a generated downmix is written with."""
    stream = audio(index, channels, title=title)
    stream["tags"]["TRACKSTARR"] = settings
    return stream


def test_generated_mode_rebuilds_stale_downmixes(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "5.1": "640k"})
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert any(reason.startswith("regenerate 2.0 downmix") for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}
    assert [out.channels for out in audio_out(plan) if out.encode] == [2]


def test_generated_mode_leaves_matching_downmixes(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "5.1": "640k"})
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"), audio(2, 6))
    assert not plan.needed


@pytest.mark.parametrize("rate", ["320k", "320000", "320K"])
def test_respelling_a_rate_is_not_a_settings_change(monkeypatch, rate):
    """The tag is compared for equality, so without one canonical spelling per
    rate, editing 320k to 320000 would re-encode every track we have made."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": rate, "5.1": "640k"})
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"), audio(2, 6))
    assert not plan.needed


def test_regeneration_is_off_by_default():
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert not plan.needed


def test_generated_mode_never_touches_untagged_tracks(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    plan = plan_for(video(0), audio(1, 2, bitrate="96000"), audio(2, 6))
    assert not plan.needed


def test_stale_downmix_kept_when_no_source_survives(monkeypatch):
    """A file must never lose a layout it had."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"))
    assert not plan.needed
    assert 1 in {out.src for out in plan.streams}


@pytest.mark.parametrize(
    ("bitrate", "extra_tags"),
    [
        ("96000", {}),
        # Matroska rarely reports bit_rate, but mkvmerge writes BPS.
        (None, {"BPS-eng": "96000"}),
    ],
    ids=["mp4 bit_rate", "mkvmerge BPS tag"],
)
def test_all_mode_replaces_weak_stereo(monkeypatch, bitrate, extra_tags):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "5.1": "640k"})
    weak = audio(1, 2, bitrate=bitrate)
    weak["tags"].update(extra_tags)
    plan = plan_for(video(0), weak, audio(2, 6))
    assert any("replace weak 2.0 track 1 (96k)" in reason for reason in plan.reasons)
    assert any("add 2.0 downmix from stream 2" in reason for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}


def test_all_mode_leaves_unknown_bitrates_alone(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert not plan.needed


def test_all_mode_only_replaces_clearly_weak_tracks(monkeypatch):
    """A decent 448k AC3 5.1 survives a 640k AAC target; _WEAK_BITRATE_RATIO says
    why the margin is that wide."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "5.1": "640k"})
    plan = plan_for(
        video(0), audio(1, 2, bitrate="300000"), audio(2, 6, bitrate="448000"), audio(3, 8)
    )
    assert not plan.needed


def test_regeneration_is_matroska_only(monkeypatch):
    """MP4 drops the identifying tag, where regeneration deleted commentary and
    re-encoded its own tracks every sweep."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    plan = plan_for(video(0), audio(1, 2, bitrate="96000"), audio(2, 6), path="/x/f.mp4")
    assert not plan.needed


def test_duplicate_layouts_regenerate_toward_one_target(monkeypatch):
    """The drop side has to judge against the layout the rebuild uses, or two
    names for one channel count regenerate forever, each rewrite making a
    track the other reads as stale. Startup refuses the pair; this is the belt."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "generated")
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"4.2", "5.1"})
    # Both 6 channels; 4.2 sorts first, so its rate is the one target.
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"4.2": "640k", "5.1": "320k"})
    plan = plan_for(video(0), tagged_downmix(1, 6, "aac 128k", title="5.1"), audio(2, 8))
    generated = [out for out in audio_out(plan) if out.encode]
    assert [out.bitrate for out in generated] == ["640k"]
    assert any("as aac 640k" in reason for reason in plan.reasons)


def test_all_mode_never_replaces_commentary(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    plan = plan_for(video(0), audio(1, 2, title="Commentary", bitrate="96000"), audio(2, 6))
    # The commentary survives; the missing real stereo is downmixed anyway.
    assert 1 in {out.src for out in plan.streams}
    assert any("add 2.0 downmix" in reason for reason in plan.reasons)


# The remux opt-in (REMUX_TO_MKV)


@pytest.mark.parametrize(
    ("remux", "path", "reasons", "out_path"),
    [
        (True, "/x/f.mp4", ["remux to mkv (REMUX_TO_MKV)"], "/x/f.mkv"),
        (True, "/x/f.mkv", [], "/x/f.mkv"),
        (False, "/x/f.mp4", [], "/x/f.mp4"),
    ],
    ids=["mp4 converts", "matroska is left alone", "off by default"],
)
def test_remux_converts_only_mp4_and_only_when_asked(
    monkeypatch, remux, path, reasons, out_path
):
    monkeypatch.setattr(config, "REMUX_TO_MKV", remux)
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), path=path)
    assert plan.reasons == reasons
    assert plan.out_path == out_path


def test_remux_converts_mov_text_subtitles(monkeypatch):
    """Matroska has no mov_text, so the text converts to SRT; other subtitle codecs
    stream copy as usual."""
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


@pytest.mark.parametrize(
    ("codec", "attached_pic"),
    [("png", 1), ("mjpeg", 0)],
    ids=["disposition flag", "image codec alone"],
)
def test_cover_art_is_dropped(codec, attached_pic):
    plan = plan_for(video(0), video(1, codec=codec, attached_pic=attached_pic), audio(2, 2))
    assert plan.needed
    assert any("cover art" in reason for reason in plan.reasons)
    assert {out.src for out in plan.streams} == {0, 2}


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


def test_a_reorder_forced_by_another_rule_rides_along():
    """The streams come out reordered either way, so the history has to say
    so, as the ride-along it was rather than the trigger it wasn't."""
    plan = plan_for(video(0), audio(1, 6), audio(2, 2), audio(3, 2, lang="ger"))
    assert any("drop audio" in reason for reason in plan.reasons)
    assert "order" not in plan.rules
    assert plan.incidental == ["reorder streams"]
    assert plan.incidental_rules == {"order"}


def test_a_downmix_insertion_alone_is_not_a_reorder():
    """A generated track is an insertion, not a move: the copied streams kept
    their relative order, so nothing is recorded against the order rule."""
    plan = plan_for(video(0), audio(1, 6))
    assert plan.rules == {"downmix"}
    assert "order" not in plan.incidental_rules


def test_generated_downmix_sorts_ahead_of_a_commentary_stereo_track():
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    order = audio_out(plan)
    assert order[0].encode is True
    assert order[1].encode is False
    assert order[1].src == 2


def test_channel_rank_ordering():
    """The order channel_rank documents: ascending, mono and unknown last."""
    ranks = [channel_rank(n) for n in (2, 4, 6, 7, 8, None, 1)]
    assert ranks == sorted(ranks)


# Incidental changes


def test_data_stream_alone_does_not_trigger_a_rewrite():
    """Rewriting a 60GB remux to drop a timecode track is not worth it."""
    plan = plan_for(video(0), audio(1, 2), other_stream(2, "data", "bin_data"))
    assert not plan.needed
    assert plan.incidental == ["drop data stream 2"]


def test_data_stream_rides_along_when_something_else_triggers():
    plan = plan_for(
        video(0),
        audio(1, 2, lang="eng"),
        audio(2, 2, lang="ger"),
        other_stream(3, "data", "bin_data"),
    )
    assert plan.needed
    assert 3 not in {out.src for out in plan.streams}


def test_attachments_are_always_carried_over():
    """Fonts, without which styled ASS subtitles render wrong."""
    plan = plan_for(
        video(0),
        audio(1, 2, lang="eng"),
        audio(2, 2, lang="ger"),
        other_stream(3, "attachment", "ttf"),
    )
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


@pytest.mark.parametrize(
    ("title", "hearing_impaired"),
    [("English (SDH)", 0), ("", 1)],
    ids=["title", "disposition flag"],
)
def test_sdh_dropped_when_a_full_subtitle_remains(title, hearing_impaired):
    plan = plan_for(
        video(0),
        audio(1, 2),
        subtitle(2, "eng"),
        subtitle(3, "eng", title=title, hearing_impaired=hearing_impaired),
    )
    assert 3 not in {out.src for out in plan.streams}
    assert any("SDH subtitle 3" in note for note in plan.incidental)
    assert not plan.needed


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
    assert is_junk_title(title, Policy.from_config()) is junk


def test_load_bearing_titles_are_never_cleared():
    """Clearing a title the planner reads breaks idempotence: the next plan would
    classify the track differently."""
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
    """A file the download client still seeds is left alone, before the probe."""
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


def test_each_layout_is_encoded_at_its_own_rate(monkeypatch):
    """Nothing is derived from anything else: a downmix is made at the rate its own
    variable states."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0", "5.1"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "192k", "5.1": "448k"})
    args = ffmpeg_args(plan_for(video(0), audio(1, 8)), "/tmp/out.mkv")
    assert args[args.index("-b:a:0") + 1] == "192k"
    assert args[args.index("-b:a:1") + 1] == "448k"
    assert "title=5.1" in args


@pytest.mark.parametrize("rate", ["320K", "320000"])
def test_a_rate_reaches_ffmpeg_in_its_one_spelling(monkeypatch, rate):
    """The command and the tag agree whatever the variable said, because a 320K
    left as 320K made the weak-track comparison unparseable."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": rate})
    args = ffmpeg_args(plan_for(video(0), audio(1, 8)), "/tmp/out.mkv")
    assert args[args.index("-b:a:0") + 1] == "320k"
    assert "TRACKSTARR=aac 320k" in args


def test_an_unrelated_language_sorts_behind_english():
    """The order _downmix_rank documents: original, English, then the rest."""
    original = audio(1, 6, lang="jpn")
    english = audio(2, 6, lang="eng")
    other = audio(3, 6, lang="fre")
    ranked = sorted([other, english, original], key=lambda s: _downmix_rank(s, "jpn"))
    assert [s["tags"]["language"] for s in ranked] == ["jpn", "eng", "fre"]


def test_a_kept_subtitle_title_is_reasserted_in_the_command():
    """MP4 drops track names on a plain copy, so every kept title is written again."""
    args = args_for(
        OutStream(src=0, kind="video"),
        OutStream(src=1, kind="subtitle", title="Forced (English)"),
    )
    assert "title=Forced (English)" in args


def test_a_copied_audio_track_keeps_its_own_title():
    """The ordinary case between a generated downmix and a stripped junk title:
    copied through, title re-asserted as it was."""
    args = args_for(
        OutStream(src=0, kind="video"),
        OutStream(src=1, kind="audio", title="Surround 5.1"),
        OutStream(src=2, kind="audio", title="Commentary"),
    )
    assert "title=Surround 5.1" in args
    assert "title=Commentary" in args


@pytest.mark.parametrize("lang", ["jpn", None], ids=["tagged source", "untagged source"])
def test_a_downmix_asserts_only_the_language_its_source_had(lang):
    """A downmix inherits an untagged source and must not claim a language the
    source never had. It also sorts ahead of that source, so its metadata is
    written mid-list with the copied original still following."""
    args = args_for(
        OutStream(src=0, kind="video"),
        OutStream(
            src=1,
            kind="audio",
            encode=True,
            channels=2,
            lang=lang,
            title="2.0",
            bitrate="192k",
        ),
        OutStream(src=1, kind="audio", title="Surround 5.1"),
    )
    assert [arg for arg in args if arg.startswith("language=")] == (
        [f"language={lang}"] if lang else []
    )
    assert "title=Surround 5.1" in args
