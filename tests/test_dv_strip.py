"""Dolby Vision eligibility, planning and staged verification failures."""

import copy
import json
import subprocess

import pytest

from conftest import (
    audio,
    fake_run,
    probe_data,
    read_events,
    seed_verdict,
    set_config,
    set_layouts,
    set_rules,
    video,
)
from trackstarr import config, executor, media, planner, processing, rewrites, settings
from trackstarr.command import ffmpeg_args
from trackstarr.executor import Outcome
from trackstarr.planner import (
    changes,
    describe,
    new_plan,
    plan_from_probe,
    planned_tracks,
    track_changes,
    why,
)
from trackstarr.policy import RULES, Policy
from trackstarr.status import Status
from trackstarr.sweep_cache import FileKey, SweepCache, Verdict


def dv_video(index=0, **record):
    return video(index, "hevc") | {
        "side_data_list": [
            {
                "side_data_type": "DOVI configuration record",
                "dv_profile": 8,
                "dv_bl_signal_compatibility_id": 1,
                "bl_present_flag": 1,
                "rpu_present_flag": 1,
                "el_present_flag": 0,
                **record,
            }
        ]
    }


def plan_for(*streams, path="/tmp/movie.mkv", **info):
    return plan_from_probe(new_plan(path, "eng"), probe_data(*streams) | info)


@pytest.fixture(autouse=True)
def isolated_rules():
    set_layouts()
    set_rules(**dict.fromkeys(RULES, "never"))


@pytest.mark.parametrize(
    "field",
    [
        "dv_profile",
        "dv_bl_signal_compatibility_id",
        "bl_present_flag",
        "rpu_present_flag",
        "el_present_flag",
    ],
)
@pytest.mark.parametrize("value", [None, True, "1", 1.0, [], {}])
def test_malformed_record_never_qualifies(field, value):
    result = media.dolby_vision(dv_video(**{field: value}))
    assert not result.eligible
    assert "malformed" in result.unsupported


@pytest.mark.parametrize(
    "record",
    [
        {"dv_profile": 5},
        {"dv_profile": 7},
        {"dv_profile": 9},
        {"dv_bl_signal_compatibility_id": 0},
        {"dv_bl_signal_compatibility_id": 2},
        {"dv_bl_signal_compatibility_id": 4},
        {"bl_present_flag": 0},
        {"rpu_present_flag": 0},
        {"el_present_flag": 1},
        {"el_present_flag": 2},
    ],
)
def test_unsupported_dv_does_not_block_other_rules(record):
    set_rules(dv_strip="always", order="always")
    plan = plan_for(audio(0, 2), dv_video(1, **record))
    assert plan.needed and not plan.skip and plan.rules == {"order"}
    assert plan.notes and plan.notes[0] in describe(plan)
    assert why(plan)["notes"] == plan.notes
    assert not any(stream.dv_strip for stream in plan.streams)
    assert plan.tracks[1]["dv"]["unsupported"]
    settled = plan_for(dv_video(**record), audio(1, 2))
    assert not settled.needed and not settled.incidental


def test_missing_conflicting_and_non_video_metadata():
    assert media.dolby_vision(video()) is None
    assert media.dolby_vision(video() | {"side_data_list": [{}]}) is None
    assert not media.dolby_vision(video() | {"side_data_list": [None, {}]}).eligible
    for name in ("DOVI RPU", "Dolby Vision Metadata"):
        result = media.dolby_vision(video() | {"side_data_list": [{"side_data_type": name}]})
        assert "Missing" in result.unsupported
    for data in (None, {}, "bad"):
        assert not media.dolby_vision(video() | {"side_data_list": data}).eligible
    malformed = video() | {"side_data_list": [{"side_data_type": 1}]}
    assert not media.dolby_vision(malformed).eligible
    stream = dv_video()
    stream["side_data_list"] *= 2
    assert "Conflicting" in media.dolby_vision(stream).unsupported
    stream = dv_video()
    del stream["side_data_list"][0]["rpu_present_flag"]
    assert not media.dolby_vision(stream).eligible
    for extra in (
        {"codec_name": "av1"},
        {"codec_name": "mjpeg"},
        {"codec_type": "audio"},
        {"disposition": {"attached_pic": 1}},
    ):
        assert not media.dolby_vision(dv_video() | extra).eligible


