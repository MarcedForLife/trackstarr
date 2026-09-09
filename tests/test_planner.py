"""The rules, tested against synthetic ffprobe output. No media required."""

import os

import pytest

from conftest import (
    audio,
    probe_data,
    set_config,
    set_langs,
    set_layouts,
    set_rules,
    subtitle,
    video,
)
from trackstarr.command import ffmpeg_args
from trackstarr.media import matches_release_tags
from trackstarr.planner import (
    OutStream,
    Plan,
    _downmix_rank,
    build_plan,
    channel_rank,
    describe,
    new_plan,
    plan_from_probe,
    planned_tracks,
    track_changes,
    why,
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


def test_the_default_list_downmixes_the_original_language_and_english():
    """Both rows add, so a Korean film with an English dub gets a 2.0 of
    each rather than one in whichever language sorted first."""
    plan = plan_for(
        video(0),
        audio(1, 6, lang="eng"),
        audio(2, 6, lang="kor"),
        original="kor",
    )
    generated = {(out.channels, out.lang) for out in audio_out(plan) if out.encode}
    assert generated == {(2, "kor"), (2, "eng")}


def test_a_language_left_at_keep_gets_no_downmix():
    """The opt-out: English survives, and nothing is generated for it."""
    set_langs("original", "eng:keep")
    plan = plan_for(
        video(0),
        audio(1, 6, lang="eng"),
        audio(2, 6, lang="kor"),
        original="kor",
    )
    assert {out.src for out in plan.streams if not out.encode} == {0, 1, 2}
    assert [(out.channels, out.lang) for out in audio_out(plan) if out.encode] == [(2, "kor")]


def test_the_original_language_gets_every_layout():
    """A stereo track in some other language doesn't answer for the one the
    title was made in, which is the track a stereo player should land on."""
    plan = plan_for(
        video(0),
        audio(1, 6, lang="jpn"),
        audio(2, 2, lang="eng"),
        original="jpn",
    )
    assert any("add 2.0 downmix from stream 1" in reason for reason in plan.reasons)
    assert [(out.channels, out.lang) for out in audio_out(plan) if out.encode] == [(2, "jpn")]


def test_a_list_that_adds_no_language_asks_only_for_the_layout():
    """With nothing wanted by name, any language fills a layout."""
    set_langs("original:keep", "eng:keep")
    plan = plan_for(
        video(0),
        audio(1, 6, lang="jpn"),
        audio(2, 2, lang="eng"),
        original="jpn",
    )
    # The file is still rewritten, for the order rule; nothing is generated.
    assert not [out for out in audio_out(plan) if out.encode]


def test_every_added_language_guarantees_a_layout_each():
    set_langs("eng", "fre")
    plan = plan_for(
        video(0),
        audio(1, 6, lang="eng"),
        audio(2, 6, lang="fre"),
        original="eng",
    )
    generated = [out for out in audio_out(plan) if out.encode]
    assert [(out.channels, out.lang) for out in generated] == [(2, "eng"), (2, "fre")]


def test_a_wanted_language_with_nothing_bigger_generates_nothing():
    """Nothing is upmixed for a named language any more than for a layout."""
    set_langs("eng", "fre")
    plan = plan_for(
        video(0),
        audio(1, 6, lang="eng"),
        audio(2, 2, lang="fre"),
        original="eng",
    )
    generated = [out for out in audio_out(plan) if out.encode]
    assert [(out.channels, out.lang) for out in generated] == [(2, "eng")]


def test_a_layout_no_wanted_language_can_fill_falls_back():
    """The original language is absent, so the layout is filled from what the
    file does have rather than left empty."""
    plan = plan_for(video(0), audio(1, 6, lang="eng"), original="jpn")
    assert [(out.channels, out.lang) for out in audio_out(plan) if out.encode] == [(2, "eng")]


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


def test_custom_layouts_replace_the_default():
    set_config(AUDIO_LAYOUTS=("2.0",))
    plan = plan_for(video(0), audio(1, 8))
    assert [out.channels for out in audio_out(plan) if out.encode] == [2]


def test_layouts_with_equal_channel_counts_generate_one_track():
    set_config(AUDIO_LAYOUTS=("4.2", "5.1"))
    plan = plan_for(video(0), audio(1, 8))
    assert len([out for out in audio_out(plan) if out.encode]) == 1


# The regenerate rule


def tagged_downmix(index, channels, settings, title="2.0", lang="eng"):
    """An audio stream carrying the tag a generated downmix is written with."""
    stream = audio(index, channels, lang=lang, title=title)
    stream["tags"]["TRACKSTARR"] = settings
    return stream


def test_generated_mode_rebuilds_stale_downmixes():
    set_rules(regenerate="always")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert any(reason.startswith("regenerate 2.0 downmix") for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}
    assert [out.channels for out in audio_out(plan) if out.encode] == [2]


def test_a_regenerated_track_is_one_reason_not_two():
    """The drop and the track replacing it are one change. Recorded apart,
    the library read "replace low-bitrate 5.1 track 2" over "add 5.1 downmix from
    stream 3", which says the file gains a layout it already had."""
    set_rules(regenerate="always")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert plan.reasons == [
        "regenerate 2.0 downmix 1 (aac 192k, 2.0) as aac 320k from stream 2 (6ch eng)"
    ]
    assert plan.rules == {"regenerate"}


def test_a_drop_with_no_track_to_claim_it_still_says_so():
    """Two stale copies of one layout, one track back: both drops are worth
    naming, and only one of them is the one the new track replaces."""
    set_rules(regenerate="always")
    set_config(AUDIO_LAYOUTS=("2.0",))
    plan = plan_for(
        video(0),
        tagged_downmix(1, 2, "aac 192k"),
        tagged_downmix(2, 2, "aac 128k"),
        audio(3, 6),
    )
    assert [reason.split(" as ")[0] for reason in plan.reasons] == [
        "regenerate 2.0 downmix 1 (aac 192k, 2.0)",
        "regenerate 2.0 downmix 2 (aac 128k, 2.0)",
    ]
    assert sum("from stream 3" in reason for reason in plan.reasons) == 1


@pytest.mark.parametrize("wanted", [True, False], ids=["original wanted", "no language wanted"])
def test_a_rebuilt_downmix_comes_back_in_its_own_language(wanted):
    """Dropping a French 2.0 and generating an English one in its place would
    be a loss dressed as a regeneration, whatever the settings ask for."""
    set_rules(regenerate="always")
    set_langs("fre", "eng" if wanted else "eng:keep")
    plan = plan_for(
        video(0),
        tagged_downmix(1, 2, "aac 192k", lang="fre"),
        audio(2, 6, lang="fre"),
        audio(3, 6, lang="eng"),
        original="eng",
    )
    assert 1 not in {out.src for out in plan.streams}
    generated = {(out.channels, out.lang) for out in audio_out(plan) if out.encode}
    assert (2, "fre") in generated
    assert ((2, "eng") in generated) is wanted


def test_generated_mode_leaves_matching_downmixes():
    set_rules(regenerate="always")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"), audio(2, 6))
    assert not plan.needed


@pytest.mark.parametrize("rate", ["320k", "320000", "320K"])
def test_respelling_a_rate_is_not_a_settings_change(rate):
    """The tag is compared for equality, so without one canonical spelling per
    rate, editing 320k to 320000 would re-encode every track we have made."""
    set_rules(regenerate="always")
    set_layouts(f"2.0:aac:{rate}", "5.1:ac3:640k")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"), audio(2, 6))
    assert not plan.needed


def test_regeneration_is_off_by_default():
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k"), audio(2, 6))
    assert not plan.needed


def test_generated_mode_never_touches_untagged_tracks():
    set_rules(regenerate="always")
    plan = plan_for(video(0), audio(1, 2, bitrate="96000"), audio(2, 6))
    assert not plan.needed


def test_stale_downmix_kept_when_no_source_survives():
    """A file must never lose a layout it had."""
    set_rules(regenerate="always")
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
def test_all_mode_replaces_low_bitrate_stereo(bitrate, extra_tags):
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    low_rate = audio(1, 2, bitrate=bitrate)
    low_rate["tags"].update(extra_tags)
    plan = plan_for(video(0), low_rate, audio(2, 6))
    # One change, said once: the track that went and the one that took its
    # place were two reasons for a while, which read as a file gaining a
    # layout it already had.
    assert [reason for reason in plan.reasons if "2.0" in reason] == [
        "replace low-bitrate 2.0 track 1 (96k) with a fresh downmix from stream 2 (6ch eng)"
    ]
    assert 1 not in {out.src for out in plan.streams}


def test_all_mode_leaves_unknown_bitrates_alone():
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert not plan.needed


def test_all_mode_leaves_a_track_near_its_layouts_rate():
    """A 300k stereo is 94% of its 320k target, so the default 80 leaves it. The
    448k 5.1 is under 80% of 640k and the 7.1 above it has more to give, so that
    one goes."""
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    plan = plan_for(
        video(0), audio(1, 2, bitrate="300000"), audio(2, 6, bitrate="448000"), audio(3, 8)
    )
    low = [reason for reason in plan.reasons if "low-bitrate" in reason]
    assert low == [
        "replace low-bitrate 5.1 track 2 (448k) with a fresh downmix from stream 3 (8ch eng)"
    ]


def test_the_low_bitrate_line_is_where_the_setting_puts_it():
    """A 280k stereo is fine at the default 80 and low-bitrate at 90, which is the
    whole point of the setting being one."""
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    set_config(REGENERATE_BELOW_PERCENT=90)
    plan = plan_for(video(0), audio(1, 2, bitrate="280000"), audio(2, 6, bitrate="448000"))
    assert any("replace low-bitrate 2.0 track 1 (280k)" in reason for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}


def test_a_low_bitrate_track_stays_when_nothing_bigger_has_more_to_give():
    """A downmix holds no more than its source did, so a 96k stereo beside a
    5.1 reporting 224k is already as good as this file makes stereo: replacing
    it would spend a rewrite writing the same bits at a bigger number."""
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    plan = plan_for(video(0), audio(1, 2, bitrate="96000"), audio(2, 6, bitrate="224000"))
    assert not plan.needed
    assert 1 in {out.src for out in plan.streams}


def test_a_low_bitrate_track_goes_when_any_bigger_track_has_the_bits():
    """The poor 5.1 above does not speak for the file: a rebuild comes from the
    best source there is, so every bigger track counts, not the first one."""
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    plan = plan_for(
        video(0),
        audio(1, 2, bitrate="96000"),
        audio(2, 6, bitrate="224000"),
        audio(3, 8, bitrate="1500000"),
    )
    assert any("replace low-bitrate 2.0 track 1 (96k)" in reason for reason in plan.reasons)
    # The 5.1 is under its own line as well, and has the same 7.1 to come from.
    assert any("replace low-bitrate 5.1 track 2 (224k)" in reason for reason in plan.reasons)


def test_regeneration_is_matroska_only():
    """MP4 drops the identifying tag, where regeneration deleted commentary and
    re-encoded its own tracks every sweep."""
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    plan = plan_for(video(0), audio(1, 2, bitrate="96000"), audio(2, 6), path="/x/f.mp4")
    assert not plan.needed


def test_duplicate_layouts_regenerate_toward_one_target():
    """The drop side has to judge against the layout the rebuild uses, or two
    names for one channel count regenerate forever, each rewrite making a
    track the other reads as stale. Startup refuses the pair; this is the belt."""
    set_rules(regenerate="always")
    # Both 6 channels; 4.2 sorts first, so its rate is the one target.
    set_layouts("4.2:aac:640k", "5.1:aac:320k")
    plan = plan_for(video(0), tagged_downmix(1, 6, "aac 128k", title="5.1"), audio(2, 8))
    generated = [out for out in audio_out(plan) if out.encode]
    assert [out.bitrate for out in generated] == ["640k"]
    assert any("as aac 640k" in reason for reason in plan.reasons)


def test_all_mode_never_replaces_commentary():
    set_rules(regenerate="always")
    set_config(REGENERATE_SCOPE="all")
    plan = plan_for(video(0), audio(1, 2, title="Commentary", bitrate="96000"), audio(2, 6))
    # The commentary survives; the missing real stereo is downmixed anyway.
    assert 1 in {out.src for out in plan.streams}
    assert any("add 2.0 downmix" in reason for reason in plan.reasons)


# The other half of the rule: a track over its rate, re-encoded from itself


def leaner(above: int = 120, scope: str = "all") -> None:
    """The regenerate rule on with stereo wanted at 192k, so a 320k one is
    167% of its rate and every test below states only what it varies."""
    set_rules(regenerate="always")
    set_layouts("2.0:aac:192k", "5.1")
    set_config(REGENERATE_SCOPE=scope)
    set_config(REGENERATE_ABOVE_PERCENT=above)


def test_a_fat_track_is_left_alone_until_the_setting_says_otherwise(monkeypatch):
    """Off at 0, which is the default: this is the one rule that spends quality
    to save space, so nobody gets it without asking."""
    leaner(above=0)
    plan = plan_for(video(0), audio(1, 2, bitrate="320000"), audio(2, 6))
    assert not plan.needed


def test_a_fat_track_is_re_encoded_from_itself(monkeypatch):
    """From itself rather than from the 5.1 beside it: the stereo in the file
    may be a real mix rather than a fold-down, and this setting is about the
    rate, not the source."""
    leaner()
    plan = plan_for(video(0), audio(1, 2, bitrate="320000"), audio(2, 6))
    assert plan.reasons == ["re-encode high-bitrate 2.0 track 1 (320k) from itself as aac 192k"]
    (made,) = [out for out in audio_out(plan) if out.encode]
    assert (made.src, made.channels, made.codec, made.bitrate) == (1, 2, "aac", "192k")
    # The track is its own source: mapped as an input, gone as a copy.
    assert 1 not in {out.src for out in audio_out(plan) if not out.encode}


def test_the_high_bitrate_line_is_where_the_setting_puts_it(monkeypatch):
    """A 220k stereo is 115% of its 192k target: fine at 120, fat at 110."""
    leaner()
    assert not plan_for(video(0), audio(1, 2, bitrate="220000"), audio(2, 6)).needed
    leaner(above=110)
    plan = plan_for(video(0), audio(1, 2, bitrate="220000"), audio(2, 6))
    assert any("re-encode high-bitrate 2.0 track 1 (220k)" in reason for reason in plan.reasons)


def test_a_track_at_its_layouts_rate_is_never_re_encoded(monkeypatch):
    """What keeps the pass idempotent: the track a rewrite leaves behind reads
    as at its rate, and the lowest line is above it."""
    leaner(above=110)
    assert not plan_for(video(0), audio(1, 2, bitrate="192000"), audio(2, 6)).needed


def test_a_fat_real_track_is_the_all_scopes_to_touch(monkeypatch):
    """Generated means our own tracks, whatever the rate beside them says."""
    leaner(scope="generated")
    assert not plan_for(video(0), audio(1, 2, bitrate="320000"), audio(2, 6)).needed


def test_a_fat_generated_track_goes_under_either_scope(monkeypatch):
    """The gap this fills: once a file is trimmed to its downmixes, nothing
    bigger survives to rebuild from, so a later rate change reached nothing.
    Matroska reports no rate for one, so the tag is what it is judged by."""
    leaner(scope="generated")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"))
    assert plan.reasons == [
        "re-encode high-bitrate 2.0 downmix 1 (320k, 2.0) from itself as aac 192k"
    ]
    assert [out.bitrate for out in audio_out(plan) if out.encode] == ["192k"]


def test_a_surviving_bigger_track_beats_a_re_encode(monkeypatch):
    """A fold-down of the original beats a second pass over a track that has
    already been through an encoder, so the rebuild side goes first."""
    leaner()
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 320k"), audio(2, 6))
    assert plan.reasons == [
        "regenerate 2.0 downmix 1 (aac 320k, 2.0) as aac 192k from stream 2 (6ch eng)"
    ]


def test_commentary_is_never_re_encoded(monkeypatch):
    """As on the low-bitrate side: a 2.0 commentary is not the stereo track."""
    leaner()
    plan = plan_for(video(0), audio(1, 2, title="Commentary", bitrate="320000"), audio(2, 6))
    assert 1 in {out.src for out in audio_out(plan) if not out.encode}
    assert plan.reasons == ["add 2.0 downmix from stream 2 (6ch eng)"]


def test_a_lossless_master_is_never_re_encoded(monkeypatch):
    """A 4Mb/s DTS-HD MA 5.1 is over every line there is, and All would have
    handed back the layout's 640k. Removing a layout is where that is asked for,
    in writing."""
    leaner()
    master = audio(1, 6, title="DTS-HD MA 5.1", bitrate="4000000")
    master |= {"codec_name": "dts", "profile": "DTS-HD MA"}
    plan = plan_for(video(0), master)
    assert plan.reasons == ["add 2.0 downmix from stream 1 (6ch eng)"]
    assert 1 in {out.src for out in audio_out(plan) if not out.encode}


def test_a_fat_lossy_source_track_is_re_encoded(monkeypatch):
    """The other side of the line: a 1.5Mb/s DTS 5.1 against a 640k layout is
    what All plus a share is for, and it is nobody's master."""
    leaner()
    fat = audio(1, 6, bitrate="1509000") | {"codec_name": "dts", "profile": "DTS-HD HRA"}
    plan = plan_for(video(0), fat)
    assert "re-encode high-bitrate 5.1 track 1 (1509k) from itself as ac3 640k" in plan.reasons


def test_a_lossless_layout_is_never_re_encoded(monkeypatch):
    """The rate beside a lossless encoder does nothing, so every track reads as
    over it and re-encoding would grow the one this is here to shrink."""
    leaner()
    set_layouts("2.0:flac:320k", "5.1")
    assert not plan_for(video(0), audio(1, 2, bitrate="1500000"), audio(2, 6)).needed


def test_a_size_no_layout_names_has_no_rate_to_be_over(monkeypatch):
    """A track is judged against its own layout's rate, so a 7.1 in a file whose
    settings name 2.0 and 5.1 is left as it is, at whatever rate. Removing it is
    what a layout row is for."""
    leaner()
    plan = plan_for(
        video(0),
        audio(1, 2, bitrate="192000"),
        audio(2, 6, bitrate="640000"),
        audio(3, 8, bitrate="3000000"),
    )
    assert not plan.needed
    assert 3 in {out.src for out in audio_out(plan) if not out.encode}


# The drop_layouts rule: the one drop nothing can undo


def removing(*entries: str) -> None:
    """The shipped layouts, plus these set to remove. The row is the whole
    switch; there is no mode."""
    set_layouts("2.0", "5.1", *(f"{entry}:remove" for entry in entries))


def test_the_source_goes_once_its_downmixes_exist(monkeypatch):
    """The whole point: a 7.1 costs tens of gigabytes, and once 2.0 and 5.1 are
    in the file nothing plays it."""
    removing("7.1")
    plan = plan_for(video(0), audio(1, 8))
    assert any("add 2.0 downmix from stream 1" in reason for reason in plan.reasons)
    assert any("drop 7.1 audio 1 (eng)" in reason for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams if not out.encode}
    assert [out.channels for out in audio_out(plan)] == [2, 6]


def test_a_size_nothing_would_replace_still_goes():
    """The row is the instruction. A 7.1 beside a real 5.1 goes with nothing
    added at all, since the 5.1 is what the file is left with."""
    set_layouts("7.1:remove")
    plan = plan_for(video(0), audio(1, 8), audio(2, 6))
    assert {out.src for out in plan.streams} == {0, 2}


def test_the_last_audio_track_is_never_taken():
    """The one thing the drop may not do. Nothing replaces the 7.1 here, so it
    stays and the plan says nothing about it."""
    set_layouts("7.1:remove")
    plan = plan_for(video(0), audio(1, 8))
    assert not plan.needed
    assert 1 in {out.src for out in plan.streams}


def test_a_generated_downmix_counts_as_what_is_left(monkeypatch):
    """The 7.1 is the file's only audio, and it still goes: the 2.0 and 5.1 the
    same rewrite makes from it are what the file ends up with."""
    removing("7.1")
    plan = plan_for(video(0), audio(1, 8))
    assert [out.encode for out in audio_out(plan)] == [True, True]


def test_smaller_layouts_drop_just_as_well():
    """The opposite setup: a receiver-only house guarantees 5.1 and wants no
    stereo at all."""
    set_layouts("2.0:remove", "5.1")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert any("drop 2.0 audio 1 (eng)" in reason for reason in plan.reasons)
    assert {out.src for out in plan.streams} == {0, 2}


def test_every_language_at_that_size_goes(monkeypatch):
    """No exception for a language whose only track is the dropped size: the
    list names sizes, not languages."""
    removing("7.1")
    set_langs("eng", "fre")
    plan = plan_for(video(0), audio(1, 8, lang="eng"), audio(2, 8, lang="fre"))
    assert not [out for out in plan.streams if out.src in {1, 2} and not out.encode]


def test_commentary_at_a_dropped_size_goes_too(monkeypatch):
    """Nothing about commentary makes a 7.1 of it worth keeping."""
    removing("7.1")
    plan = plan_for(video(0), audio(1, 8), audio(2, 8, title="Director's Commentary"))
    assert {out.src for out in plan.streams if not out.encode} == {0}


def test_a_file_already_trimmed_is_left_alone(monkeypatch):
    """Idempotence: a guaranteed layout can never be a dropped one."""
    removing("7.1")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6))
    assert not plan.needed


