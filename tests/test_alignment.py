"""Alignment accepts only a mapping its pictures confirm, and says why it refused any other."""

import functools
import os
import random
import shutil
import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from conftest import audio, probe_data, set_config, video
from trackstarr import alignment, executor
from trackstarr.alignment import (
    ACCEPTED,
    FAILED,
    NEEDS_REVIEW,
    UNSUPPORTED,
    AlignmentResult,
    Frame,
    UnsupportedMediaError,
    match,
)
from trackstarr.planner import FileRevision, TimingMapping


@pytest.fixture(autouse=True)
def workspace(tmp_path):
    set_config(WORK_DIR=str(tmp_path / "work"))
    yield
    assert not executor._source_reads
    assert not executor._preparing
    assert not list((tmp_path / "work").glob("*"))


@functools.cache
def _pictures() -> tuple[Frame, ...]:
    generator = random.Random(7)  # noqa: S311
    return tuple(
        Frame(
            index * 100_000,
            50,
            *alignment._fingerprint([generator.randrange(3) for _ in range(380)]),
        )
        for index in range(192)
    )


def frames() -> list[Frame]:
    """192 unrelated pictures at 10 fps."""
    return list(_pictures())


def shifted(source: list[Frame], scale: float = 1.0, offset_us: int = 0) -> list[Frame]:
    return [
        replace(frame, time_us=round(frame.time_us * scale) + offset_us) for frame in source
    ]


def showing(source: list[Frame], picture: Frame, indices) -> list[Frame]:
    """The frames at ``indices`` replaced by one picture, as a static scene gives."""
    return [
        replace(frame, lower=picture.lower, upper=picture.upper) if index in indices else frame
        for index, frame in enumerate(source)
    ]


def tied(source: list[Frame], period: int) -> list[Frame]:
    """Every ``period``th frame repeats the one before, as a slow scene gives at 24 fps."""
    return [
        replace(frame, lower=source[index - 1].lower, upper=source[index - 1].upper)
        if index % period == 1
        else frame
        for index, frame in enumerate(source)
    ]


def cut(source: list[Frame], first: int, last: int) -> list[Frame]:
    """Frames ``first`` up to ``last`` removed and the rest pulled earlier to close the gap."""
    removed = (last - first) * 100_000
    return [
        replace(frame, time_us=frame.time_us - removed) if index >= last else frame
        for index, frame in enumerate(source)
        if not first <= index < last
    ]


def flipped(frame: Frame, bits: int) -> Frame:
    """The frame with ``bits`` of its signature inverted, as compression noise leaves it."""
    return replace(frame, lower=frame.lower ^ ((1 << bits) - 1))


class StopsAfter(executor.Cancel):
    """A kill switch that trips once it has been read ``reads`` times."""

    def __init__(self, reads: int) -> None:
        super().__init__()
        self.reads = reads

    def stopped(self) -> bool:
        self.reads -= 1
        return self.reads < 0


def test_the_distance_between_fingerprints_is_the_ternary_distance():
    first = [0, 1, 2, 0, 2] * 76
    second = [1, 1, 0, 2, 2] * 76
    distance = alignment._distance(
        Frame(0, 1, *alignment._fingerprint(first)),
        Frame(0, 1, *alignment._fingerprint(second)),
    )
    assert distance == sum(
        abs(value - other) for value, other in zip(first, second, strict=True)
    )


@pytest.mark.parametrize("values", [[0] * 379, [3] * 380, [-1] * 380])
def test_a_signature_that_is_not_380_ternary_values_is_refused(values):
    with pytest.raises(ValueError, match="380 ternary"):
        alignment._fingerprint(values)


