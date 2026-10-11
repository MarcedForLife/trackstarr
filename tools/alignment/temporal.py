"""Compare short fingerprint sequences without a fitted-timeline search hint."""

from bisect import bisect_left

from visual import SETTINGS, Frame, assess, region

# A separate experiment. Keep the baseline matcher and acceptance limits unchanged.
TEMPORAL_SETTINGS: dict = {
    "context_offsets_us": [-400_000, -200_000, 0, 200_000, 400_000],
    "context_scales": [0.9 + i * 0.02 for i in range(11)],
    "max_sample_gap_us": 75_000,
}


def window(frames: list[Frame], times: list[int], center: int, scale: float) -> list[Frame]:
    selected = []
    for delta in TEMPORAL_SETTINGS["context_offsets_us"]:
        point = center + scale * delta
        if point < times[0] or point > times[-1]:
            return []
        index = bisect_left(times, point)
        nearest = min(
            (max(0, index - 1), min(index, len(times) - 1)), key=lambda i: abs(times[i] - point)
        )
        frame = frames[nearest]
        if abs(frame.time_us - point) > TEMPORAL_SETTINGS["max_sample_gap_us"]:
            return []
        if frame.confidence == 0:
            return []
        selected.append(frame)
    return selected


def match_temporal(target: list[Frame], source: list[Frame]) -> dict:
    source_bounds = (source[0].time_us, source[-1].time_us)
    target_bounds = (target[0].time_us, target[-1].time_us)
    target_times = [f.time_us for f in target]
    source_times = [f.time_us for f in source]
    candidates = [
        (frame, scale, context)
        for frame in target
        for scale in TEMPORAL_SETTINGS["context_scales"]
        if (context := window(target, target_times, frame.time_us, scale))
    ]
    anchors, probes = [], []
    for bucket in range(SETTINGS["regions"]):
        frames = [f for f in source if region(f.time_us, source_bounds) == bucket]
        count = min(len(frames), SETTINGS["probes_per_region"])
        selected = [frames[int((i + 0.5) * len(frames) / count)] for i in range(count)]
        for frame in selected:
            probe: dict = {"source_time_us": frame.time_us, "region": bucket}
            probes.append(probe)
            context = window(source, source_times, frame.time_us, 1)
            # Keep source context within its fit or held-out region.
            if not context or any(region(f.time_us, source_bounds) != bucket for f in context):
                probe["reason"] = "missing_context"
                continue
            distances = [
                (
                    sum(
                        (a.lower ^ b.lower).bit_count() + (a.upper ^ b.upper).bit_count()
                        for a, b in zip(context, other, strict=True)
                    )
                    / len(context),
                    candidate,
                    scale,
                )
                for candidate, scale, other in candidates
            ]
            if not distances:
                probe["reason"] = "uninformative_target"
                continue
            distance, best, scale = min(distances, key=lambda item: item[0])
            alternative = min(
                (
                    d
                    for d, f, _ in distances
                    if abs(f.time_us - best.time_us) > SETTINGS["alternative_separation_us"]
                ),
                default=760,
            )
            probe.update(
                distance=distance, alternative_distance=alternative, context_scale=scale
            )
            if distance > SETTINGS["max_distance"]:
                probe["reason"] = "no_close_match"
            elif alternative - distance < SETTINGS["ambiguity_margin"]:
                probe["reason"] = "ambiguous_match"
            else:
                probe["reason"] = "matched"
                anchors.append(
                    {
                        "source_time_us": frame.time_us,
                        "target_time_us": best.time_us,
                        "distance": distance,
                        "alternative_distance": alternative,
                        "context_scale": scale,
                    }
                )
    result = assess(anchors, target_bounds, source_bounds)
    fractions = [
        sum(p["reason"] == "matched" for p in probes if p["region"] == b)
        / max(1, sum(p["region"] == b for p in probes))
        for b in range(SETTINGS["regions"])
    ]
    if min(fractions) < SETTINGS["min_match_fraction"]:
        result.update(status="review_required", reason="ambiguous_or_missing_regions")
    return result | {
        "probes": probes,
        "region_match_fractions": fractions,
        "independent_validation_search": True,
        "frame_counts": {"target": len(target), "source": len(source)},
    }