def test_the_shipped_defaults_remove_no_layout():
    """Every shipped row adds, and a change that deletes a mix must ship that
    way."""
    plan = plan_for(video(0), audio(1, 8))
    assert 1 in {out.src for out in plan.streams}


def test_the_drop_orders_a_rewrite_of_its_own(monkeypatch):
    """No ride-along mode: a file needing nothing else is still rewritten,
    since a 7.1 is worth more than the rewrite costs."""
    removing("7.1")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), audio(3, 8))
    assert plan.needed
    assert any("drop 7.1 audio 3 (eng)" in reason for reason in plan.reasons)
    assert plan.incidental == []


# The remux rule


@pytest.mark.parametrize(
    ("mode", "path", "reasons", "out_path"),
    [
        ("always", "/x/f.mp4", ["remux to mkv (RULE_REMUX)"], "/x/f.mkv"),
        ("always", "/x/f.mkv", [], "/x/f.mkv"),
        ("never", "/x/f.mp4", [], "/x/f.mp4"),
    ],
    ids=["mp4 converts", "matroska is left alone", "off by default"],
)
def test_remux_converts_only_mp4_and_only_when_asked(mode, path, reasons, out_path):
    set_rules(remux=mode)
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), path=path)
    assert plan.reasons == reasons
    assert plan.out_path == out_path


