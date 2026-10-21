"""Measure how a source file's timeline maps onto a target's, from their pictures.

FFmpeg's MPEG-7 signature filter fingerprints every frame. Probes from half
of the source's regions vote for one mapping at a fixed set of speeds, and
every frame of the other half checks it, so no region confirms a mapping it
voted for. Ambiguity, cuts and wrong pictures go to review, never a guess.
"""

import contextlib
import hashlib
import json
import logging
import os
import statistics
import subprocess
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction

from . import config, executor
from .executor import Cancel
from .media import ProbeError, is_cover_art, probe
from .planner import FileRevision, TimingMapping

log = logging.getLogger(__name__)

#: Named in every record, so a changed matcher never reuses an old answer.
BACKEND = "signature-consensus"
VERSION = 1

#: What a measurement concluded.
ACCEPTED = "accepted"
#: Evidence exists but does not support one mapping, such as a cut or an
#: ambiguous scene. A person can still choose a mapping.
NEEDS_REVIEW = "needs_review"
#: Nothing to measure with, such as a file without video.
UNSUPPORTED = "unsupported"
#: The measurement itself broke. Says nothing about the content.
FAILED = "failed"

#: Fixed from the evaluation corpus before the episode runs. Times are in
#: microseconds and distances in signature bits.
SETTINGS: dict = {
    # Fingerprints are taken at this width, whatever the source resolution.
    "width": 320,
    "regions": 8,
    "max_distance": 60,
    "ambiguity_margin": 8,
    "alternative_separation_us": 500_000,
    "max_residual_us": 50_000,
    "min_span_fraction": 0.8,
    # Identity, 24 to 23.976, 24 to 25 and PAL speed-up from 23.976, both ways.
    "scales": ["1", "1001/1000", "1000/1001", "25/24", "24/25", "25025/24000", "24000/25025"],
    "votes_per_probe": 8,
    "offset_bin_us": 20_000,
    "min_votes": 10,
    "min_peak_ratio": 2.0,
    "max_sample_gap_us": 75_000,
    "probes_per_region": 100,
    "min_region_confirmations": 3,
    "min_contradicting_run": 3,
    "min_confirmed_regions": 5,
    # Four hours at 30 fps. The export runs about 1.6 KB per frame.
    "max_frames": 432_000,
}

SCALES = tuple(Fraction(scale) for scale in SETTINGS["scales"])

#: A short digest of the settings, stored beside every result.
SETTINGS_DIGEST = hashlib.sha256(
    json.dumps({"backend": BACKEND, "version": VERSION, **SETTINGS}, sort_keys=True).encode()
).hexdigest()[:12]

#: What each refusal means, in words for the sheet. The codes stay in records.
REFUSALS = {
    "no_votes": "no picture in the source matched the file",
    "insufficient_votes": "too few pictures matched",
    "ambiguous_mapping": "the pictures matched at more than one timing",
    "folds_disagree": "the two halves of the source matched at different timings",
    "contradicting_region": "part of the source contradicts the timing",
    "unconfirmed_ends": "the start or end of the source could not be matched",
    "insufficient_coverage": "too little of the source matched",
    "insufficient_span": "the matches cover too little of the running time",
    "inconsistent_timeline": "the matches do not fit one steady timing",
}


def refusal(reason: str) -> str:
    """Why a measurement confirmed no mapping, as the sheet says it."""
    return REFUSALS.get(reason) or f"timing could not be confirmed: {reason}"


#: How far two video timelines may differ and still count as identical.
SAME_TIMELINE_SECONDS = 0.05

#: Verdicts whose predicted frame matched, which end a run of contradictions.
_LOCAL_MATCHES = frozenset({"consistent", "confirms", "uninformative"})

#: How often the matcher's loops look at the kill switch.
_CANCEL_EVERY = 256