@pytest.mark.parametrize("mode", ["never", "alongside", "always"])
@pytest.mark.parametrize("rewrite", [False, True])
def test_rule_modes_and_planned_tracks(mode, rewrite):
    set_rules(dv_strip=mode, order="always")
    streams = [audio(0, 2), dv_video(1)] if rewrite else [dv_video(), audio(1, 2)]
    plan = plan_for(*streams)
    applies = mode == "always" or (mode == "alongside" and rewrite)
    assert plan.needed == (rewrite or mode == "always")
    assert any(out.dv_strip for out in plan.streams) == applies
    planned = next(track for track in planned_tracks(plan) if track["kind"] == "video")
    assert planned.get("dv_removed", False) == applies
    assert ("dv" in planned) != applies
    assert ("dv_strip" in plan.rules) == (mode == "always")
    assert ("dv_strip" in plan.incidental_rules) == (mode == "alongside")
    assert not plan_for(video(codec="hevc"), audio(1, 2)).needed


def test_filter_uses_output_video_ordinal_with_artwork_and_audio_between():
    set_rules(dv_strip="always")
    plan = plan_for(video(0, "mjpeg"), audio(1, 2), dv_video(2), audio(3, 2), dv_video(4))
    args = ffmpeg_args(plan, "/tmp/staged")
    assert "-bsf:v:0" not in args
    assert args[args.index("-bsf:v:1") + 1] == "dovi_rpu=strip=1"
    assert args[args.index("-bsf:v:2") + 1] == "dovi_rpu=strip=1"
    assert "-bsf:v:4" not in args and args[args.index("-c") + 1] == "copy"
    assert args[args.index("-map_chapters") + 1] == "0"
    cover = dv_video() | {"disposition": {"attached_pic": 1}}
    assert not plan_for(cover, audio(1, 2)).needed


def test_rejected_trial_is_explained_and_strips_abort_on_error():
    set_rules(dv_strip="always")
    rejected = dv_video() | {media.STRIP_REJECTED: "Buffering period SEI requires HRD"}
    plan = plan_for(rejected, audio(1, 2))
    assert not plan.needed
    assert plan.notes == [
        (
            "video stream 0: FFmpeg cannot remove Dolby Vision from this stream "
            "(Buffering period SEI requires HRD)"
        )
    ]
    assert "-xerror" not in ffmpeg_args(plan, "/tmp/staged")
    assert "-xerror" in ffmpeg_args(plan_for(dv_video(), audio(1, 2)), "/tmp/staged")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (fake_run(), ""),
        (
            fake_run(
                183,
                stderr="[dovi_rpu @ 0x7e55d3152f80] Buffering period SEI requires HRD.\n"
                "[dovi_rpu @ 0x7e55d3152f80] Failed to read unit 8 (type 39).\n",
            ),
            "Buffering period SEI requires HRD",
        ),
        (fake_run(1), "ffmpeg exited 1"),
    ],
)
def test_strip_trial_reports_ffmpegs_first_error(monkeypatch, result, expected):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
    assert media.strip_trial("/f", 0) == expected


@pytest.mark.parametrize("error", [OSError("gone"), subprocess.TimeoutExpired("ffmpeg", 1)])
def test_strip_trial_that_cannot_run_is_a_probe_error(monkeypatch, error):
    def fail(*a, **k):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(media.ProbeError):
        media.strip_trial("/f", 0)


