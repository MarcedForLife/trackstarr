"""Prepared imports go through the same staged publication as local rewrites."""

import copy
import os
import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from conftest import set_config, set_layouts
from trackstarr import executor
from trackstarr.command import ffmpeg_args
from trackstarr.executor import Outcome, apply_plan
from trackstarr.media import GENERATED_TAG, probe, stream_title
from trackstarr.planner import (
    FileRevision,
    OutStream,
    Plan,
    PlanInput,
    StreamRef,
    TimingMapping,
)
from trackstarr.policy import Policy


@pytest.fixture(autouse=True)
def settings(tmp_path):
    set_config(WORK_DIR=str(tmp_path / "work"))
    set_layouts("2.0:flac:192k")
    yield
    assert not executor._source_reads
    assert not executor._preparing
    assert not list((tmp_path / "work").glob("*"))


def make_media(path, language, seconds, frequency, chapters=None):
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=32x32:rate=10:duration={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
    ]
    if chapters:
        args += ["-i", str(chapters), "-map_metadata", "2", "-map_chapters", "2"]
    args += [
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "ffv1",
        "-c:a",
        "flac",
        "-ac",
        "2",
        "-metadata:s:a:0",
        f"language={language}",
        "-metadata:s:a:0",
        f"title={language} dialogue",
        "-disposition:a:0",
        "default" if chapters else "default+comment",
        str(path),
    ]
    subprocess.run(args, check=True)


@pytest.fixture
def media(tmp_path):
    metadata = tmp_path / "chapters.txt"
    metadata.write_text(
        ";FFMETADATA1\ntitle=Target title\nCOMMENT=Target comment\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=6000\ntitle=Opening\n"
    )
    target = tmp_path / "target.mkv"
    source = tmp_path / "source.mkv"
    make_media(target, "eng", 6, 440, metadata)
    make_media(source, "fra", 4, 880)
    info = probe(str(target))
    plan = Plan(
        str(target),
        policy=Policy.from_config(),
        inputs=(PlanInput(FileRevision.of(str(source)), 1),),
        streams=[
            OutStream(0, "video"),
            OutStream(1, "audio", title="eng dialogue"),
            OutStream(source=StreamRef(1, 1), kind="audio"),
        ],
        src_duration=6,
        src_chapter_count=1,
    )
    return plan, info


def decoded(path, index):
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            f"0:{index}",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout


@pytest.mark.parametrize(
    "scale,offset",
    [
        (Fraction(1), 0),
        (Fraction(1), 250_000),
        (Fraction(1), -250_000),
        (Fraction(1001, 960), 200_000),
        (Fraction(960, 1001), -250_000),
    ],
)
def test_generated_import_preserves_target_and_source(media, scale, offset):
    plan, before = media
    item = replace(plan.inputs[0], mapping=TimingMapping(scale, offset))
    plan.inputs = (item,)
    original = Path(item.revision.path).read_bytes()
    video = decoded(plan.path, 0)
    audio = decoded(plan.path, 1)
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.APPLIED, detail
    after = probe(plan.path)
    assert decoded(plan.path, 0) == video
    assert decoded(plan.path, 1) == audio
    assert after["chapters"] == before["chapters"]
    imported = after["streams"][2]
    assert imported["codec_name"] == "flac"
    assert imported["tags"]["language"] == "fra"
    assert imported["tags"]["title"] == "fra dialogue"
    assert GENERATED_TAG not in imported["tags"]
    assert imported["disposition"]["default"] == 0
    assert imported["disposition"]["comment"] == 1
    assert after["streams"][1]["disposition"]["default"] == 1
    assert float(imported["start_time"]) == pytest.approx(max(0, offset / 1e6), abs=0.002)
    assert executor._stream_end(imported) == pytest.approx(
        float(scale) * 4 + offset / 1e6, abs=0.05
    )
    if scale == 1 and offset >= 0:
        assert decoded(plan.path, 2) == decoded(item.revision.path, 1)
    assert Path(item.revision.path).read_bytes() == original
    assert plan.streams[-1].codec == ""
    assert plan.streams[-1].source == StreamRef(1, 1)


def test_duplicate_stream_indexes_from_two_files_keep_their_identity(media, tmp_path):
    plan, _ = media
    second = tmp_path / "second.mkv"
    make_media(second, "deu", 4, 1200)
    plan.inputs += (PlanInput(FileRevision.of(str(second)), 1),)
    plan.streams.append(OutStream(source=StreamRef(2, 1), kind="audio"))
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.APPLIED, detail
    result = probe(plan.path)
    assert [s.get("tags", {}).get("language") for s in result["streams"][1:]] == [
        "eng",
        "fra",
        "deu",
    ]
    for index, item in enumerate(plan.inputs, 2):
        assert decoded(plan.path, index) == decoded(item.revision.path, 1)