class UnsupportedMediaError(ValueError):
    """The file is outside what the matcher measures, such as a timeline too
    long to hold or one with no duration."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame's signature: 380 ternary values as two bit masks."""

    time_us: int
    confidence: int
    lower: int
    upper: int


@dataclass(frozen=True)
class AlignmentResult:
    """One measurement between two file revisions.

    ``evidence`` holds counts and residuals only, never pictures or
    fingerprints. ``mapping`` is set only when the status is accepted.
    """

    target: FileRevision
    source: FileRevision
    status: str
    reason: str
    mapping: TimingMapping | None = None
    evidence: dict = field(default_factory=dict)
    #: Consecutive failed measurements of these revisions, for a bounded retry.
    attempts: int = 1
    backend: str = BACKEND
    version: int = VERSION
    settings: str = SETTINGS_DIGEST

    @property
    def current(self) -> bool:
        """Whether this build would measure the same way."""
        return (self.backend, self.version, self.settings) == (
            BACKEND,
            VERSION,
            SETTINGS_DIGEST,
        )


def _fingerprint(values: list[int]) -> tuple[int, int]:
    if len(values) != 380 or any(value not in (0, 1, 2) for value in values):
        raise ValueError("expected 380 ternary signature values")
    return (
        sum((value >= 1) << bit for bit, value in enumerate(values)),
        sum((value == 2) << bit for bit, value in enumerate(values)),
    )


def _frame(element: ET.Element) -> Frame:
    timestamp = int(element.findtext("{*}MediaTimeOfFrame", ""))
    confidence = int(element.findtext("{*}FrameConfidence", ""))
    values = [int(value) for value in element.findtext("{*}FrameSignature", "").split()]
    if abs(timestamp) > 86_400_000_000 or not 0 <= confidence <= 255:
        raise ValueError("invalid frame timestamp or confidence")
    return Frame(timestamp, confidence, *_fingerprint(values))


def read_signatures(path: str) -> list[Frame]:
    """Every frame of an FFmpeg signature export, parsed as a stream."""
    frames: list[Frame] = []
    unit = None
    with open(path, "rb") as export:
        if b"<!" in export.read(4096):
            raise ValueError("unexpected XML declaration")
        export.seek(0)
        # Local FFmpeg output with no declarations, so no entity expansion.
        for _, element in ET.iterparse(export):  # noqa: S314
            name = element.tag.rsplit("}", 1)[-1]
            if name == "MediaTimeUnit":
                unit = element.text
            elif name == "VideoFrame":
                if unit != "1000000":
                    raise ValueError("expected a microsecond signature time base")
                frame = _frame(element)
                if frames and frame.time_us < frames[-1].time_us:
                    raise ValueError("signature timestamps must not decrease")
                frames.append(frame)
                element.clear()
                if len(frames) > SETTINGS["max_frames"]:
                    raise UnsupportedMediaError("too many frames to analyse")
    if len(frames) < 2 or frames[0].time_us == frames[-1].time_us:
        raise UnsupportedMediaError("the signature timeline has no duration")
    return frames


def main_video(info: dict) -> dict | None:
    """The first video stream that is not cover art."""
    return next(
        (
            stream
            for stream in info.get("streams") or []
            if stream.get("codec_type") == "video" and not is_cover_art(stream)
        ),
        None,
    )


def same_timeline(target: dict, source: dict) -> bool:
    """Whether two probes' main videos start and end together at one frame
    rate, which lets audio move across unmeasured.

    A programme shifted inside an unchanged running time passes, a known
    limit of the shortcut.
    """
    first, second = main_video(target), main_video(source)
    if first is None or second is None:
        return False
    if (first.get("r_frame_rate") or "") != (second.get("r_frame_rate") or ""):
        return False
    try:
        starts = [float(stream.get("start_time") or 0) for stream in (first, second)]
        ends = [executor.stream_end(stream) for stream in (first, second)]
    except ValueError:
        return False
    return (
        abs(starts[0] - starts[1]) <= SAME_TIMELINE_SECONDS
        and abs(ends[0] - ends[1]) <= SAME_TIMELINE_SECONDS
    )