def export(
    times=(0, 1000), unit: int = 1_000_000, confidence: int = 30, values=(1,) * 380
) -> str:
    """An FFmpeg signature export with one frame at each of ``times``."""
    signature = " ".join(map(str, values))
    body = "".join(
        f"<VideoFrame><MediaTimeOfFrame>{time}</MediaTimeOfFrame>"
        f"<FrameConfidence>{confidence}</FrameConfidence><Word>1 2 3 4 5</Word>"
        f"<FrameSignature>{signature}</FrameSignature></VideoFrame>"
        for time in times
    )
    return (
        "<?xml version='1.0' encoding='ASCII' ?>"
        '<Mpeg7 xmlns="urn:mpeg:mpeg7:schema:2001">'
        f"<MediaTimeUnit>{unit}</MediaTimeUnit>{body}</Mpeg7>"
    )


def written(tmp_path, contents: str) -> str:
    path = tmp_path / "0.xml"
    path.write_text(contents)
    return str(path)


def test_an_export_reads_as_timed_fingerprints(tmp_path):
    values = [0, 1, 2, 1] * 95
    parsed = alignment.read_signatures(
        written(tmp_path, export((2_000_000, 2_042_000), values=values))
    )
    fingerprint = alignment._fingerprint(values)
    assert parsed == [Frame(2_000_000, 30, *fingerprint), Frame(2_042_000, 30, *fingerprint)]


@pytest.mark.parametrize(
    ("contents", "error"),
    [
        (export((100, 0)), "must not decrease"),
        (export(unit=1000), "microsecond"),
        (export((0, 2**64 - 1)), "invalid frame"),
        (export(confidence=256), "invalid frame"),
        (export(values=(1,) * 379), "380 ternary"),
        ("<!DOCTYPE foo><Mpeg7/>", "XML declaration"),
    ],
)
def test_a_malformed_export_is_refused(tmp_path, contents, error):
    with pytest.raises(ValueError, match=error):
        alignment.read_signatures(written(tmp_path, contents))


@pytest.mark.parametrize("times", [(0,), (100, 100)])
def test_an_export_without_duration_is_unsupported(tmp_path, times):
    with pytest.raises(UnsupportedMediaError, match="no duration"):
        alignment.read_signatures(written(tmp_path, export(times)))


def test_an_export_too_long_to_hold_is_unsupported(tmp_path, monkeypatch):
    monkeypatch.setitem(alignment.SETTINGS, "max_frames", 2)
    with pytest.raises(UnsupportedMediaError, match="too many frames"):
        alignment.read_signatures(written(tmp_path, export((0, 1, 2))))


def test_the_main_video_skips_cover_art():
    info = probe_data(video(0, "mjpeg", attached_pic=1), audio(1, 2), video(2))
    assert alignment.main_video(info)["index"] == 2


@pytest.mark.parametrize("info", [probe_data(audio(0, 2)), {}])
def test_a_file_without_video_has_no_main_video(info):
    assert alignment.main_video(info) is None


def timed(**changes) -> dict:
    """A probe of one 23.976 fps picture running 1000 s from zero, with ``changes``."""
    stream = video() | {"r_frame_rate": "24000/1001", "start_time": "0.0", "duration": "1000"}
    return probe_data(stream | changes)


@pytest.mark.parametrize(
    ("changes", "same"),
    [
        ({}, True),
        ({"start_time": "0.04"}, True),
        ({"duration": None, "tags": {"DURATION": "00:16:40.000000000"}}, True),
        ({"start_time": "0.1"}, False),
        ({"duration": "999.9"}, False),
        ({"r_frame_rate": "24/1"}, False),
        ({"duration": None}, False),
        ({"start_time": "N/A"}, False),
    ],
)
def test_a_timeline_is_the_same_only_if_both_ends_and_the_rate_agree(changes, same):
    assert alignment.same_timeline(timed(), timed(**changes)) is same


def test_a_file_without_video_never_shares_a_timeline():
    assert not alignment.same_timeline(timed(), probe_data(audio(0, 2)))