def test_remux_converts_mov_text_subtitles():
    """Matroska has no mov_text, so the text converts to SRT; other subtitle codecs
    stream copy as usual."""
    set_rules(remux="always")
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


def test_the_original_language_can_be_left_out():
    """A library watched in dub: no original row, so the list is the whole
    keep-list."""
    set_langs("eng")
    plan = plan_for(
        video(0),
        audio(1, 2, lang="kor"),
        audio(2, 2, lang="eng"),
        original="kor",
    )
    assert {out.src for out in plan.streams} == {0, 2}
    assert any("drop audio 1 (kor)" in reason for reason in plan.reasons)


def test_untagged_tracks_are_kept():
    plan = plan_for(video(0), audio(1, 2, lang=None), audio(2, 2, lang="und"))
    assert not plan.needed


def test_file_is_skipped_rather_than_left_silent():
    """Dropping every audio track would be worse than doing nothing."""
    plan = plan_for(video(0), audio(1, 6, lang="ger"), audio(2, 2, lang="fre"))
    assert plan.skip == "would remove every audio track"
    assert not plan.needed


def test_a_skip_describes_itself_before_what_the_rules_wanted():
    """pending.tsv and the sweep cache both carry describe(). A row reading
    "drop audio 1 (ger)" for a file nothing touches reads as a rewrite that
    never comes; the wanted drops still follow, since they name the tracks."""
    plan = plan_for(video(0), audio(1, 6, lang="ger"), audio(2, 2, lang="fre"))
    assert describe(plan) == (
        "would remove every audio track: drop audio 1 (ger); drop audio 2 (fre)"
    )