def _signatures(path: str, stream_index: int, workspace: str, cancel: Cancel) -> list[Frame]:
    """Fingerprint one video stream, keeping the parsed frames, not the XML."""
    export = f"{stream_index}.xml"
    args = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-copyts",
        "-i",
        path,
        "-map",
        f"0:{stream_index}",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        # settb avoids the exporter's integer division of a time-base numerator.
        (
            f"scale={SETTINGS['width']}:-2,settb=1/1000000,"
            f"signature=detectmode=off:format=xml:filename={export}"
        ),
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "filter",
        "-f",
        "null",
        "-",
    ]
    try:
        code, stderr = executor.run_ffmpeg(args, cancel=cancel, cwd=workspace)
        if cancel.stopped() or code < 0:
            raise InterruptedError("alignment was stopped")
        if code:
            raise RuntimeError(f"signature export failed ({code}): {stderr.strip()[-300:]}")
        return read_signatures(os.path.join(workspace, export))
    finally:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(workspace, export))


def _unchanged(revision: FileRevision, role: str) -> None:
    """Raise InterruptedError unless the file still holds what was measured.
    A released hard link moves only ctime, which must not cost hours of work."""
    try:
        current = FileRevision.of(revision.path)
    except OSError as err:
        raise InterruptedError(f"{role} is unavailable, measurement discarded") from err
    if (current.identity, current.size, current.mtime_ns) != (
        revision.identity,
        revision.size,
        revision.mtime_ns,
    ):
        raise InterruptedError(f"{role} changed, measurement discarded")


def analyse(target: FileRevision, source: FileRevision, cancel: Cancel) -> AlignmentResult:
    """Measure where the source's pictures fall on the target's timeline.

    Neither file is held against edits, since a measurement can take hours.
    One that changes meanwhile discards the result. Raises InterruptedError
    then, and when stopped.
    """

    def result(status: str, reason: str, **found) -> AlignmentResult:
        return AlignmentResult(target, source, status, reason, **found)

    _unchanged(target, "target")
    _unchanged(source, "source")
    with executor.job_workspace("align") as workspace:
        streams: list[int] = []
        for revision in (target, source):
            try:
                video = main_video(probe(revision.path))
            except ProbeError as err:
                return result(FAILED, f"probe failed: {err}")
            if video is None:
                return result(UNSUPPORTED, "no video stream to align by")
            streams.append(video["index"])
        try:
            frames = [
                _signatures(revision.path, index, workspace, cancel)
                for revision, index in zip((target, source), streams, strict=True)
            ]
        except subprocess.TimeoutExpired:
            timeout = config.current().FFMPEG_TIMEOUT
            return result(FAILED, f"signature export timed out after {timeout}s")
        except UnsupportedMediaError as err:
            return result(UNSUPPORTED, str(err))
        except (ValueError, ET.ParseError) as err:
            return result(FAILED, f"signature export unreadable: {err}")
        except InterruptedError:
            # An OSError, but a stop rather than a failure.
            raise
        except (OSError, RuntimeError) as err:
            return result(FAILED, str(err))
        measured = match(frames[0], frames[1], cancel)
    _unchanged(target, "target")
    _unchanged(source, "source")
    log.info(
        "aligned %s to %s: %s (%s)", source.path, target.path, measured.status, measured.reason
    )
    return result(
        measured.status, measured.reason, mapping=measured.mapping, evidence=measured.evidence
    )


@dataclass(frozen=True)
class Measurement:
    """What the matcher concluded from two frame lists."""

    status: str
    reason: str
    mapping: TimingMapping | None
    evidence: dict