def test_a_listed_speed_and_an_offset_are_recovered():
    source = frames()
    measured = match(shifted(source, 25 / 24, 1_250_000), source)
    assert (measured.status, measured.reason) == (ACCEPTED, "mapping_confirmed")
    assert measured.mapping.scale == Fraction(25, 24)
    assert measured.mapping.offset_us == pytest.approx(1_250_000, abs=10)
    assert measured.evidence["regions"] == ["confirms"] * 8
    assert {fold["hypothesis"]["scale"] for fold in measured.evidence["folds"]} == {"25/24"}


def test_each_region_is_checked_only_by_the_fold_that_did_not_fit_it():
    source = frames()
    evidence = match(shifted(source, offset_us=500_000), source).evidence
    assert [fold["parity"] for fold in evidence["folds"]] == [0, 1]
    assert sum(fold["probes"] for fold in evidence["folds"]) == len(source)
    assert evidence["confirmed"] == len(source)


def test_static_middle_regions_are_uninformative_not_contradicting():
    original = frames()
    # Regions 2 and 5 of 8.
    source = showing(original, original[60], {*range(48, 72), *range(120, 144)})
    measured = match(shifted(source), source)
    assert measured.status == ACCEPTED
    assert measured.evidence["regions"][2] == measured.evidence["regions"][5] == "insufficient"


def test_a_static_opening_leaves_the_start_unconfirmed():
    original = frames()
    source = showing(original, original[60], range(24))
    assert match(shifted(source), source).reason == "unconfirmed_ends"


def test_a_long_static_scene_leaves_too_few_regions_confirmed():
    original = frames()
    source = showing(original, original[60], range(48, 144))
    measured = match(shifted(source), source)
    assert measured.reason == "insufficient_coverage"
    assert measured.evidence["regions"].count("confirms") == 4


@pytest.mark.parametrize(("copies", "reason"), [(2, "ambiguous_mapping"), (10, "no_votes")])
def test_content_the_target_repeats_never_picks_a_copy(copies, reason):
    source = frames()
    target = [
        frame
        for copy in range(copies)
        for frame in shifted(source, offset_us=copy * 19_200_000)
    ]
    assert match(target, source).reason == reason


WRONG_SOURCES = {
    "reverse": lambda target: [
        Frame(frame.time_us, mirror.confidence, mirror.lower, mirror.upper)
        for frame, mirror in zip(target, reversed(target), strict=True)
    ],
    "black": lambda target: [Frame(frame.time_us, 0, 0, 0) for frame in target],
    "unrelated": lambda target: [
        Frame(frame.time_us, 50, frame.upper, frame.lower) for frame in target
    ],
}


@pytest.mark.parametrize("kind", WRONG_SOURCES)
def test_a_wrong_picture_is_never_accepted(kind):
    target = frames()
    measured = match(target, WRONG_SOURCES[kind](target))
    assert measured.status == NEEDS_REVIEW
    assert measured.mapping is None


def test_an_internal_cut_is_a_contradiction_or_a_disagreement():
    target = frames()
    assert match(target, cut(target, 76, 96)).reason in {
        "contradicting_region",
        "folds_disagree",
        "ambiguous_mapping",
    }


@pytest.mark.parametrize(("first", "last"), [(10, 20), (180, 188), (186, 188), (100, 101)])
def test_cuts_near_the_ends_or_one_frame_long_are_caught(first, last):
    target = frames()
    assert match(target, cut(target, first, last)).reason in {
        "contradicting_region",
        "folds_disagree",
    }


def test_halves_that_fit_different_offsets_are_not_reconciled():
    target = frames()
    # Odd regions show the target's pictures from a second earlier.
    source = [
        replace(frame, lower=target[index - 10].lower, upper=target[index - 10].upper)
        if index // 24 % 2
        else frame
        for index, frame in enumerate(target)
    ]
    measured = match(target, source)
    offsets = [fold["hypothesis"]["offset_us"] for fold in measured.evidence["folds"]]
    assert (measured.reason, offsets) == ("folds_disagree", [0, -1_000_000])