@pytest.mark.parametrize("change", ["source", "target", "cancel", "claim"])
def test_late_change_prevents_publication(media, change):
    plan, _ = media
    before = Path(plan.path).read_bytes()
    cancel = executor.Cancel(plan.path)

    def claim():
        if change in {"source", "target"}:
            path = Path(plan.path if change == "target" else plan.inputs[0].revision.path)
            st = path.stat()
            # Keep the weak signature unchanged while changing filesystem identity.
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            os.utime(replacement, ns=(st.st_atime_ns, st.st_mtime_ns))
            replacement.replace(path)
        if change == "cancel":
            cancel.ask()
        return change != "claim"

    outcome, _ = apply_plan(plan, claim=claim, cancel=cancel)
    assert outcome == Outcome.DEFERRED
    assert Path(plan.path).read_bytes() == before


def test_renderer_requires_preparation_for_selected_inputs(media):
    plan, _ = media
    with pytest.raises(ValueError, match="preparation"):
        ffmpeg_args(plan, "out.mkv")


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("kind", "external inputs"),
        ("index", "selected input"),
        ("unused", "unused source"),
        ("alias", "different file"),
        ("missing", "source is unavailable"),
    ],
)
def test_invalid_inputs_leave_target_untouched(media, tmp_path, problem, expected):
    plan, _ = media
    before = Path(plan.path).read_bytes()
    if problem == "kind":
        plan.streams[-1].kind = "video"
    elif problem == "index":
        plan.streams[-1].source = StreamRef(1, 9)
    elif problem == "unused":
        plan.streams.pop()
    elif problem == "alias":
        alias = tmp_path / "alias.mkv"
        os.link(plan.path, alias)
        plan.inputs = (PlanInput(FileRevision.of(str(alias)), 1),)
    else:
        Path(plan.inputs[0].revision.path).unlink()
    outcome, detail = apply_plan(plan)
    assert outcome == (Outcome.DEFERRED if problem == "missing" else Outcome.FAILED)
    assert expected in detail
    assert Path(plan.path).read_bytes() == before


@pytest.fixture
def verification(media, monkeypatch):
    plan, target = media
    original = probe(plan.inputs[0].revision.path)["streams"][1]
    prepared = executor.PreparedTrack("prepared.mka", plan.inputs[0], False)
    result = copy.deepcopy(target)
    stream = copy.deepcopy(original)
    stream["index"] = 2
    stream["disposition"]["default"] = 0
    result["streams"].append(stream)
    monkeypatch.setattr(executor, "probe", lambda _: {"streams": [original]})
    monkeypatch.setattr(executor, "_run_ffmpeg", lambda *a, **kw: (0, "nb_samples:1024"))
    return plan, target, (prepared,), result


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("chapter_count", "chapter count"),
        ("chapter_time", "chapter content"),
        ("chapter_title", "chapter content"),
        ("metadata", "target metadata"),
        ("kind", "stream kind"),
        ("codec", "copied stream codec"),
        ("video", "video properties"),
        ("hdr", "HDR metadata"),
        ("channels", "channel count"),
        ("layout", "channel layout"),
        ("language", "language or title"),
        ("title", "language or title"),
        ("flags", "disposition"),
        ("start", "start time"),
        ("empty", "end time"),
        ("end", "end time"),
    ],
)
def test_verification_rejects_damaged_or_misidentified_output(verification, problem, expected):
    plan, target, prepared, result = verification
    imported = result["streams"][-1]
    if problem == "chapter_count":
        result["chapters"] = []
    elif problem == "chapter_time":
        result["chapters"][0]["end_time"] = "5.9"
    elif problem == "chapter_title":
        result["chapters"][0]["tags"]["title"] = "Source chapter"
    elif problem == "metadata":
        result["format"]["tags"]["title"] = "Source title"
    elif problem == "kind":
        imported["codec_type"] = "video"
    elif problem == "codec":
        imported["codec_name"] = "aac"
    elif problem == "video":
        result["streams"][0]["width"] = 100
    elif problem == "hdr":
        result["streams"][0]["side_data_list"] = [
            {"side_data_type": "Mastering display metadata", "red_x": "123"}
        ]
    elif problem == "channels":
        imported["channels"] = 1
    elif problem == "layout":
        imported["channel_layout"] = "mono"
    elif problem == "language":
        imported["tags"]["language"] = "eng"
    elif problem == "title":
        imported["tags"]["title"] = "Wrong track"
    elif problem == "flags":
        imported["disposition"]["default"] = 1
    elif problem == "start":
        imported["start_time"] = "0.5"
    elif problem == "empty":
        imported["tags"]["DURATION"] = "00:00:00.000"
    else:
        imported["tags"]["DURATION"] = "00:00:03.000"
    assert expected in executor._verify_sourced(
        plan, target, prepared, result, "out.mkv", executor.Cancel()
    )