class _Target:
    """The target's frames, searchable by time. A predicted frame is compared
    whatever its confidence, since a re-encode can lift a dim frame above zero
    on one side only. Only informative frames are evidence."""

    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames
        self.times = [frame.time_us for frame in frames]
        self.informative = [frame for frame in frames if frame.confidence > 0]
        self.bounds = (frames[0].time_us, frames[-1].time_us)

    def near(self, time_us: float, within: float) -> list[Frame]:
        start = bisect_left(self.times, time_us - within)
        return self.frames[start : bisect_right(self.times, time_us + within)]


def _distance(first: Frame, second: Frame) -> int:
    return (first.lower ^ second.lower).bit_count() + (first.upper ^ second.upper).bit_count()


def _predict(hypothesis: dict, time_us: float) -> float:
    return float(hypothesis["scale"]) * time_us + hypothesis["offset_us"]


def _separated(first: dict, second: dict, bounds: tuple[int, int], limit: float) -> bool:
    # Both mappings are linear, so the largest gap is at one end.
    return max(abs(_predict(first, time) - _predict(second, time)) for time in bounds) > limit


def _region(time_us: int, bounds: tuple[int, int]) -> int:
    count = SETTINGS["regions"]
    return min(count - 1, max(0, int(count * (time_us - bounds[0]) / (bounds[1] - bounds[0]))))


def _sample(frames: list[Frame]) -> list[Frame]:
    count = min(len(frames), SETTINGS["probes_per_region"])
    return [frames[int((index + 0.5) * len(frames) / count)] for index in range(count)]


def _distinct_matches(frame: Frame, target: _Target) -> list[int]:
    """Target times about as close as the best match, one per separated
    stretch. A probe with more of them than it may vote for abstains, and
    neighbouring frames never vote twice."""
    limit = SETTINGS["max_distance"]
    lower, upper = frame.lower, frame.upper
    close = sorted(
        (gap, other.time_us)
        for other in target.informative
        if (gap := (lower ^ other.lower).bit_count() + (upper ^ other.upper).bit_count())
        <= limit
    )
    chosen: list[int] = []
    for gap, time in close:
        if gap >= close[0][0] + SETTINGS["ambiguity_margin"]:
            break
        if all(abs(time - other) > SETTINGS["alternative_separation_us"] for other in chosen):
            chosen.append(time)
    return chosen if len(chosen) <= SETTINGS["votes_per_probe"] else []


def _vote(target: _Target, probes: list[Frame], cancel: Cancel | None) -> Counter:
    """Each probe votes once per offset bin, at every scale."""
    bin_us = SETTINGS["offset_bin_us"]
    counts: Counter = Counter()
    for index, frame in enumerate(probes):
        if cancel is not None and index % _CANCEL_EVERY == 0 and cancel.stopped():
            raise InterruptedError("alignment was stopped")
        times = _distinct_matches(frame, target)
        for scale in SCALES:
            bins = {round((time - float(scale) * frame.time_us) / bin_us) for time in times}
            counts.update((scale, bucket) for bucket in bins)
    return counts


def _ranked(counts: Counter) -> list[dict]:
    """Hypotheses with the votes of the bins either side, best first."""
    bin_us = SETTINGS["offset_bin_us"]
    scored = [
        {
            "scale": scale,
            "offset_us": bucket * bin_us,
            "votes": sum(counts[(scale, bucket + step)] for step in (-1, 0, 1)),
        }
        for scale, bucket in counts
    ]
    return sorted(scored, key=lambda hypothesis: -hypothesis["votes"])