@pytest.mark.parametrize(
    ("period", "anchors", "reason", "mapping"),
    [
        (4, 96, "mapping_confirmed", TimingMapping(offset_us=300_000)),
        (2, 0, "inconsistent_timeline", None),
    ],
)
def test_frames_tied_with_a_neighbour_show_coverage_but_not_timing(
    period, anchors, reason, mapping
):
    source = tied(frames(), period)
    measured = match(shifted(source, offset_us=300_000), source)
    assert (measured.reason, measured.mapping) == (reason, mapping)
    assert (measured.evidence["confirmed"], measured.evidence["anchors"]) == (192, anchors)


def test_anchors_from_one_instant_cannot_time_the_mapping():
    original = frames()
    # Only frames 10 and 11 are untied, and they share a timestamp.
    source = tied(original, 2)
    source[11] = replace(original[11], time_us=original[10].time_us)
    measured = match(shifted(source, offset_us=300_000), source)
    assert (measured.reason, measured.evidence["anchors"]) == ("inconsistent_timeline", 2)


def test_one_anchor_off_the_line_makes_the_timeline_inconsistent():
    source = frames()
    # Inside the 75 ms search window but past the 50 ms residual limit.
    target = [
        replace(frame, time_us=frame.time_us + 70_000) if index == 100 else frame
        for index, frame in enumerate(shifted(source, offset_us=300_000))
    ]
    measured = match(target, source)
    assert measured.reason == "inconsistent_timeline"
    assert measured.evidence["residual_us"] == 70_000


def test_frames_between_sampled_probes_still_catch_a_cut(monkeypatch):
    monkeypatch.setitem(alignment.SETTINGS, "probes_per_region", 4)
    target = frames()
    assert match(shifted(target), target).status == ACCEPTED
    # Removing frames 150 to 152 leaves the evenly spaced probes untouched.
    measured = match(target, cut(target, 150, 153))
    assert measured.reason == "contradicting_region"
    assert measured.evidence["regions"][6] == "contradicts"


@pytest.fixture
def one_contradiction_shows(monkeypatch):
    """A single contradicting frame turns its region's verdict."""
    monkeypatch.setitem(alignment.SETTINGS, "min_contradicting_run", 1)


@pytest.mark.usefixtures("one_contradiction_shows")
def test_a_dim_predicted_frame_is_compared_not_skipped():
    original = frames()
    # A dim scene at 20 to 29 recurs at 100 to 109, where only the target's copy is
    # zero confidence, as a re-encode's grade can leave it.
    source = showing(original, original[25], {*range(20, 30), *range(100, 110)})
    target = [
        replace(frame, confidence=0) if 100 <= index < 110 else frame
        for index, frame in enumerate(shifted(source, offset_us=300_000))
    ]
    assert match(target, source).status == ACCEPTED


@pytest.mark.usefixtures("one_contradiction_shows")
def test_a_barely_closer_match_elsewhere_is_not_a_contradiction():
    original = frames()
    # A dim, static stretch at 120 to 129. Compression leaves the predicted target frames
    # 62 bits off and one neighbour 56 off, closer by less than the ambiguity margin.
    source = showing(original, original[125], range(120, 130))
    noise = {120: 56} | dict.fromkeys(range(121, 130), 62)
    target = [
        flipped(frame, noise[index]) if index in noise else frame
        for index, frame in enumerate(shifted(source, offset_us=300_000))
    ]
    assert match(target, source).status == ACCEPTED


@pytest.mark.usefixtures("one_contradiction_shows")
def test_a_hole_in_the_target_leaves_its_frames_unmatched_not_contradicting():
    source = frames()
    target = [frame for index, frame in enumerate(source) if not 100 <= index < 110]
    assert match(target, source).status == ACCEPTED


def test_a_source_running_past_the_target_leaves_the_end_unconfirmed():
    source = frames()
    measured = match(source[:150], source)
    assert measured.reason == "unconfirmed_ends"
    assert measured.evidence["regions"][7] == "insufficient"