def test_a_skip_with_nothing_to_say_still_says_why():
    """The video-only file every library has: no rule fires, so the reason
    cell used to be blank and the verdict unexplainable."""
    assert describe(plan_for(video(0))) == "no audio streams"


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


def test_a_reorder_forced_by_another_rule_is_still_the_order_rule():
    """The streams come out reordered either way, so the history has to say so.
    Which half it lands in is the mode's answer and nothing else's: always here,
    riding along below."""
    plan = plan_for(video(0), audio(1, 6), audio(2, 2), audio(3, 2, lang="ger"))
    assert any("drop audio" in reason for reason in plan.reasons)
    assert "order" in plan.rules
    assert "reorder streams" in plan.reasons

    set_rules(order="alongside")
    plan = plan_for(video(0), audio(1, 6), audio(2, 2), audio(3, 2, lang="ger"))
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
    """The order channel_rank documents when no layout matches: ascending,
    mono and unknown last."""
    ranks = [channel_rank(n) for n in (2, 4, 6, 7, 8, None, 1)]
    assert ranks == sorted(ranks)


def test_channel_rank_follows_the_layout_order():
    """AUDIO_LAYOUTS order beats channel count for the sizes it names."""
    ranks = [channel_rank(n, [6, 2]) for n in (6, 2, 4, 8, None)]
    assert ranks == sorted(ranks)