@pytest.mark.parametrize(
    "code,stderr,stop",
    [(1, "corrupt", False), (0, "", False), (-15, "", False), (0, "nb_samples:1", True)],
)
def test_import_decode_errors_and_cancellation(verification, monkeypatch, code, stderr, stop):
    plan, target, prepared, result = verification
    cancel = executor.Cancel()

    def run(*args, **kwargs):
        if stop:
            cancel.ask()
        return code, stderr

    monkeypatch.setattr(executor, "_run_ffmpeg", run)
    if stop or code < 0:
        with pytest.raises(InterruptedError, match="verification was stopped"):
            executor._verify_sourced(plan, target, prepared, result, "out.mkv", cancel)
    else:
        assert "could not be decoded" in executor._verify_sourced(
            plan, target, prepared, result, "out.mkv", cancel
        )


def test_missing_track_duration_refuses_verification():
    with pytest.raises(ValueError, match="end time"):
        executor._stream_end({"tags": {"title": "no duration"}})
    assert executor._stream_end({"start_time": "1", "duration": "3"}) == 4


@pytest.mark.parametrize(
    "layout,channels,expected",
    [(None, 2, "stereo"), (None, 6, None), ("5.1(side)", 6, "5.1(side)")],
)
def test_retiming_requires_explicit_multichannel_layout(
    media, monkeypatch, layout, channels, expected
):
    plan, _ = media
    track = executor.PreparedTrack("prepared.mka", plan.inputs[0], True)
    monkeypatch.setattr(
        executor,
        "probe",
        lambda _: {"streams": [{"index": 1, "channels": channels, "channel_layout": layout}]},
    )
    if expected:
        assert executor._source_layout(track) == expected
    else:
        with pytest.raises(ValueError, match="known source channel layout"):
            executor._source_layout(track)


def test_render_budget_failure_cleans_prepared_audio(media, monkeypatch):
    plan, _ = media
    before = Path(plan.path).read_bytes()
    actual = executor.shutil.disk_usage
    calls = 0

    def space(path):
        nonlocal calls
        calls += 1
        return actual(path) if calls == 1 else actual(path)._replace(free=0)

    monkeypatch.setattr(executor.shutil, "disk_usage", space)
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.FAILED
    assert "space for sourced rendering" in detail
    assert Path(plan.path).read_bytes() == before


@pytest.mark.parametrize("codec,channels,extension", [("flac", 2, ".mp4"), ("ac3", 8, ".mkv")])
def test_encoder_must_support_container_and_channel_count(
    media, monkeypatch, codec, channels, extension
):
    plan, _ = media
    plan.path = str(Path(plan.path).with_suffix(extension))
    plan.streams[-1] = replace(plan.streams[-1], encode=True, codec=codec, channels=channels)
    track = executor.PreparedTrack("prepared.mka", plan.inputs[0], False)
    monkeypatch.setattr(executor, "probe", lambda _: {"streams": [{}]})
    with pytest.raises(ValueError, match="cannot preserve"):
        executor._render_plan(plan, (track,))


def test_custom_encoder_is_left_to_ffmpeg(media, monkeypatch):
    plan, _ = media
    plan.streams[-1].encode = True
    plan.streams[-1].codec = "custom_encoder"
    track = executor.PreparedTrack("prepared.mka", plan.inputs[0], False)
    monkeypatch.setattr(executor, "probe", lambda _: {"streams": [{}]})
    assert executor._render_plan(plan, (track,)).streams[-1].codec == "custom_encoder"


@pytest.mark.parametrize("retimed", [False, True])
def test_import_can_also_supply_an_explicit_generated_layout(media, retimed):
    plan, _ = media
    if retimed:
        plan.inputs = (replace(plan.inputs[0], mapping=TimingMapping(offset_us=-250_000)),)
    plan.streams[-1] = replace(
        plan.streams[-1],
        encode=True,
        channels=1,
        codec="aac",
        bitrate="96k",
        lang="fra",
        title="French mono",
    )
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.APPLIED, detail
    result = probe(plan.path)["streams"][-1]
    assert result["channels"] == 1
    assert result["codec_name"] == "aac"
    assert GENERATED_TAG in result["tags"]