def test_build_plan_trials_eligible_streams_while_the_rule_runs(tmp_path, monkeypatch):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"")
    tried = []

    def trial(file, index):
        tried.append(index)
        return "Buffering period SEI requires HRD"

    monkeypatch.setattr(planner, "probe", lambda _: probe_data(dv_video(), audio(1, 2)))
    monkeypatch.setattr(planner, "strip_trial", trial)
    set_rules(dv_strip="always")
    plan = planner.build_plan(str(path), "eng")
    assert tried == [0] and not plan.needed
    assert plan.notes[0].startswith("video stream 0: FFmpeg cannot remove Dolby Vision")
    set_rules(dv_strip="never")
    planner.build_plan(str(path), "eng")
    assert tried == [0]


@pytest.mark.parametrize("ext", ["mkv", "mp4", "m4v", "mov"])
@pytest.mark.parametrize("remux", ["never", "always"])
def test_container_gate(ext, remux):
    set_rules(dv_strip="always", remux=remux)
    plan = plan_for(dv_video(), audio(1, 2), path=f"/tmp/movie.{ext}")
    assert plan.streams[0].dv_strip == (ext != "mov")
    assert bool(plan.notes) == (ext == "mov")


def test_default_persistence_environment_and_cache_invalidation(settings_state, monkeypatch):
    config.apply(config.load())
    before = Policy.from_config().fingerprint()
    assert dict(Policy.from_config().rule_modes)["dv_strip"] == "never"
    old = copy.deepcopy(before)
    old["rule_modes"] = [pair for pair in old["rule_modes"] if pair[0] != "dv_strip"]
    settings_state.mkdir(parents=True, exist_ok=True)
    path = str(settings_state / "old-cache.json")
    cache = SweepCache(path, old)
    seed_verdict(
        cache,
        "/movie.mkv",
        FileKey(10, 1, 1, "eng"),
        Verdict(Status.CONFORM, tracks=[{"index": 0, "kind": "video"}]),
    )
    cache.save()
    assert SweepCache.load(path, before).lookup("/movie.mkv", FileKey(10, 1, 1, "eng")) is None
    settings.update({"RULE_DV_STRIP": "always"})
    assert dict(Policy.from_config().rule_modes)["dv_strip"] == "always"
    assert before != Policy.from_config().fingerprint()
    monkeypatch.setenv("RULE_DV_STRIP", "alongside")
    config.apply(config.load())
    assert dict(Policy.from_config().rule_modes)["dv_strip"] == "alongside"


@pytest.mark.parametrize(
    "result,expected",
    [
        (fake_run(stdout="  -strip             <boolean>"), True),
        (fake_run(stdout="Unknown bit stream filter"), False),
        (fake_run(returncode=1, stdout="  -strip "), False),
    ],
)
def test_filter_capability(monkeypatch, result, expected):
    executor.dv_filter_available.cache_clear()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
    assert executor.dv_filter_available() == expected
    executor.dv_filter_available.cache_clear()


@pytest.mark.parametrize("error", [OSError(), subprocess.TimeoutExpired("ffmpeg", 30)])
def test_capability_probe_failure(monkeypatch, error):
    executor.dv_filter_available.cache_clear()

    def fail(*a, **k):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    assert not executor.dv_filter_available()
    executor.dv_filter_available.cache_clear()


@pytest.fixture
def staged_plan(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"original")
    set_config(WORK_DIR=str(tmp_path))
    set_rules(dv_strip="always")
    plan = plan_for(dv_video(), audio(1, 2), path=str(source))
    monkeypatch.setattr(executor, "dv_filter_available", lambda: True)
    monkeypatch.setattr(executor, "_run_ffmpeg", lambda *a: (0, ""))
    monkeypatch.setattr(
        executor, "probe", lambda path: probe_data(video(codec="hevc"), audio(1, 2))
    )
    return plan, source


