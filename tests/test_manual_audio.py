"""Manual CLI sourcing uses ordinary planning and staged publication."""

import argparse
import os
import subprocess
from fractions import Fraction

import pytest

from conftest import set_config, set_langs, set_layouts, set_rules
from test_sourced_rendering import decoded, make_media
from trackstarr import cli, executor, pauses, processing
from trackstarr.cli import _manual_inputs, _seconds, main
from trackstarr.media import probe, stream_lang
from trackstarr.planner import (
    FileRevision,
    PlanInput,
    TimingMapping,
    build_plan,
    planned_tracks,
)
from trackstarr.policy import Policy
from trackstarr.processing import Job, _build_plan, process
from trackstarr.status import Status


@pytest.fixture
def media(tmp_path):
    set_config(
        WORK_DIR=str(tmp_path / "work"),
        REWRITE_MODE="imports",
        ALLOWED_EXTS=frozenset({".mkv"}),
    )
    set_rules(**dict.fromkeys(dict(Policy.from_config().rule_modes), "never"))
    set_layouts("2.0:flac:192k")
    set_langs("eng", "fra")
    target, source = tmp_path / "target.mkv", tmp_path / "source.mkv"
    make_media(target, "eng", 6, 440)
    make_media(source, "fra", 4, 880)
    yield target, source
    assert not executor._source_reads
    assert not executor._preparing
    assert not list((tmp_path / "work").glob("*"))


def selection(source, mapping=None, index=1):
    return (PlanInput(FileRevision.of(str(source)), index, mapping or TimingMapping()),)


@pytest.mark.parametrize(
    "timing",
    [[], ["--offset", "0.25"], ["--offset=-0.25"], ["--anchor", "0=0", "--anchor", "3=3.125"]],
)
def test_cli_preview_then_real_copy(media, capsys, timing):
    target, source = media
    before = target.read_bytes()
    source_before = source.read_bytes()
    video_hash, audio_hash = decoded(target, 0), decoded(target, 1)
    args = [
        str(target),
        "--original",
        "eng",
        "--source-file",
        str(source),
        "--source-stream",
        "1",
        *timing,
    ]
    assert main(["plan", *args]) == 0
    preview = capsys.readouterr().out
    assert "import " in preview and "stream 1" in preview
    assert "scale " in preview
    assert "ffmpeg " not in preview
    assert target.read_bytes() == before
    assert main(["fix", *args]) == 0
    assert "modified" in capsys.readouterr().out
    result = probe(str(target))["streams"]
    assert [stream_lang(s) for s in result if s["codec_type"] == "audio"] == ["eng", "fre"]
    assert decoded(target, 0) == video_hash
    assert decoded(target, 1) == audio_hash
    assert source.read_bytes() == source_before
    if not timing or timing == ["--offset", "0.25"]:
        assert decoded(target, 2) == decoded(source, 1)
    assert not build_plan(str(target), "eng").needed


@pytest.mark.parametrize(
    "value, expected",
    [
        ("-0.250", Fraction(-1, 4)),
        ("01:02:03.125", Fraction(29785, 8)),
        ("1001/960", Fraction(1001, 960)),
    ],
)
def test_exact_timestamp_parsing(value, expected):
    assert _seconds(value) == expected


@pytest.mark.parametrize(
    "value",
    ["1:2", "-1:02:03", "0:60:00", "0:00:60", "0.5:00:00", "0:1.5:00", "nan", "inf", ""],
)
def test_bad_timestamps(value):
    with pytest.raises(ValueError):
        _seconds(value)