def test_explicit_title_removal_and_language_override(verification):
    plan, target, prepared, result = verification
    plan.clear_container_title = True
    result["format"]["tags"].pop("title")
    plan.streams[-1].clear_title = True
    plan.streams[-1].lang = "deu"
    result["streams"][-1]["tags"].pop("title")
    result["streams"][-1]["tags"]["language"] = "deu"
    assert (
        executor._verify_sourced(plan, target, prepared, result, "out.mkv", executor.Cancel())
        is None
    )


def test_local_rewrite_keeps_existing_oserror_contract(tmp_path, monkeypatch):
    path = tmp_path / "local.mkv"
    path.write_bytes(b"unchanged")
    monkeypatch.setattr(
        executor,
        "_run_ffmpeg",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("ffmpeg unavailable")),
    )
    with pytest.raises(OSError, match="unavailable"):
        apply_plan(Plan(str(path)))


@pytest.mark.parametrize("retimed", [False, True])
def test_mp4_import_preserves_titles_and_timestamps(media, tmp_path, retimed):
    plan, _ = media
    target = tmp_path / "target.mp4"
    source = tmp_path / "source-aac.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            plan.path,
            "-map",
            "0",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "title=eng dialogue",
            str(target),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            plan.inputs[0].revision.path,
            "-map",
            "0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-disposition:a:0",
            "default",
            str(source),
        ],
        check=True,
    )
    set_layouts("2.0:aac:192k")
    plan.policy = Policy.from_config()
    plan.path = str(target)
    plan.inputs = (
        PlanInput(
            FileRevision.of(str(source)),
            1,
            TimingMapping(offset_us=-250_000 if retimed else 250_000),
        ),
    )
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.APPLIED, detail
    result = probe(plan.path)["streams"][2]
    assert result["codec_name"] == "aac"
    assert stream_title(result) == "fra dialogue"


@pytest.mark.parametrize("offset", [-100_000, 100_000])
def test_timing_is_checked_against_mapping_not_only_prepared_file(verification, offset):
    plan, target, prepared, result = verification
    prepared = (
        replace(
            prepared[0],
            source=replace(prepared[0].source, mapping=TimingMapping(offset_us=offset)),
        ),
    )
    assert "selected mapping" in executor._verify_sourced(
        plan, target, prepared, result, "out.mkv", executor.Cancel()
    )


@pytest.mark.parametrize("failure", ["verification", "decode", "cancel"])
def test_failed_final_verification_never_publishes(media, monkeypatch, failure):
    plan, _ = media
    original = Path(plan.path).read_bytes()
    real_probe = executor.probe
    real_run = executor._run_ffmpeg
    cancel = executor.Cancel(plan.path)

    def inspect(path):
        result = real_probe(path)
        if str(path).endswith(".partial") and failure == "verification":
            result["streams"][-1]["channels"] = 1
        return result

    def run(args, *a, **kw):
        if "ashowinfo" in args and failure in {"decode", "cancel"}:
            if failure == "cancel":
                cancel.ask()
            return 1, "corrupt sample"
        return real_run(args, *a, **kw)

    monkeypatch.setattr(executor, "probe", inspect)
    monkeypatch.setattr(executor, "_run_ffmpeg", run)
    outcome, _ = apply_plan(plan, cancel=cancel)
    assert outcome == (Outcome.DEFERRED if failure == "cancel" else Outcome.FAILED)
    assert Path(plan.path).read_bytes() == original


def test_import_does_not_select_a_default_when_target_has_none(media, tmp_path):
    plan, _ = media
    replacement = tmp_path / "no-default.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            plan.path,
            "-map",
            "0",
            "-c",
            "copy",
            "-disposition:a:0",
            "0",
            str(replacement),
        ],
        check=True,
    )
    replacement.replace(plan.path)
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.APPLIED, detail
    assert all(
        not s["disposition"]["default"]
        for s in probe(plan.path)["streams"]
        if s["codec_type"] == "audio"
    )


def test_surround_retiming_preserves_speaker_layout(media, tmp_path):
    plan, _ = media
    source = tmp_path / "surround.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            plan.inputs[0].revision.path,
            "-map",
            "0",
            "-c:v",
            "copy",
            "-c:a",
            "flac",
            "-ac",
            "6",
            str(source),
        ],
        check=True,
    )
    set_layouts("5.1:flac:640k")
    plan.policy = Policy.from_config()
    plan.inputs = (
        PlanInput(FileRevision.of(str(source)), 1, TimingMapping(offset_us=-250_000)),
    )
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.APPLIED, detail
    expected = probe(str(source))["streams"][1]
    actual = probe(plan.path)["streams"][2]
    assert actual["channels"] == 6
    assert actual["channel_layout"] == expected["channel_layout"]