def test_channel_rank_puts_unnamed_sizes_after_every_layout():
    """An 8-channel track sorts behind a listed stereo, not ahead of it."""
    assert channel_rank(2, [2, 6]) < channel_rank(8, [2, 6])


def test_layout_order_lays_out_the_audio_tracks():
    """The setting reordered is the whole feature: 5.1 first puts the 5.1
    track first, where the default 2.0,5.1 puts stereo there."""
    set_config(AUDIO_LAYOUTS=("5.1", "2.0"))
    plan = plan_for(video(0), audio(1, 8))
    assert [out.channels for out in audio_out(plan)] == [6, 2, 8]


def test_default_layout_order_is_ascending():
    set_config(AUDIO_LAYOUTS=("2.0", "5.1"))
    plan = plan_for(video(0), audio(1, 8))
    assert [out.channels for out in audio_out(plan)] == [2, 6, 8]


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


# Rule modes


def test_a_ride_along_drop_never_orders_the_rebuild_that_follows_it():
    """The cascade the two planning passes exist for: a ride-along languages
    rule dropping the German 5.1 would have the downmix rule order a rewrite
    to rebuild it, a rewrite caused by the one rule that never may."""
    set_langs("eng:keep")
    streams = (
        video(0),
        audio(1, 2, lang="eng"),
        audio(2, 6, lang="ger"),
        audio(3, 8, lang="eng"),
    )
    set_rules(languages="alongside")
    plan = plan_for(*streams)
    assert not plan.needed
    assert plan.rules == set()
    assert any("drop audio 2" in note for note in plan.incidental)

    # The same drop under always: it orders the rewrite, and the rebuild the
    # rewrite makes room for is part of it.
    set_rules(languages="always")
    plan = plan_for(*streams)
    assert plan.needed
    assert plan.rules == {"languages", "downmix"}