@pytest.mark.parametrize(
    "flags, message",
    [
        (["--offset", "1"], "require --source-file"),
        (["--source-stream", "1"], "require --source-file"),
        (["--anchor", "0=0"], "require --source-file"),
        (["--source-file", "SOURCE"], "at least one"),
        (["--source-file", "SOURCE", "--source-stream", "-1"], "non-negative"),
        (
            ["--source-file", "SOURCE", "--source-stream", "1", "--source-stream", "1"],
            "more than once",
        ),
        (["--source-file", "SOURCE", "--source-stream", "1", "--anchor", "0=0"], "exactly two"),
        (
            [
                "--source-file",
                "SOURCE",
                "--source-stream",
                "1",
                "--anchor",
                "0",
                "--anchor",
                "1=1",
            ],
            "SOURCE=TARGET",
        ),
        (
            [
                "--source-file",
                "SOURCE",
                "--source-stream",
                "1",
                "--anchor=-1=0",
                "--anchor",
                "1=1",
            ],
            "non-negative",
        ),
        (
            [
                "--source-file",
                "SOURCE",
                "--source-stream",
                "1",
                "--anchor",
                "0=0",
                "--anchor",
                "0=1",
            ],
            "increase",
        ),
        (
            [
                "--source-file",
                "SOURCE",
                "--source-stream",
                "1",
                "--anchor",
                "0=1",
                "--anchor",
                "1=0",
            ],
            "increase",
        ),
        (
            [
                "--source-file",
                "SOURCE",
                "--source-stream",
                "1",
                "--anchor",
                "0=0",
                "--anchor",
                "1=3",
            ],
            "scales",
        ),
        (["--source-file", "SOURCE", "--source-stream", "1", "--offset", "1/0"], "Fraction"),
    ],
)
def test_invalid_cli_selection(media, flags, message, caplog):
    target, source = media
    before = target.read_bytes()
    flags = [str(source) if value == "SOURCE" else value for value in flags]
    assert main(["plan", str(target), "--original", "eng", *flags]) == 1
    assert message in caplog.text
    assert target.read_bytes() == before


def test_multiple_targets_are_refused(media, caplog):
    target, source = media
    assert (
        main(
            [
                "plan",
                str(target),
                str(target),
                "--source-file",
                str(source),
                "--source-stream",
                "1",
            ]
        )
        == 1
    )
    assert "exactly one target" in caplog.text


def test_anchor_mapping_is_exact(media):
    target, source = media
    args = argparse.Namespace(
        files=[str(target)],
        source_file=str(source),
        source_stream=[1],
        offset=None,
        anchor=["1=1.25", "961=1002.25"],
    )
    mapping = _manual_inputs(args)[0].mapping
    assert mapping.scale == Fraction(1001, 960)
    assert mapping.offset_us == round((Fraction(5, 4) - Fraction(1001, 960)) * 1_000_000)


@pytest.mark.parametrize("index", [0, 99])
def test_non_audio_selection_refused(media, index):
    target, source = media
    result = process(Job(str(target), "eng"), False, inputs=selection(source, index=index))
    assert result.status is Status.FAILED
    assert "not audio" in result.detail


def test_duplicate_input_refused(media):
    target, source = media
    inputs = selection(source) * 2
    result = process(Job(str(target), "eng"), False, inputs=inputs)
    assert "more than once" in result.detail


def test_same_file_alias_refused(media, tmp_path):
    target, _ = media
    alias = tmp_path / "alias.mkv"
    os.link(target, alias)
    set_config(SKIP_HARDLINKS=False)
    result = process(Job(str(target), "eng"), False, inputs=selection(alias))
    assert result.status is Status.FAILED
    assert "different files" in result.detail


def test_stale_source_defers(media):
    target, source = media
    inputs = selection(source)
    source.touch()
    result = process(Job(str(target), "eng"), False, inputs=inputs)
    assert result.status is Status.DEFERRED


def test_rules_cannot_remove_selected_audio(media):
    target, source = media
    set_langs("eng")
    set_rules(languages="always")
    result = process(Job(str(target), "eng"), False, inputs=selection(source))
    assert result.status is Status.FAILED
    assert "current rules" in result.detail