def _check(frame: Frame, target: _Target, hypothesis: dict, probing: bool) -> dict:
    """Whether the predicted frame matches, and for a probe whether it is the
    distinct match."""
    gap = SETTINGS["max_sample_gap_us"]
    margin = SETTINGS["ambiguity_margin"]
    predicted = _predict(hypothesis, frame.time_us)
    found: dict = {"source_time_us": frame.time_us}
    if not target.bounds[0] - gap <= predicted <= target.bounds[1] + gap:
        return found | {"verdict": "no_counterpart"}
    nearest = min(
        ((_distance(frame, other), other.time_us) for other in target.near(predicted, gap)),
        default=None,
    )
    if nearest is None or nearest[0] > SETTINGS["max_distance"]:
        # A distinctly closer match elsewhere is a timing error. One barely
        # closer is noise in a dim, heavily compressed scene.
        limit = SETTINGS["max_distance"]
        if nearest is not None:
            limit = min(limit, nearest[0] - margin)
        contradicted = any(
            _distance(frame, other) <= limit
            for other in target.informative
            if abs(other.time_us - predicted) > gap
        )
        return found | {"verdict": "contradicts" if contradicted else "unmatched"}
    if not probing:
        return found | {"verdict": "consistent"}
    scored = [(_distance(frame, other), other.time_us) for other in target.informative]
    # Distinctness ignores neighbours, which resemble any match in a slow scene.
    alternative = min(
        (
            distance
            for distance, time in scored
            if abs(time - predicted) > SETTINGS["alternative_separation_us"]
        ),
        default=None,
    )
    if alternative is not None and alternative - nearest[0] < margin:
        return found | {"verdict": "uninformative"}
    # Only a frame that also beats its neighbours pins the time, not just the scene.
    runner_up = min((distance for distance, time in scored if time != nearest[1]), default=None)
    exact = runner_up is None or runner_up - nearest[0] >= margin
    return found | {"verdict": "confirms", "target_time_us": nearest[1], "exact": exact}


def _contradicting_run(verdicts: list[str]) -> bool:
    """Consecutive contradictions mark a cut, however long the region."""
    run = 0
    for verdict in verdicts:
        if verdict == "contradicts":
            run += 1
            if run >= SETTINGS["min_contradicting_run"]:
                return True
        elif verdict in _LOCAL_MATCHES:
            run = 0
    return False


def _check_region(
    frames: list[Frame], target: _Target, hypothesis: dict, cancel: Cancel | None
) -> list[dict]:
    """Every frame in time order, stopping once a contradicting run settles it."""
    probes = {frame.time_us for frame in _sample(frames)}
    checks: list[dict] = []
    run = SETTINGS["min_contradicting_run"]
    for index, frame in enumerate(frames):
        if cancel is not None and index % _CANCEL_EVERY == 0 and cancel.stopped():
            raise InterruptedError("alignment was stopped")
        checks.append(_check(frame, target, hypothesis, frame.time_us in probes))
        if checks[-1]["verdict"] == "contradicts" and _contradicting_run(
            [check["verdict"] for check in checks[-run:]]
        ):
            break
    return checks


def _fold(
    regions: list[list[Frame]],
    target: _Target,
    parity: int,
    bounds: tuple[int, int],
    cancel: Cancel | None,
) -> dict:
    """Fit on one parity of source regions and check the other."""
    fitting = [
        frame
        for index, frames in enumerate(regions)
        if index % 2 == parity
        for frame in _sample(frames)
    ]
    ranked = _ranked(_vote(target, fitting, cancel))
    fold: dict = {"parity": parity, "probes": len(fitting), "checks": {}}
    if not ranked:
        return fold | {"reason": "no_votes"}
    best = ranked[0]
    runner_up = next(
        (
            other
            for other in ranked[1:]
            if _separated(best, other, bounds, SETTINGS["alternative_separation_us"])
        ),
        None,
    )
    fold.update(hypothesis=best, runner_up=runner_up)
    if best["votes"] < SETTINGS["min_votes"]:
        return fold | {"reason": "insufficient_votes"}
    if runner_up and best["votes"] < SETTINGS["min_peak_ratio"] * runner_up["votes"]:
        return fold | {"reason": "ambiguous_mapping"}
    fold["checks"] = {
        index: _check_region(frames, target, best, cancel)
        for index, frames in enumerate(regions)
        if index % 2 != parity
    }
    return fold