def test_remux_riding_along_converts_only_a_file_being_rewritten():
    """The mode the two booleans could not spell: convert to Matroska when
    something else is already paying for the rewrite."""
    set_rules(remux="alongside")
    plan = plan_for(video(0), audio(1, 2), audio(2, 6), path="/x/f.mp4")
    assert not plan.needed
    assert plan.out_path == "/x/f.mp4"
    assert plan.incidental == ["remux to mkv (RULE_REMUX)"]

    # A 5.1 with no stereo beside it, so the downmix rule orders the rewrite.
    plan = plan_for(video(0), audio(1, 6), path="/x/f.mp4")
    assert plan.needed
    assert plan.out_path == "/x/f.mkv"
    assert "remux to mkv (RULE_REMUX)" in plan.incidental


def test_cover_art_riding_along_is_not_worth_a_rewrite_alone():
    """40KB of artwork against a 60GB remux, which is the whole argument for
    the mode existing."""
    set_rules(cover_art="alongside")
    plan = plan_for(video(0), video(1, codec="mjpeg"), audio(2, 2))
    assert not plan.needed
    assert any("cover art" in note for note in plan.incidental)
    assert 1 in {out.src for out in plan.streams}


def test_with_no_rule_riding_along_the_deciding_pass_is_the_whole_answer():
    """Every rule settled one way or the other, which is what a settings page
    somebody has actually been through looks like. Nothing is held back, so
    there is no second pass and nothing incidental to report from it."""
    set_rules(sdh="never", release_tags="never", stray_streams="never")
    plan = plan_for(video(0), audio(1, 6, title="AC3 5.1 @ 640kbps"))

    assert plan.needed
    assert not plan.incidental
    assert not plan.incidental_rules


def test_release_tags_switched_off_leaves_the_title():
    """A ride-along with no switch before this; now it has one."""
    set_rules(release_tags="never")
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="AC3 5.1 @ 640kbps"))
    assert plan.needed
    assert not plan.incidental
    assert not any(out.clear_title for out in plan.streams)


def test_stray_streams_switched_off_carries_them_through():
    """Off means kept, not silently dropped without the note."""
    strays = other_stream(3, "data", "bin_data")
    plan = plan_for(video(0), audio(1, 6), strays)
    assert 3 not in {out.src for out in plan.streams}
    assert any("drop data stream 3" in note for note in plan.incidental)

    set_rules(stray_streams="never")
    plan = plan_for(video(0), audio(1, 6), strays)
    assert 3 in {out.src for out in plan.streams}
    assert not plan.incidental


def test_disabled_languages_keeps_foreign_tracks():
    set_rules(languages="never")
    plan = plan_for(
        video(0), audio(1, 2, lang="eng"), audio(2, 2, lang="ger"), subtitle(3, "ger")
    )
    assert {out.src for out in plan.streams} == {0, 1, 2, 3}
    assert not plan.needed


def test_a_list_that_adds_nothing_generates_nothing():
    """Downmixing off is a list with no added row, since the row is the switch.
    Keep leaves the size in the order and makes nothing."""
    set_layouts("2.0:keep", "5.1:keep")
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    assert not any("downmix" in reason for reason in plan.reasons)
    assert not any(out.encode for out in plan.streams)


def test_disabled_cover_art_keeps_the_poster():
    set_rules(cover_art="never")
    plan = plan_for(video(0), video(1, codec="mjpeg"), audio(2, 2))
    assert not any("cover art" in reason for reason in plan.reasons)
    assert 1 in {out.src for out in plan.streams}


def test_disabled_order_never_reorders():
    set_rules(order="never")
    plan = plan_for(video(0), audio(1, 8), audio(2, 6), audio(3, 2))
    assert not plan.needed


def test_disabled_order_preserves_input_order_when_rewriting():
    set_rules(order="never")
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Commentary"))
    # The downmix still triggers the rewrite, but streams keep the input
    # order and the generated track rides directly after its source.
    assert [out.src for out in plan.streams] == [0, 1, 1, 2]
    assert plan.streams[2].encode is True


def test_drop_commentary_removes_the_track():
    set_rules(commentary="always")
    plan = plan_for(video(0), audio(1, 6), audio(2, 2, title="Director's Commentary"))
    assert 2 not in {out.src for out in plan.streams}
    assert any("drop commentary audio 2" in reason for reason in plan.reasons)
    # The 5.1 still gets its downmix; the commentary never counted anyway.
    assert any(out.encode for out in plan.streams)