def test_a_source_covering_half_the_target_spans_too_little():
    target = frames()
    measured = match(target, target[:96])
    assert measured.reason == "insufficient_span"
    assert measured.evidence["span"] == {"source": 1.0, "target": 0.4974}


def test_a_source_too_short_to_outvote_noise_has_too_few_votes():
    target = frames()
    assert match(target, target[:12]).reason == "insufficient_votes"


@pytest.mark.parametrize("reads", [0, 1])
def test_a_stop_while_voting_or_checking_interrupts_the_match(reads):
    source = frames()
    with pytest.raises(InterruptedError, match="stopped"):
        match(shifted(source), source, StopsAfter(reads))


TARGET = FileRevision("/media/target.mkv", 1, 10, 1_000, 5, 6)
SOURCE = FileRevision("/media/source.mkv", 1, 20, 2_000, 7, 8)
MEASURED = AlignmentResult(
    TARGET,
    SOURCE,
    ACCEPTED,
    "mapping_confirmed",
    TimingMapping(Fraction(25, 24), 1_250_000),
    {"confirmed": 330},
)
BROKEN = AlignmentResult(TARGET, SOURCE, FAILED, "signature export failed (1): boom")
RECORDED = alignment.record(MEASURED)


def test_only_a_result_from_this_matcher_is_current():
    assert MEASURED.current
    assert not replace(MEASURED, settings="000000000000").current


def test_a_record_names_revisions_by_identity_and_spells_the_mapping_out():
    assert RECORDED == {
        "target": {
            "path": "/media/target.mkv",
            "device": 1,
            "inode": 10,
            "size": 1_000,
            "mtime_ns": 5,
        },
        "source": {
            "path": "/media/source.mkv",
            "device": 1,
            "inode": 20,
            "size": 2_000,
            "mtime_ns": 7,
        },
        "backend": "signature-consensus",
        "version": 1,
        "settings": alignment.SETTINGS_DIGEST,
        "status": ACCEPTED,
        "reason": "mapping_confirmed",
        "mapping": {"scale": "25/24", "offset_us": 1_250_000},
        "evidence": {"confirmed": 330},
    }


def test_a_first_failure_records_neither_attempts_nor_a_mapping():
    assert not {"attempts", "mapping"} & alignment.record(BROKEN).keys()


@pytest.mark.parametrize("result", [MEASURED, replace(BROKEN, attempts=3)])
def test_a_recorded_result_is_recalled_unchanged(result):
    assert alignment.recalled([alignment.record(result)], TARGET, SOURCE) == result


def test_a_renamed_file_or_a_new_ctime_is_still_the_measured_file():
    moved = replace(TARGET, path="/media/renamed.mkv", ctime_ns=99)
    assert alignment.recalled([RECORDED], moved, SOURCE) == replace(MEASURED, target=moved)


@pytest.mark.parametrize("changes", [{"backend": "other"}, {"version": 0}, {"settings": "0"}])
def test_a_record_from_another_matcher_is_not_recalled(changes):
    assert alignment.recalled([RECORDED | changes], TARGET, SOURCE) is None


@pytest.mark.parametrize(
    ("target", "source"),
    [(replace(TARGET, size=1), SOURCE), (TARGET, replace(SOURCE, mtime_ns=1))],
)
def test_a_record_of_another_revision_is_not_recalled(target, source):
    assert alignment.recalled([RECORDED], target, source) is None


