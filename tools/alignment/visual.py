"""Bounded visual alignment experiment. No production acceptance policy or media writes."""

import math
import statistics
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Fixed before benchmarking. These limits are research parameters, not calibrated confidence.
SETTINGS: dict = {
    "regions": 8,
    "probes_per_region": 12,
    "max_distance": 60,
    "ambiguity_margin": 8,
    "alternative_separation_us": 500_000,
    "min_region_matches": 2,
    "min_match_fraction": 0.5,
    "max_residual_us": 50_000,
    "min_span_fraction": 0.8,
    "min_scale": 0.9,
    "max_scale": 1.1,
    "max_duration_seconds": 120,
    "max_frames": 20_000,
}


@dataclass(frozen=True)
class Frame:
    time_us: int
    confidence: int
    lower: int
    upper: int


def fingerprint(values: list[int]) -> tuple[int, int]:
    if len(values) != 380 or any(v not in (0, 1, 2) for v in values):
        raise ValueError("expected 380 ternary signature values")
    return (
        sum((v >= 1) << i for i, v in enumerate(values)),
        sum((v == 2) << i for i, v in enumerate(values)),
    )


def read_signatures(path: Path) -> list[Frame]:
    if path.stat().st_size > 32_000_000:
        raise ValueError("signature export exceeds research size bound")
    raw = path.read_bytes()
    if b"<!" in raw:
        raise ValueError("unexpected XML declaration")
    root = ET.fromstring(raw)  # noqa: S314 - bounded local FFmpeg output, no declarations
    units = root.findall(".//{*}MediaTimeUnit")
    if len(units) != 1 or units[0].text != "1000000":
        raise ValueError("expected microsecond signature time base")
    frames: list[Frame] = []
    for element in root.findall(".//{*}VideoFrame"):
        timestamp = int(element.findtext("{*}MediaTimeOfFrame", ""))
        confidence = int(element.findtext("{*}FrameConfidence", ""))
        values = [int(v) for v in element.findtext("{*}FrameSignature", "").split()]
        if abs(timestamp) > 86_400_000_000 or not 0 <= confidence <= 255:
            raise ValueError("invalid frame timestamp or confidence")
        if frames and timestamp < frames[-1].time_us:
            raise ValueError("signature timestamps must not decrease")
        frames.append(Frame(timestamp, confidence, *fingerprint(values)))
    if not 2 <= len(frames) <= SETTINGS["max_frames"]:
        raise ValueError("signature frame count outside research bound")
    if frames[0].time_us == frames[-1].time_us:
        raise ValueError("signature timeline has no duration")
    return frames


def extract(binary: str, media: Path, output: Path) -> list[Frame]:
    # settb avoids the exporter's integer division of a non-unit time-base numerator.
    subprocess.run(
        [
            binary,
            "-nostdin",
            "-v",
            "error",
            "-copyts",
            "-threads",
            "1",
            "-i",
            str(media),
            "-map",
            "0:v:0",
            "-an",
            "-filter_threads",
            "1",
            "-vf",
            f"settb=1/1000000,signature=detectmode=off:format=xml:filename={output.name}",
            "-fps_mode",
            "passthrough",
            "-enc_time_base",
            "filter",
            "-f",
            "null",
            "-",
        ],
        cwd=output.parent,
        check=True,
        timeout=120,
    )
    return read_signatures(output)


def region(time_us: float, bounds: tuple[float, float]) -> int:
    return min(7, max(0, int(8 * (time_us - bounds[0]) / (bounds[1] - bounds[0]))))


