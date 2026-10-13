"""Main-audio requirements against a local plan, without discovery or file reads."""

from dataclasses import dataclass

from .planner import Plan
from .policy import ALONGSIDE, ALWAYS


@dataclass(frozen=True, slots=True)
class AudioRequirement:
    """One LANGUAGES language as main audio. Satisfied describes the local
    plan's output, not a completed rewrite."""

    lang: str
    satisfied: bool


def requirements(plan: Plan) -> tuple[AudioRequirement, ...]:
    """Assess retained main audio before selecting any external tracks.

    Alongside requirements remain visible without a rewrite. This check never
    adds rewrite reasons or authorises copying. The caller supplies a probed,
    local-only plan so imported audio cannot satisfy its own prerequisite.
    """
    if "source_audio" not in plan.policy.rules_in(ALWAYS, ALONGSIDE):
        return ()
    if plan.inputs:
        raise ValueError("audio requirements need a local-only plan")
    tracks = {track["index"]: track for track in plan.tracks}
    present = set()
    for stream in plan.streams:
        if stream.kind != "audio":
            continue
        track = tracks.get(stream.source.stream_index, {})
        if "commentary" in track.get("flags", ()):
            continue
        if language := stream.lang or track.get("lang"):
            present.add(language)
    return tuple(AudioRequirement(lang.name, lang.name in present) for lang in plan.langs)