def test_alongside_rules_and_import_projection(media):
    target, source = media
    set_rules(release_tags="alongside")
    plan = _build_plan(Job(str(target), "eng"), Policy.from_config(), selection(source))
    assert plan.acts("release_tags")
    assert len(plan.tracks) == 2
    imported = planned_tracks(plan)[2]
    assert imported["source"] == {"input_index": 1, "stream_index": 1}
    assert imported["codec"] == "flac"
    assert imported["channels"] == 2
    assert imported["lang"] == "fre"
    assert imported["title"] == "fra dialogue"


def test_report_does_not_cache_manual_pending_plan(media, monkeypatch):
    target, source = media
    set_config(REWRITE_MODE="report")
    monkeypatch.setattr(
        cli, "remember", lambda *args: pytest.fail("manual pending selection cached")
    )
    before = target.read_bytes()
    assert (
        main(
            [
                "fix",
                str(target),
                "--original",
                "eng",
                "--source-file",
                str(source),
                "--source-stream",
                "1",
            ]
        )
        == 0
    )
    assert target.read_bytes() == before


@pytest.mark.parametrize("change", ["policy", "pause", "target", "missing", "source"])
def test_changes_before_publication_discard_output(media, monkeypatch, change):
    target, source = media
    before = target.read_bytes()
    apply = processing.apply_plan

    def racing(plan, progress, encoded, cancel, claim):
        def changed():
            if change == "policy":
                set_langs("eng")
            elif change == "pause":
                pauses.place(str(target))
            elif change == "target":
                replacement = target.with_suffix(".replacement")
                replacement.write_bytes(before)
                os.replace(replacement, target)
            elif change == "missing":
                target.unlink()
            else:
                source.touch()
            return claim()

        return apply(plan, progress, encoded, cancel, changed)

    monkeypatch.setattr(processing, "apply_plan", racing)
    result = process(Job(str(target), "eng"), False, inputs=selection(source))
    assert result.status is Status.DEFERRED, result.detail
    if change != "missing":
        assert target.read_bytes() == before


@pytest.mark.parametrize(
    "paused_file, expected", [("source", Status.MODIFIED), ("target", Status.PENDING)]
)
def test_pause_applies_only_to_target(media, paused_file, expected):
    target, source = media
    pauses.place(str(source if paused_file == "source" else target))
    result = process(Job(str(target), "eng"), False, inputs=selection(source))
    assert result.status is expected, result.detail


def test_source_audio_can_supply_downmix(media):
    target, source = media
    surround = source.with_name("surround.mkv")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0",
            "-c:v",
            "copy",
            "-c:a",
            "flac",
            "-ac",
            "6",
            "-disposition:a:0",
            "default",
            str(surround),
        ],
        check=True,
    )
    set_rules(downmix="always")
    result = process(Job(str(target), "eng"), False, inputs=selection(surround))
    assert result.status is Status.MODIFIED, result.detail
    audio = [s for s in probe(str(target))["streams"] if s["codec_type"] == "audio"]
    assert sorted((stream_lang(s), s["channels"]) for s in audio) == [
        ("eng", 2),
        ("fre", 2),
        ("fre", 6),
    ]
    assert not build_plan(str(target), "eng").needed


def test_multiple_source_tracks_keep_original_coordinates(media):
    target, source = media
    multiple = source.with_name("multiple.mkv")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v",
            "-map",
            "0:a",
            "-map",
            "0:a",
            "-c",
            "copy",
            "-metadata:s:a:1",
            "language=deu",
            str(multiple),
        ],
        check=True,
    )
    result = process(
        Job(str(target), "eng"),
        False,
        inputs=selection(multiple) + selection(multiple, index=2),
    )
    assert result.status is Status.MODIFIED, result.detail
    assert [s.source.stream_index for s in result.plan.streams if s.source.input_index] == [
        1,
        2,
    ]
    assert [stream_lang(s) for s in probe(str(target))["streams"][1:]] == ["eng", "fre", "ger"]


def test_source_probes_are_guarded(media, monkeypatch):
    target, source = media
    seen = set()
    original = processing.probe

    def guarded(path):
        assert executor.is_rewriting(path)
        seen.add(path)
        return original(path)

    monkeypatch.setattr(processing, "probe", guarded)
    _build_plan(Job(str(target), "eng"), Policy.from_config(), selection(source))
    assert seen == {str(target), str(source)}