@pytest.mark.parametrize(
    "damaged",
    [
        pytest.param("junk", id="not a record"),
        pytest.param(RECORDED | {"target": "/media/target.mkv"}, id="path for a revision"),
        pytest.param(RECORDED | {"mapping": {"offset_us": 0}}, id="no scale"),
        pytest.param(RECORDED | {"mapping": {"scale": None, "offset_us": 0}}, id="null scale"),
        pytest.param(
            RECORDED | {"mapping": {"scale": "fast", "offset_us": 0}}, id="word scale"
        ),
        pytest.param(
            RECORDED | {"mapping": {"scale": "1/0", "offset_us": 0}}, id="zero divisor"
        ),
        pytest.param(
            RECORDED | {"mapping": {"scale": "-1", "offset_us": 0}}, id="negative scale"
        ),
        # What json reads 1e400 as.
        pytest.param(
            RECORDED | {"mapping": {"scale": "1", "offset_us": float("inf")}},
            id="infinite offset",
        ),
        pytest.param(RECORDED | {"mapping": None}, id="accepted without a mapping"),
        pytest.param(RECORDED | {"status": NEEDS_REVIEW}, id="refused with a mapping"),
        pytest.param(RECORDED | {"status": "guessed"}, id="unknown status"),
    ],
)
def test_a_damaged_record_is_passed_over_for_the_next(damaged):
    assert alignment.recalled([damaged, RECORDED], TARGET, SOURCE) == MEASURED


def test_odd_evidence_attempts_and_reason_fall_back_to_defaults():
    stored = alignment.record(BROKEN) | {"evidence": ["frames"], "attempts": 0, "reason": None}
    assert alignment.recalled([stored], TARGET, SOURCE) == replace(BROKEN, reason="")


def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def life(seed: int) -> str:
    """Conway's life, which rarely repeats a picture, seeded so every run is alike."""
    return (
        f"life=s=160x90:rate=24:ratio=0.3:mold=5:seed={seed}"
        ":death_color=#203040:life_color=#f0c040,format=yuv420p"
    )


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("clips")
    target = folder / "target.mkv"
    ffmpeg("-f", "lavfi", "-i", life(1), "-t", 15, "-c:v", "ffv1", target)
    ffmpeg("-f", "lavfi", "-i", life(2), "-t", 15, "-c:v", "ffv1", folder / "unrelated.mkv")
    # 25/24 as fast, with every frame kept on its own timestamp. The encoder's
    # default 1/24 time base would round neighbours onto one.
    ffmpeg(
        "-itsscale",
        0.96,
        "-i",
        target,
        "-vf",
        "trim=start=1.2,setpts=PTS-STARTPTS",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "1/1000",
        "-c:v",
        "ffv1",
        folder / "faster.mkv",
    )
    ffmpeg("-i", target, "-c", "copy", "-output_ts_offset", 2, folder / "delayed.mkv")
    ffmpeg("-i", target, "-vf", "reverse", "-c:v", "ffv1", folder / "reversed.mkv")
    ffmpeg("-f", "lavfi", "-i", life(1), "-frames:v", 1, "-c:v", "ffv1", folder / "still.mkv")
    ffmpeg("-f", "lavfi", "-i", "sine=d=1", "-c:a", "flac", folder / "voice.mka")
    (folder / "notes.mkv").write_text("not media")
    return folder


def measure(
    target: Path, source: Path, cancel: executor.Cancel | None = None
) -> AlignmentResult:
    return alignment.analyse(
        FileRevision.of(str(target)), FileRevision.of(str(source)), cancel or executor.Cancel()
    )


def exporting(contents: str | None, code: int = 0):
    """A run_ffmpeg that leaves ``contents`` as the export and exits with ``code``."""

    def run(args, cancel, cwd):
        if contents is not None:
            filename = args[args.index("-vf") + 1].rsplit("filename=", 1)[1]
            Path(cwd, filename).write_text(contents)
        return code, "boom\n"

    return run


@pytest.mark.parametrize(
    ("name", "mapping"),
    [
        ("faster.mkv", TimingMapping(Fraction(25, 24), 1_250_000)),
        ("delayed.mkv", TimingMapping(Fraction(1), -2_000_000)),
    ],
)
def test_a_retimed_copy_is_measured_from_its_pictures(clips, name, mapping):
    result = measure(clips / "target.mkv", clips / name)
    assert (result.status, result.reason) == (ACCEPTED, "mapping_confirmed")
    assert result.mapping.scale == mapping.scale
    assert result.mapping.offset_us == pytest.approx(mapping.offset_us, abs=2_000)


