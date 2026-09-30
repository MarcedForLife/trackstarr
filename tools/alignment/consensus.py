"""Vote for one timeline mapping, then check it on regions that did not vote."""

import statistics
from collections import Counter
from fractions import Fraction

from visual import SETTINGS, Frame, region

# Fixed before benchmarking. Research parameters, not calibrated acceptance limits.
CONSENSUS_SETTINGS: dict = {
    # Identity, 24 to 23.976, 24 to 25 and PAL speed-up from 23.976, in both directions.
    "scales": ["1", "1001/1000", "1000/1001", "25/24", "24/25", "25025/24000", "24000/25025"],
    "votes_per_probe": 8,
    "offset_bin_us": 20_000,
    "min_votes": 10,
    "min_peak_ratio": 2.0,
    "max_sample_gap_us": 75_000,
    "min_region_confirmations": 3,
    "min_contradicting_run": 3,
    "min_confirmed_regions": 5,
}

SCALES = [Fraction(scale) for scale in CONSENSUS_SETTINGS["scales"]]


def distance(first: Frame, second: Frame) -> int:
    return (first.lower ^ second.lower).bit_count() + (first.upper ^ second.upper).bit_count()


def predict(hypothesis: dict, time_us: float) -> float:
    return float(Fraction(hypothesis["scale"])) * time_us + hypothesis["offset_us"]


def separated(first: dict, second: dict, source_bounds: tuple, limit: float) -> bool:
    # Both mappings are linear, so the largest gap is at one end of the source timeline.
    return max(abs(predict(first, t) - predict(second, t)) for t in source_bounds) > limit


def distinct_matches(probe: Frame, target: list[Frame]) -> list[int]:
    """Target times about as close as the best match, one per separated stretch.

    A match's neighbouring frames would otherwise take every vote, and a slightly
    wrong scale drifts them into its offset window. A probe with more such matches
    than it may vote for abstains.
    """
    close = sorted(
        (gap, frame.time_us)
        for frame in target
        if (gap := distance(probe, frame)) <= SETTINGS["max_distance"]
    )
    chosen: list[int] = []
    for gap, time in close:
        if gap >= close[0][0] + SETTINGS["ambiguity_margin"]:
            break
        if all(abs(time - other) > SETTINGS["alternative_separation_us"] for other in chosen):
            chosen.append(time)
    return chosen if len(chosen) <= CONSENSUS_SETTINGS["votes_per_probe"] else []


def vote(target: list[Frame], probes: list[Frame]) -> Counter:
    """Each probe votes once per offset bin, at every scale, for its distinct matches."""
    bin_us = CONSENSUS_SETTINGS["offset_bin_us"]
    counts: Counter = Counter()
    for probe in probes:
        times = distinct_matches(probe, target)
        for scale in SCALES:
            bins = {round((time - float(scale) * probe.time_us) / bin_us) for time in times}
            counts.update((scale, bucket) for bucket in bins)
    return counts


def ranked_hypotheses(counts: Counter) -> list[dict]:
    """Votes within one bin either side of each bin, best first."""
    bin_us = CONSENSUS_SETTINGS["offset_bin_us"]
    scored = [
        {
            "scale": str(scale),
            "offset_us": bucket * bin_us,
            "votes": sum(counts[(scale, bucket + step)] for step in (-1, 0, 1)),
        }
        for scale, bucket in counts
    ]
    return sorted(scored, key=lambda hypothesis: -hypothesis["votes"])


def check(frame: Frame, target: list[Frame], hypothesis: dict, bounds: dict) -> dict:
    """Whether the frame the mapping predicts is this frame's distinct match."""
    gap = CONSENSUS_SETTINGS["max_sample_gap_us"]
    predicted = predict(hypothesis, frame.time_us)
    result: dict = {
        "source_time_us": frame.time_us,
        "region": region(frame.time_us, bounds["source"]),
    }
    if not bounds["target"][0] - gap <= predicted <= bounds["target"][1] + gap:
        return result | {"verdict": "no_counterpart"}
    scored = [(distance(frame, f), f.time_us, abs(f.time_us - predicted)) for f in target]
    nearest = min(((d, t) for d, t, away in scored if away <= gap), default=None)
    # Distinctness ignores neighbouring frames, which resemble any match in a slow scene.
    alternative = min(
        (d for d, _, away in scored if away > SETTINGS["alternative_separation_us"]),
        default=None,
    )
    if nearest is not None and nearest[0] <= SETTINGS["max_distance"]:
        margin = SETTINGS["ambiguity_margin"]
        if alternative is not None and alternative - nearest[0] < margin:
            return result | {"verdict": "uninformative"}
        # Only a frame that also beats its neighbours pins the time, not just the scene.
        runner_up = min((d for d, t, _ in scored if t != nearest[1]), default=None)
        exact = runner_up is None or runner_up - nearest[0] >= margin
        return result | {"verdict": "confirms", "target_time_us": nearest[1], "exact": exact}
    # A close match anywhere but the predicted frame, even a few frames away, is a timing error.
    if any(d <= SETTINGS["max_distance"] for d, _, away in scored if away > gap):
        return result | {"verdict": "contradicts"}
    return result | {"verdict": "unmatched"}