def test_drop_commentary_declines_rather_than_silencing():
    set_rules(commentary="always")
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
    """Riding along by default, so a redundant SDH track alone is reported and
    left where it is; a rewrite already under way takes it."""
    subs = (
        subtitle(2, "eng"),
        subtitle(3, "eng", title=title, hearing_impaired=hearing_impaired),
    )
    plan = plan_for(video(0), audio(1, 2), *subs)
    assert not plan.needed
    assert 3 in {out.src for out in plan.streams}
    assert any("SDH subtitle 3" in note for note in plan.incidental)

    # A 5.1 with no stereo beside it, so the downmix rule orders the rewrite.
    plan = plan_for(video(0), audio(1, 6), *subs)
    assert plan.needed
    assert 3 not in {out.src for out in plan.streams}
    assert any("SDH subtitle 3" in note for note in plan.incidental)


def test_sdh_set_to_always_is_worth_a_rewrite_alone():
    set_rules(sdh="always")
    plan = plan_for(
        video(0), audio(1, 2), subtitle(2, "eng"), subtitle(3, "eng", title="English (SDH)")
    )
    assert plan.needed
    assert plan.rules == {"sdh"}
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


def test_sdh_rule_can_be_disabled():
    set_rules(sdh="never")
    plan = plan_for(
        video(0),
        audio(1, 2),
        subtitle(2, "eng"),
        subtitle(3, "eng", title="English (SDH)"),
    )
    assert 3 in {out.src for out in plan.streams}


# Release tags (ride along with a rewrite, never trigger one)


@pytest.mark.parametrize(
    ("title", "tagged"),
    [
        ("AC3 5.1 @ 640kbps", True),
        ("Movie.2024.1080p.WEB-DL.x264-GRP", True),
        # A pure pattern test: load-bearing protection is the planner's job.
        ("Commentary @ 192kbps", True),
        # The default is conservative: bare codec names are not release tags.
        ("DTS-HD MA", False),
        ("AAC 2.0", False),
        ("Director's Commentary", False),
        ("Surround", False),
        ("", False),
    ],
)
def test_matches_release_tags(title, tagged):
    assert matches_release_tags(title, Policy.from_config()) is tagged


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
    assert not any("release tags" in note for note in plan.incidental)
    # The forced track never counts as the full subtitle, so the SDH stays.
    assert {out.src for out in plan.streams} >= {3, 4}


def test_release_tags_alone_do_not_trigger():
    plan = plan_for(video(0), audio(1, 2, title="AAC 2.0 @ 192kbps"))
    assert not plan.needed
    assert any("release tags" in note for note in plan.incidental)


def test_release_tags_cleared_when_rewriting_anyway():
    plan = plan_for(
        video(0),
        audio(1, 6, title="DTS-HD MA 5.1 @ 1509kbps"),
        audio(2, 2, lang="ger"),
        subtitle(3, "eng", title="English [SRT 1080p]"),
    )
    assert plan.needed  # the German track and the missing stereo trigger it
    args = ffmpeg_args(plan, "/tmp/out.mkv")
    # Audio 0 is the generated downmix; the tagged 5.1 copies as audio 1.
    assert args[args.index("-metadata:s:a:1") + 1] == "title="
    assert args[args.index("-metadata:s:s:0") + 1] == "title="


def test_release_tags_on_the_container_title_are_cleared():
    """Named on its own, cleared once a rewrite is happening for another reason.
    The 5.1 with no stereo beside it is that reason."""
    title = "Movie.2024.1080p.BluRay.x264-GRP"
    plan = plan_for(video(0), audio(1, 2), title=title)
    assert not plan.needed
    assert not plan.clear_container_title
    assert any(title in note for note in plan.incidental)

    plan = plan_for(video(0), audio(1, 6), title=title)
    assert plan.clear_container_title
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


def test_hardlinked_file_is_skipped_when_configured(tmp_path):
    """A file the download client still seeds is left alone, before the probe."""
    set_config(SKIP_HARDLINKS=True)
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


def test_each_layout_is_encoded_at_its_own_rate():
    """Nothing is derived from anything else: a downmix is made at the rate its own
    variable states."""
    set_config(AUDIO_LAYOUTS=("2.0", "5.1"))
    set_layouts("2.0:aac:192k", "5.1:ac3:448k")
    args = ffmpeg_args(plan_for(video(0), audio(1, 8)), "/tmp/out.mkv")
    assert args[args.index("-b:a:0") + 1] == "192k"
    assert args[args.index("-b:a:1") + 1] == "448k"
    assert "title=5.1" in args


@pytest.mark.parametrize("rate", ["320K", "320000"])
def test_a_rate_reaches_ffmpeg_in_its_one_spelling(rate):
    """The command and the tag agree whatever the variable said, because a 320K
    left as 320K made the low-bitrate comparison unparseable."""
    set_config(AUDIO_LAYOUTS=("2.0",))
    set_layouts(f"2.0:aac:{rate}")
    args = ffmpeg_args(plan_for(video(0), audio(1, 8)), "/tmp/out.mkv")
    assert args[args.index("-b:a:0") + 1] == "320k"
    assert "TRACKSTARR=aac 320k" in args