@pytest.mark.parametrize(
    "change, message",
    [
        ({"start_time": "0", "duration": "0.1"}, "no source audio"),
        ({"codec_name": "truehd"}, "immersive"),
        ({"channels": 6, "channel_layout": ""}, "known source channel layout"),
        ({"channels": 6, "channel_layout": "5.1"}, "AUDIO_LAYOUTS encoder"),
    ],
)
def test_preview_rejects_unsupported_retiming(media, change, message):
    target, source = media
    if message == "known source channel layout":
        set_layouts("2.0:flac:192k", "5.1:flac:640k")
    plan = build_plan(str(target), "eng")
    stream = probe(str(source))["streams"][1] | {"duration": "4"} | change
    item = selection(source, TimingMapping(offset_us=-250000))[0]
    with pytest.raises(ValueError, match=message):
        processing._source_description(item, stream, plan)


def test_preview_rejects_unsupported_scale(media):
    target, source = media
    with pytest.raises(ValueError, match="scales"):
        processing._source_description(
            selection(source, TimingMapping(Fraction(3)))[0],
            probe(str(source))["streams"][1],
            build_plan(str(target), "eng"),
        )


def test_preview_rejects_encoder_container_mismatch(media):
    target, source = media
    plan = build_plan(str(target), "eng")
    plan.path = "target.mp4"
    with pytest.raises(ValueError, match="target container"):
        processing._source_description(
            selection(source, TimingMapping(offset_us=-250000))[0],
            probe(str(source))["streams"][1],
            plan,
        )


def test_protected_target_stays_skipped(media):
    target, source = media
    os.link(target, target.with_name("hardlink.mkv"))
    plan = _build_plan(Job(str(target), "eng"), Policy.from_config(), selection(source))
    assert "hardlinked" in plan.skip


def test_failed_manual_plan_reports_error(media, capsys):
    target, source = media
    assert (
        main(
            [
                "plan",
                str(target),
                "--original",
                "eng",
                "--source-file",
                str(source),
                "--source-stream",
                "0",
            ]
        )
        == 1
    )
    assert "not audio" in capsys.readouterr().out


def test_combined_plan_cannot_remove_every_audio_track(media):
    target, source = media
    set_langs("ger")
    set_rules(languages="always")
    result = process(Job(str(target), "eng"), False, inputs=selection(source))
    assert result.status is Status.FAILED
    assert "every audio track" in result.detail


def test_dolby_vision_trial_facts_reach_combined_plan(media, monkeypatch):
    target, source = media
    set_rules(dv_strip="always")
    trials = []
    monkeypatch.setattr(processing, "_trial_dv_strip", lambda path, info: trials.append(path))
    plan = _build_plan(Job(str(target), "eng"), Policy.from_config(), selection(source))
    assert trials == [str(target)]
    assert plan.needed


def test_source_changed_during_later_probe_is_refused(media, monkeypatch):
    target, source = media
    other = source.with_name("other.mkv")
    other.write_bytes(source.read_bytes())
    inputs = selection(source) + selection(other)
    original = processing.probe

    def racing(path):
        if path == str(other):
            source.touch()
        return original(path)

    monkeypatch.setattr(processing, "probe", racing)
    result = process(Job(str(target), "eng"), True, inputs=inputs)
    assert result.status is Status.DEFERRED
    assert "source changed" in result.detail


def test_retimed_preview_names_final_encoder_without_generated_marker(media):
    target, source = media
    set_layouts("2.0:aac:192k")
    plan = _build_plan(
        Job(str(target), "eng"),
        Policy.from_config(),
        selection(source, TimingMapping(offset_us=-250000)),
    )
    imported = planned_tracks(plan)[2]
    assert imported["codec"] == "aac"
    assert "generated" not in imported.get("flags", [])
    assert "encode aac 192k" in plan.reasons[-1]