def _region_verdict(verdicts: list[str]) -> str:
    if _contradicting_run(verdicts):
        return "contradicts"
    if verdicts.count("confirms") >= SETTINGS["min_region_confirmations"]:
        return "confirms"
    return "insufficient"


def _fit(pairs: list[tuple[int, int]]) -> tuple[float, float] | None:
    """Least-squares scale and offset, or None for fewer than two times."""
    if len(pairs) < 2:
        return None
    sources, targets = zip(*pairs, strict=True)
    source_mean, target_mean = statistics.mean(sources), statistics.mean(targets)
    variance = sum((time - source_mean) ** 2 for time in sources)
    if variance == 0:
        return None
    scale = (
        sum((source - source_mean) * (target - target_mean) for source, target in pairs)
        / variance
    )
    return scale, target_mean - scale * source_mean


def _span(pairs: list[tuple[int, int]], bounds: dict[str, tuple[int, int]]) -> dict:
    """How much of each timeline lies between its first and last confirmed frame."""
    spans = {}
    for index, side in enumerate(("source", "target")):
        times = [pair[index] for pair in pairs]
        start, end = bounds[side]
        spans[side] = (max(times) - min(times)) / (end - start) if times else 0.0
    return spans


def _summary(fold: dict) -> dict:
    """A fold's votes, for the evidence."""
    told = {"parity": fold["parity"], "probes": fold["probes"]}
    for name in ("hypothesis", "runner_up"):
        if hypothesis := fold.get(name):
            told[name] = {
                "scale": str(hypothesis["scale"]),
                "offset_us": hypothesis["offset_us"],
                "votes": hypothesis["votes"],
            }
    if reason := fold.get("reason"):
        told["reason"] = reason
    return told


def match(
    target_frames: list[Frame], source: list[Frame], cancel: Cancel | None = None
) -> Measurement:
    """The mapping both folds agree on, or the reason none can be trusted."""
    bounds = {
        "source": (source[0].time_us, source[-1].time_us),
        "target": (target_frames[0].time_us, target_frames[-1].time_us),
    }
    target = _Target(target_frames)
    regions: list[list[Frame]] = [[] for _ in range(SETTINGS["regions"])]
    for frame in source:
        if frame.confidence > 0:
            regions[_region(frame.time_us, bounds["source"])].append(frame)
    folds = [_fold(regions, target, parity, bounds["source"], cancel) for parity in (0, 1)]
    checks_by_region: list[list[dict]] = [[] for _ in range(SETTINGS["regions"])]
    for fold in folds:
        for index, checks in fold.pop("checks").items():
            checks_by_region[index] = checks
    verdicts = [
        _region_verdict([check["verdict"] for check in checks]) for checks in checks_by_region
    ]
    confirmed = [
        (index, check)
        for index, checks in enumerate(checks_by_region)
        for check in checks
        if check["verdict"] == "confirms"
    ]
    pairs = [(check["source_time_us"], check["target_time_us"]) for _, check in confirmed]
    # Every confirmation shows coverage, but only exact ones time the mapping.
    anchors = [(index, check) for index, check in confirmed if check["exact"]]
    evidence: dict = {
        "folds": [_summary(fold) for fold in folds],
        "regions": verdicts,
        "confirmed": len(pairs),
        "anchors": len(anchors),
        "span": {side: round(value, 4) for side, value in _span(pairs, bounds).items()},
        "frames": {"target": len(target.informative), "source": sum(map(len, regions))},
    }

    def refused(reason: str) -> Measurement:
        return Measurement(NEEDS_REVIEW, reason, None, evidence)

    if reason := next((fold["reason"] for fold in folds if "reason" in fold), None):
        return refused(reason)
    first, second = folds[0]["hypothesis"], folds[1]["hypothesis"]
    if first["scale"] != second["scale"] or _separated(
        first, second, bounds["source"], SETTINGS["max_residual_us"]
    ):
        return refused("folds_disagree")
    if "contradicts" in verdicts:
        return refused("contradicting_region")
    if verdicts[0] != "confirms" or verdicts[-1] != "confirms":
        return refused("unconfirmed_ends")
    if verdicts.count("confirms") < SETTINGS["min_confirmed_regions"]:
        return refused("insufficient_coverage")
    if min(evidence["span"].values()) < SETTINGS["min_span_fraction"]:
        return refused("insufficient_span")
    timed = [(check["source_time_us"], check["target_time_us"]) for _, check in anchors]
    fitted = _fit(timed)
    if fitted is None:
        return refused("inconsistent_timeline")
    # The voted speed is exact. The free fit only has to agree with it.
    scale = first["scale"]
    offset = round(
        statistics.median(target - float(scale) * source for source, target in timed)
    )
    residual = max(
        max(abs(target - float(scale) * source - offset) for source, target in timed),
        max(abs(target - fitted[0] * source - fitted[1]) for source, target in timed),
    )
    evidence["residual_us"] = round(residual)
    if residual > SETTINGS["max_residual_us"]:
        return refused("inconsistent_timeline")
    return Measurement(ACCEPTED, "mapping_confirmed", TimingMapping(scale, offset), evidence)