def test_missing_filter_stages_nothing(staged_plan, monkeypatch, tmp_path):
    plan, source = staged_plan
    monkeypatch.setattr(executor, "dv_filter_available", lambda: False)
    outcome, detail = executor.apply_plan(plan)
    assert outcome == Outcome.FAILED and "strip support" in detail
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"side_data_list": dv_video()["side_data_list"]}, "configuration remains"),
        ({"width": 1920}, "video width"),
        ({"pix_fmt": "yuv420p"}, "video pix_fmt"),
        ({"color_transfer": "bt709"}, "video color_transfer"),
        ({"side_data_list": [{"side_data_type": "Mastering display metadata"}]}, "stream HDR"),
    ],
)
def test_stream_verification_rejects_changes(staged_plan, monkeypatch, change, reason):
    plan, source = staged_plan
    monkeypatch.setattr(
        executor, "probe", lambda p: probe_data(video(codec="hevc") | change, audio(1, 2))
    )
    outcome, detail = executor.apply_plan(plan)
    assert outcome == Outcome.FAILED and reason in detail
    assert source.read_bytes() == b"original"


HDR = {"side_data_type": "Mastering display metadata", "max_luminance": "1000/1"}
HDR_PLUS = {
    "side_data_type": "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)",
    "application_version": 1,
}
FRAME = {"media_type": "video", "width": 3840, "side_data_list": [HDR, HDR_PLUS]}


@pytest.mark.parametrize(
    "frames,reason",
    [
        ([], "frame count"),
        ([FRAME | {"width": 1920}], "sampled video width"),
        ([FRAME | {"side_data_list": [HDR]}], "sampled HDR"),
        ([FRAME | {"side_data_list": [HDR_PLUS]}], "sampled HDR"),
        (
            [
                FRAME
                | {
                    "side_data_list": [
                        HDR,
                        HDR_PLUS,
                        {"side_data_type": "Dolby Vision RPU Data"},
                    ]
                }
            ],
            "data remains",
        ),
        (
            [FRAME | {"side_data_list": [{"side_data_type": "DOVI configuration record"}]}],
            "data remains",
        ),
    ],
)
def test_frame_verification_failure_preserves_source(staged_plan, monkeypatch, frames, reason):
    plan, source = staged_plan
    monkeypatch.setattr(
        executor, "frame_sample", lambda path, index: [FRAME] if path == plan.path else frames
    )
    outcome, detail = executor.apply_plan(plan)
    assert outcome == Outcome.FAILED and reason in detail
    assert source.read_bytes() == b"original"
    assert not list(source.parent.glob(".trackstarr-*"))


def test_frame_probe_error_discards_stage(staged_plan, monkeypatch):
    plan, source = staged_plan

    def fail(*a):
        raise media.ProbeError("sample failed")

    monkeypatch.setattr(executor, "frame_sample", fail)
    assert executor.apply_plan(plan) == (Outcome.FAILED, "sample failed, result discarded")
    assert source.read_bytes() == b"original"


def test_matching_frames_publish_and_probe_exact_stream_indices(staged_plan, monkeypatch):
    plan, _ = staged_plan
    plan.streams[0].src = 3
    calls = []

    def sample(path, index):
        calls.append((path, index))
        return [FRAME]

    monkeypatch.setattr(executor, "frame_sample", sample)
    assert executor.apply_plan(plan) == (Outcome.APPLIED, "")
    assert calls[0] == (plan.path, 3) and calls[1][1] == 0


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "[]",
        "{}",
        '{"frames":null}',
        '{"frames":[]}',
        '{"frames":[null]}',
        '{"frames":[{}]}',
    ],
)
def test_frame_probe_requires_usable_frames(monkeypatch, payload):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_run(stdout=payload))
    with pytest.raises(media.ProbeError, match="usable frames"):
        media.frame_sample("/f", 2)


@pytest.mark.parametrize("sides", [None, {}, [None], [{"side_data_type": None}]])
def test_frame_probe_rejects_malformed_side_data(monkeypatch, sides):
    payload = json.dumps({"frames": [FRAME | {"side_data_list": sides}]})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_run(stdout=payload))
    with pytest.raises(media.ProbeError, match="usable frames"):
        media.frame_sample("/f", 2)


def test_frame_probe_rejects_partial_decode_even_with_success_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: fake_run(stdout=json.dumps({"frames": [FRAME]}), stderr="decode error"),
    )
    with pytest.raises(media.ProbeError, match="decode error"):
        media.frame_sample("/f", 2)