def fold(target: list[Frame], source: list[Frame], fit_parity: int, bounds: dict) -> dict:
    """Fit on one parity of source regions and check the other."""
    parities = [region(f.time_us, bounds["source"]) % 2 for f in source]
    fitting = [f for f, parity in zip(source, parities, strict=True) if parity == fit_parity]
    held_out = [f for f, parity in zip(source, parities, strict=True) if parity != fit_parity]
    ranked = ranked_hypotheses(vote(target, fitting))
    result: dict = {"fit_parity": fit_parity, "fit_probes": len(fitting), "checks": []}
    if not ranked:
        return result | {"reason": "no_votes"}
    best = ranked[0]
    runner_up = next(
        (
            h
            for h in ranked[1:]
            if separated(best, h, bounds["source"], SETTINGS["alternative_separation_us"])
        ),
        None,
    )
    result.update(hypothesis=best, runner_up=runner_up)
    if best["votes"] < CONSENSUS_SETTINGS["min_votes"]:
        return result | {"reason": "insufficient_votes"}
    if runner_up and best["votes"] < CONSENSUS_SETTINGS["min_peak_ratio"] * runner_up["votes"]:
        return result | {"reason": "ambiguous_mapping"}
    result["checks"] = [check(f, target, best, bounds) for f in held_out]
    return result


def region_verdict(verdicts: list[str]) -> str:
    """A run of contradicting frames marks a cut however long the region is."""
    run = longest = 0
    for verdict in verdicts:
        if verdict == "contradicts":
            run += 1
            longest = max(longest, run)
        elif verdict == "confirms":
            run = 0
    if longest >= CONSENSUS_SETTINGS["min_contradicting_run"]:
        return "contradicts"
    if verdicts.count("confirms") >= CONSENSUS_SETTINGS["min_region_confirmations"]:
        return "confirms"
    return "insufficient"


def fit(pairs: list[tuple[int, int]]) -> dict | None:
    if len(pairs) < 2:
        return None
    xs, ys = zip(*pairs, strict=True)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance == 0:
        return None
    scale = sum((x - mx) * (y - my) for x, y in pairs) / variance
    return {"scale": scale, "offset_us": my - scale * mx}


def span_fraction(pairs: list[tuple[int, int]], bounds: dict) -> dict:
    """How much of each timeline lies between its first and last confirmed frame."""
    spans = {}
    for index, side in enumerate(("source", "target")):
        times = [pair[index] for pair in pairs]
        start, end = bounds[side]
        spans[side] = (max(times) - min(times)) / (end - start) if times else 0.0
    return spans


def match_consensus(target: list[Frame], source: list[Frame]) -> dict:
    bounds = {
        "source": (source[0].time_us, source[-1].time_us),
        "target": (target[0].time_us, target[-1].time_us),
    }
    target = [f for f in target if f.confidence > 0]
    source = [f for f in source if f.confidence > 0]
    folds = [fold(target, source, parity, bounds) for parity in (0, 1)]
    checks = [c for f in folds for c in f.pop("checks")]
    ordered: list[list[str]] = [[] for _ in range(SETTINGS["regions"])]
    for item in sorted(checks, key=lambda c: c["source_time_us"]):
        ordered[item["region"]].append(item["verdict"])
    verdicts = [region_verdict(region_checks) for region_checks in ordered]
    confirmed = [c for c in checks if c["verdict"] == "confirms"]
    pairs = [(c["source_time_us"], c["target_time_us"]) for c in confirmed]
    # Every confirmation shows coverage, but only exact ones fit and test the timing.
    anchors = [c for c in confirmed if c["exact"]]
    mapping = fit([(c["source_time_us"], c["target_time_us"]) for c in anchors])
    residuals = [0.0] * SETTINGS["regions"]
    if mapping:
        for item in anchors:
            residual = abs(item["target_time_us"] - predict(mapping, item["source_time_us"]))
            residuals[item["region"]] = max(residuals[item["region"]], residual)
    result: dict = {
        "status": "review_required",
        "folds": folds,
        "region_tallies": [dict(Counter(region_checks)) for region_checks in ordered],
        "region_verdicts": verdicts,
        "max_region_residual_us": residuals,
        "confirmed_pairs": len(pairs),
        "exact_anchors": len(anchors),
        "span_fraction": span_fraction(pairs, bounds),
        "frame_counts": {"target": len(target), "source": len(source)},
    }
    if mapping:
        result["mapping"] = mapping
    if reason := next((f["reason"] for f in folds if "reason" in f), None):
        return result | {"reason": reason}
    if separated(
        folds[0]["hypothesis"],
        folds[1]["hypothesis"],
        bounds["source"],
        SETTINGS["max_residual_us"],
    ):
        return result | {"reason": "folds_disagree"}
    if "contradicts" in verdicts:
        return result | {"reason": "contradicting_region"}
    if verdicts[0] != "confirms" or verdicts[-1] != "confirms":
        return result | {"reason": "unconfirmed_ends"}
    if verdicts.count("confirms") < CONSENSUS_SETTINGS["min_confirmed_regions"]:
        return result | {"reason": "insufficient_coverage"}
    if min(result["span_fraction"].values()) < SETTINGS["min_span_fraction"]:
        return result | {"reason": "insufficient_span"}
    if mapping is None or max(residuals) > SETTINGS["max_residual_us"]:
        return result | {"reason": "inconsistent_timeline"}
    return result | {"status": "proposed", "reason": "research_limits_met"}