@pytest.mark.parametrize("name", ["reversed.mkv", "unrelated.mkv"])
def test_a_reversed_or_unrelated_picture_goes_to_review(clips, name):
    result = measure(clips / "target.mkv", clips / name)
    assert result.status == NEEDS_REVIEW
    assert result.mapping is None


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("voice.mka", "no video stream to align by"),
        ("still.mkv", "the signature timeline has no duration"),
    ],
)
def test_a_file_without_a_moving_picture_is_unsupported(clips, name, reason):
    result = measure(clips / "target.mkv", clips / name)
    assert (result.status, result.reason) == (UNSUPPORTED, reason)


def test_a_file_ffprobe_cannot_read_fails_the_measurement(clips):
    result = measure(clips / "target.mkv", clips / "notes.mkv")
    assert result.status == FAILED
    assert result.reason.startswith("probe failed: ")


@pytest.mark.parametrize(
    ("contents", "code", "reason"),
    [
        (None, 1, "signature export failed (1): boom"),
        (None, 0, "[Errno 2] No such file"),
        ("<Mpeg7>", 0, "signature export unreadable: "),
        (export(unit=1000), 0, "signature export unreadable: expected a microsecond"),
    ],
)
def test_a_failed_or_unreadable_export_fails_the_measurement(
    clips, monkeypatch, contents, code, reason
):
    monkeypatch.setattr(executor, "run_ffmpeg", exporting(contents, code))
    result = measure(clips / "target.mkv", clips / "faster.mkv")
    assert result.status == FAILED
    assert result.reason.startswith(reason)


def test_an_export_that_times_out_fails_the_measurement(clips, monkeypatch):
    def stalled(args, cancel, cwd):
        raise subprocess.TimeoutExpired(args, 7)

    set_config(FFMPEG_TIMEOUT=7)
    monkeypatch.setattr(executor, "run_ffmpeg", stalled)
    result = measure(clips / "target.mkv", clips / "faster.mkv")
    assert (result.status, result.reason) == (FAILED, "signature export timed out after 7s")


def test_a_stopped_measurement_raises_rather_than_failing(clips):
    stopped = executor.Cancel()
    stopped.ask()
    with pytest.raises(InterruptedError, match="stopped"):
        measure(clips / "target.mkv", clips / "faster.mkv", stopped)


def test_an_ffmpeg_killed_from_outside_interrupts_the_measurement(clips, monkeypatch):
    monkeypatch.setattr(executor, "run_ffmpeg", exporting(None, -15))
    with pytest.raises(InterruptedError, match="stopped"):
        measure(clips / "target.mkv", clips / "faster.mkv")


def test_a_source_changing_mid_measurement_is_discarded(clips, tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    shutil.copy(clips / "faster.mkv", source)
    original = executor.run_ffmpeg

    def touching(args, cancel, cwd):
        os.utime(source, ns=(0, 0))
        return original(args, cancel=cancel, cwd=cwd)

    monkeypatch.setattr(executor, "run_ffmpeg", touching)
    with pytest.raises(InterruptedError, match="source changed"):
        measure(clips / "target.mkv", source)


def test_a_refusal_reads_as_words_and_an_unknown_one_as_itself():
    assert alignment.refusal("folds_disagree") == (
        "the two halves of the source matched at different timings"
    )
    assert alignment.refusal("no video stream to align by") == (
        "timing could not be confirmed: no video stream to align by"
    )


def test_a_released_hard_link_does_not_discard_a_measurement(tmp_path):
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(b"clip")
    before = FileRevision.of(str(clip))
    os.chmod(clip, 0o600)
    alignment._unchanged(before, "source")
    os.utime(clip, ns=(0, 0))
    with pytest.raises(InterruptedError, match="target changed"):
        alignment._unchanged(before, "target")
    clip.unlink()
    with pytest.raises(InterruptedError, match="target is unavailable"):
        alignment._unchanged(before, "target")