@pytest.mark.parametrize("error", [OSError(), subprocess.TimeoutExpired("ffprobe", 30)])
def test_frame_probe_process_error(monkeypatch, error):
    def fail(*a, **k):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(media.ProbeError, match="probe failed"):
        media.frame_sample("/f", 2)


def test_frame_probe_checks_exit_and_bounded_command(monkeypatch):
    def run(args, **kwargs):
        assert args[args.index("-select_streams") + 1] == "3"
        assert args[args.index("-read_intervals") + 1] == "%+#24"
        assert kwargs["timeout"] == config.current().PROBE_TIMEOUT
        return fake_run(stdout=json.dumps({"frames": [FRAME]}))

    monkeypatch.setattr(subprocess, "run", run)
    assert media.frame_sample("/f", 3) == [FRAME]
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: fake_run(returncode=1, stderr="broken")
    )
    with pytest.raises(media.ProbeError, match="broken"):
        media.frame_sample("/f", 3)


def test_mp4_chapter_track_is_regenerated_and_verified():
    chapter = {"id": 0, "start_time": "0", "end_time": "1"}
    chapter_stream = {
        "index": 2,
        "codec_type": "data",
        "codec_name": "bin_data",
        "codec_tag_string": "text",
    }
    set_rules(dv_strip="always", stray_streams="always")
    plan = plan_for(
        dv_video(), audio(1, 2), chapter_stream, path="/tmp/f.mp4", chapters=[chapter]
    )
    assert [stream.src for stream in plan.streams] == [0, 1]
    assert [track["index"] for track in plan.tracks] == [0, 1]
    assert not track_changes(plan)
    assert changes(planned_tracks(plan), plan.tracks).drops == 0
    assert plan.rules == {"dv_strip"}
    output = probe_data(video(codec="hevc"), audio(1, 2), chapter_stream) | {
        "chapters": [chapter]
    }
    assert executor._verify(plan, output) is None
    assert executor._verify(plan, output | {"chapters": []}) == "chapter count mismatch"
    assert "stream count mismatch" in executor._verify(
        plan, output | {"streams": output["streams"] * 2}
    )
    set_rules(remux="always")
    remux = plan_for(
        dv_video(), audio(1, 2), chapter_stream, path="/tmp/f.mp4", chapters=[chapter]
    )
    assert [stream.src for stream in remux.streams] == [0, 1]


@pytest.mark.parametrize(
    "lights",
    [
        [],
        [
            {
                "side_data_type": "Content light level metadata",
                "max_content": 0,
                "max_average": 0,
            }
        ],
    ],
)
def test_missing_and_zero_content_light_levels_are_valid(staged_plan, monkeypatch, lights):
    plan, _ = staged_plan
    monkeypatch.setattr(
        executor, "frame_sample", lambda *a: [{"media_type": "video", "side_data_list": lights}]
    )
    assert executor.apply_plan(plan) == (Outcome.APPLIED, "")


@pytest.mark.parametrize("mode", ["always", "alongside"])
def test_completed_strip_survives_rejudging_and_record_reload(tmp_path, stub_rewrite, mode):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"rewritten")
    set_rules(dv_strip=mode, order="always")
    plan = plan_for(audio(0, 2), dv_video(1), path=str(source))
    stub_rewrite(plan)
    assert processing.process(processing.Job(str(source)), dry_run=False).status == "modified"
    rewrites.forget()
    assert rewrites.records()[str(source)].made["dv_removed"] is True
    event = next(line for line in read_events() if line["event"] == "modified")
    assert "dv_strip" in event.get("rules", []) + event.get("incidental_rules", [])


def test_in_place_tag_does_not_record_incidental_dv_removal():
    set_rules(dv_strip="alongside", order="always")
    plan = plan_for(audio(0, 2), dv_video(1))
    assert plan.streams[0].dv_strip
    assert "dv_removed" not in processing._modified(plan, 10, 10, in_place=True)