def test_a_source_is_picked_in_the_written_language_order():
    """No hardcoded English: the list says which source a downmix comes from,
    and an unlisted language sorts last."""
    ranked = sorted(
        [audio(1, 6, lang="fre"), audio(2, 6, lang="eng"), audio(3, 6, lang="jpn")],
        key=lambda stream: _downmix_rank(stream, ["jpn", "eng"]),
    )
    assert [stream["tags"]["language"] for stream in ranked] == ["jpn", "eng", "fre"]


def test_a_kept_subtitle_title_is_reasserted_in_the_command():
    """MP4 drops track names on a plain copy, so every kept title is written again."""
    args = args_for(
        OutStream(src=0, kind="video"),
        OutStream(src=1, kind="subtitle", title="Forced (English)"),
    )
    assert "title=Forced (English)" in args


def test_a_copied_audio_track_keeps_its_own_title():
    """The ordinary case between a generated downmix and a cleared release tag:
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


def test_a_plan_records_every_input_track():
    """Drops included: the summaries are what is in the file, so the sweep
    cache can serve as a library index, not what survives the rules."""
    plan = plan_for(video(), audio(1, 6), audio(2, 2, lang="kor"), subtitle(3))
    assert [track["index"] for track in plan.tracks] == [0, 1, 2, 3]
    assert plan.tracks[2]["lang"] == "kor"


def test_an_untagged_stale_downmix_rebuilds_without_claiming_a_language():
    """A generated track whose language tag has since been stripped still
    regenerates, but names no language for the rebuild to come back in: an
    untagged source would otherwise reserve a layout for the empty string."""
    set_rules(regenerate="always")
    plan = plan_for(video(0), tagged_downmix(1, 2, "aac 192k", lang=None), audio(2, 6))
    assert any(reason.startswith("regenerate 2.0 downmix") for reason in plan.reasons)
    assert 1 not in {out.src for out in plan.streams}
    assert [out.channels for out in audio_out(plan) if out.encode] == [2]


def planned_for(*streams, original="eng"):
    """The plan's output summaries, which is what the library view reads."""
    return planned_tracks(plan_for(*streams, original=original))


def test_planned_tracks_carry_a_generated_downmix_as_the_track_it_will_be():
    """Not as the track it came from: the whole point of the after-state is
    that a 5.1 source becomes a 2.0 AAC at the layout's own rate."""
    planned = planned_for(video(), audio(1, 8))
    generated = [track for track in planned if "generated" in track.get("flags", [])]
    assert [(track["channels"], track["codec"], track["src"]) for track in generated] == [
        (2, "aac", 1),
        (6, "ac3", 1),
    ]
    assert generated[0]["bitrate"] == 320_000


def test_a_dropped_track_is_absent_from_the_planned_side():
    """Which is how the view marks it: nothing in the after list names it as
    a source, so the pairing shows the row struck out."""
    plan = plan_for(video(), audio(1, 6), audio(2, 2, lang="dan"))
    assert 2 not in {track["src"] for track in planned_tracks(plan)}
    assert 2 in {track["index"] for track in plan.tracks}


def test_a_copied_track_keeps_its_source_and_loses_a_cleared_title():
    """The generated downmix names the same source, so the copy is the one
    of the pair that is not flagged generated."""
    plan = plan_for(video(), audio(1, 6, title="AC3 5.1 @ 640kbps"))
    copied = next(
        track
        for track in planned_tracks(plan)
        if track["src"] == 1 and "generated" not in track.get("flags", [])
    )
    assert copied["codec"] == plan.tracks[1]["codec"]
    assert "title" not in copied


def test_planned_tracks_are_positions_in_the_output():
    """Not input indices: two of them can share one source, and the order is
    the rewrite's rather than the file's."""
    planned = planned_for(video(), audio(1, 8))
    assert [track["index"] for track in planned] == list(range(len(planned)))


def test_track_changes_names_the_dropped_source_and_the_generated_position():
    """One index space each, matching planned_tracks: `dropped` is read against
    the file the rewrite started from and `added` against the one it left."""
    plan = plan_for(video(), audio(1, 8), audio(2, 2, lang="dan"))
    told = track_changes(plan)
    assert told["dropped"] == [2]
    assert told["added"] == [
        position
        for position, track in enumerate(planned_tracks(plan))
        if "generated" in track.get("flags", [])
    ]


def test_track_changes_says_nothing_where_no_track_moved():
    """A remux carries every stream over, so both lists are empty and both are
    dropped: the view has nothing to compare and shows the one column."""
    set_rules(remux="always")
    plan = plan_for(video(), audio(1, 2), subtitle(2), path="/x/f.mp4")
    assert plan.needed
    assert track_changes(plan) == {}


def test_why_names_the_changes_and_the_rules_behind_them():
    told = why(plan_for(video(), audio(1, 6), audio(2, 2, lang="dan")))
    assert told["rules"] == ["downmix", "languages"]
    assert any(reason.startswith("drop audio 2") for reason in told["reasons"])
    assert "skip" not in told


def test_why_leads_with_the_skip_when_there_is_one():
    """A skipped file's reasons are what the rules wanted, not what happens,
    so the view has to be able to say the skip first."""
    told = why(plan_for(video(), audio(1, 2, lang="dan")))
    assert told["skip"] == "would remove every audio track"


def test_a_conforming_file_has_nothing_to_say():
    assert why(plan_for(video(), audio(1, 2), audio(2, 6))) == {}