def record(result: AlignmentResult) -> dict:
    """The result as a verdict stores it. Revisions are named by identity,
    size and mtime, which a hard link released or a rename leaves alone."""
    return {
        "target": _revision_record(result.target),
        "source": _revision_record(result.source),
        "backend": result.backend,
        "version": result.version,
        "settings": result.settings,
        "status": result.status,
        "reason": result.reason,
        **({"attempts": result.attempts} if result.attempts > 1 else {}),
        **(
            {
                "mapping": {
                    "scale": str(result.mapping.scale),
                    "offset_us": result.mapping.offset_us,
                }
            }
            if result.mapping
            else {}
        ),
        "evidence": result.evidence,
    }


def _revision_record(revision: FileRevision) -> dict:
    return {
        "path": revision.path,
        "device": revision.device,
        "inode": revision.inode,
        "size": revision.size,
        "mtime_ns": revision.mtime_ns,
    }


def same_file(stored: object, revision: FileRevision) -> bool:
    """Whether a stored revision record names this revision's content."""
    if not isinstance(stored, dict):
        return False
    return (
        stored.get("device"),
        stored.get("inode"),
        stored.get("size"),
        stored.get("mtime_ns"),
    ) == (revision.device, revision.inode, revision.size, revision.mtime_ns)


def recalled(
    records: Iterable[object], target: FileRevision, source: FileRevision
) -> AlignmentResult | None:
    """A stored, current measurement of exactly these two revisions."""
    for stored in records:
        if not isinstance(stored, dict) or (
            stored.get("backend"),
            stored.get("version"),
            stored.get("settings"),
        ) != (BACKEND, VERSION, SETTINGS_DIGEST):
            continue
        if not same_file(stored.get("target"), target) or not same_file(
            stored.get("source"), source
        ):
            continue
        mapping = None
        if (found := stored.get("mapping")) is not None:
            try:
                mapping = TimingMapping(Fraction(found["scale"]), int(found["offset_us"]))
            except KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError:
                continue
        status = stored.get("status")
        if status not in (ACCEPTED, NEEDS_REVIEW, UNSUPPORTED, FAILED):
            continue
        if (status == ACCEPTED) != (mapping is not None):
            continue
        evidence = stored.get("evidence")
        attempts = stored.get("attempts")
        return AlignmentResult(
            target,
            source,
            status,
            str(stored.get("reason") or ""),
            mapping,
            evidence if isinstance(evidence, dict) else {},
            attempts=attempts if isinstance(attempts, int) and attempts > 0 else 1,
        )
    return None