def assess(anchors: list[dict], target_bounds: tuple, source_bounds: tuple) -> dict:
    """Fit even time regions, then validate odd regions without refitting or dropping pairs."""
    result: dict = {"status": "review_required", "anchors": anchors}
    if any(
        not math.isfinite(v)
        for a in anchors
        for v in (a["source_time_us"], a["target_time_us"])
    ) or any(not math.isfinite(v) for v in (*target_bounds, *source_bounds)):
        raise ValueError("non-finite evidence")
    if target_bounds[1] <= target_bounds[0] or source_bounds[1] <= source_bounds[0]:
        raise ValueError("empty timeline")
    counts = [0] * 8
    for anchor in anchors:
        bucket = region(anchor["source_time_us"], source_bounds)
        anchor["region"] = bucket
        anchor["role"] = "fit" if bucket % 2 == 0 else "validation"
        counts[bucket] += 1
    result["region_counts"] = counts
    training = [a for a in anchors if a["role"] == "fit"]
    if len(training) < 2:
        return result | {"reason": "insufficient_fit_anchors"}
    xs = [a["source_time_us"] for a in training]
    ys = [a["target_time_us"] for a in training]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance == 0:
        return result | {"reason": "degenerate_fit"}
    scale = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / variance
    offset = my - scale * mx
    result["mapping"] = {"scale": scale, "offset_us": offset}
    for anchor in anchors:
        anchor["residual_us"] = anchor["target_time_us"] - (
            scale * anchor["source_time_us"] + offset
        )
    result["max_fit_residual_us"] = max(abs(a["residual_us"]) for a in training)
    validation = [a for a in anchors if a["role"] == "validation"]
    result["max_validation_residual_us"] = (
        max(abs(a["residual_us"]) for a in validation) if validation else None
    )
    result["span_fraction"] = {
        side: (
            max(a[f"{side}_time_us"] for a in anchors)
            - min(a[f"{side}_time_us"] for a in anchors)
        )
        / (bounds[1] - bounds[0])
        for side, bounds in (("source", source_bounds), ("target", target_bounds))
    }
    if not SETTINGS["min_scale"] <= scale <= SETTINGS["max_scale"]:
        reason = "unsupported_scale_or_order"
    elif min(counts) < SETTINGS["min_region_matches"]:
        reason = "insufficient_distributed_evidence"
    elif min(result["span_fraction"].values()) < SETTINGS["min_span_fraction"]:
        reason = "insufficient_coverage"
    elif max(abs(a["residual_us"]) for a in anchors) > SETTINGS["max_residual_us"]:
        reason = "inconsistent_timeline"
    else:
        return result | {"status": "proposed", "reason": "research_limits_met"}
    return result | {"reason": reason}


def match(target: list[Frame], source: list[Frame]) -> dict:
    """Search all target frames for each probe, without timing hints from the fitted mapping."""
    source_bounds = (source[0].time_us, source[-1].time_us)
    target_bounds = (target[0].time_us, target[-1].time_us)
    anchors, probes = [], []
    for bucket in range(8):
        frames = [f for f in source if region(f.time_us, source_bounds) == bucket]
        count = min(len(frames), SETTINGS["probes_per_region"])
        selected = [frames[int((i + 0.5) * len(frames) / count)] for i in range(count)]
        for frame in selected:
            probe: dict = {"source_time_us": frame.time_us, "region": bucket}
            probes.append(probe)
            if frame.confidence == 0:
                probe["reason"] = "uninformative_frame"
                continue
            distances = [
                ((frame.lower ^ f.lower).bit_count() + (frame.upper ^ f.upper).bit_count(), f)
                for f in target
                if f.confidence > 0
            ]
            if not distances:
                probe["reason"] = "uninformative_target"
                continue
            distance, best = min(distances, key=lambda item: item[0])
            alternative = min(
                (
                    d
                    for d, f in distances
                    if abs(f.time_us - best.time_us) > SETTINGS["alternative_separation_us"]
                ),
                default=760,
            )
            probe.update(distance=distance, alternative_distance=alternative)
            if distance > SETTINGS["max_distance"]:
                probe["reason"] = "no_close_match"
            elif alternative - distance < SETTINGS["ambiguity_margin"]:
                probe["reason"] = "ambiguous_match"
            else:
                probe["reason"] = "matched"
                anchors.append(
                    {
                        "target_time_us": best.time_us,
                        "source_time_us": frame.time_us,
                        "distance": distance,
                        "alternative_distance": alternative,
                    }
                )
    result = assess(anchors, target_bounds, source_bounds)
    fractions = [
        sum(p["reason"] == "matched" for p in probes if p["region"] == b)
        / max(1, sum(p["region"] == b for p in probes))
        for b in range(8)
    ]
    if min(fractions) < SETTINGS["min_match_fraction"]:
        result.update(status="review_required", reason="ambiguous_or_missing_regions")
    return result | {
        "probes": probes,
        "region_match_fractions": fractions,
        "independent_validation_search": True,
        "frame_counts": {"target": len(target), "source": len(source)},
    }
