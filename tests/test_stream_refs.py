"""Input coordinates must not confuse imported streams with target streams."""

from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from trackstarr.command import ffmpeg_args
from trackstarr.executor import Outcome, apply_plan
from trackstarr.planner import (
    OutStream,
    Plan,
    StreamRef,
    changes,
    planned_tracks,
    track_changes,
)
from trackstarr.processing import _tag_only


def test_legacy_constructors_and_replacement_share_one_reference():
    legacy = OutStream(3, "audio", channels=6)
    explicit = OutStream(source=StreamRef(0, 3), kind="audio", channels=6)
    assert legacy == explicit == OutStream(src=3, kind="audio", channels=6)
    assert explicit.src == 3
    assert "src" not in asdict(explicit)
    assert OutStream(src=3, source=StreamRef(0, 3), kind="audio") == OutStream(3, "audio")
    imported = replace(explicit, source=StreamRef(1, 3))
    assert imported.source == StreamRef(1, 3)
    assert explicit.source == StreamRef(0, 3)
    with pytest.raises(FrozenInstanceError):
        imported.source.input_index = 2
    imported.src = 4
    assert imported.source == StreamRef(1, 4)
    assert explicit.src == 3


@pytest.mark.parametrize("reference", [(-1, 0), (0, -1)])
def test_negative_coordinates_are_refused(reference):
    with pytest.raises(ValueError, match="non-negative"):
        StreamRef(*reference)


def test_missing_or_conflicting_legacy_reference_is_refused():
    with pytest.raises(TypeError, match="requires source or src"):
        OutStream(kind="audio")
    with pytest.raises(ValueError, match="different streams"):
        OutStream(src=3, source=StreamRef(1, 3), kind="audio")


def test_stream_and_metadata_maps_use_the_same_input_coordinates():
    plan = Plan(
        "target.mkv",
        streams=[
            OutStream(source=StreamRef(0, 1), kind="audio"),
            OutStream(source=StreamRef(1, 1), kind="audio"),
            OutStream(
                source=StreamRef(2, 1),
                kind="audio",
                encode=True,
                channels=2,
                codec="aac",
                bitrate="192k",
            ),
        ],
    )
    args = ffmpeg_args(plan, "output.mkv")
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "-map"] == [
        "0:1",
        "1:1",
        "2:1",
    ]
    assert [args[args.index(f"-map_metadata:s:{i}") + 1] for i in range(3)] == [
        "0:s:1",
        "1:s:1",
        "-1",
    ]
    assert args[args.index("-map_chapters") + 1] == "0"


def test_import_cannot_inherit_target_metadata_or_hide_a_target_drop():
    plan = Plan(
        "target.mkv",
        tracks=[{"index": 1, "kind": "audio", "lang": "eng", "codec": "ac3"}],
        streams=[OutStream(source=StreamRef(1, 1), kind="audio")],
    )
    assert planned_tracks(plan) == [
        {
            "index": 0,
            "kind": "audio",
            "source": {"input_index": 1, "stream_index": 1},
        }
    ]
    assert track_changes(plan) == {"dropped": [1], "added": [0]}
    tally = changes(planned_tracks(plan), plan.tracks)
    assert tally.adds == ["und audio"]
    assert tally.drops == 1


def test_target_projection_retains_legacy_index_and_explicit_source():
    plan = Plan(
        "target.mkv",
        tracks=[{"index": 4, "kind": "audio", "lang": "fra", "codec": "ac3"}],
        streams=[OutStream(source=StreamRef(0, 4), kind="audio")],
    )
    (track,) = planned_tracks(plan)
    assert track["src"] == 4
    assert track["source"] == {"input_index": 0, "stream_index": 4}
    assert track["lang"] == "fra"
    assert changes([track], plan.tracks).drops == 0
    assert track_changes(plan) == {}


def test_explicit_source_takes_precedence_over_a_legacy_index():
    tracks = [{"index": 1, "kind": "audio"}, {"index": 2, "kind": "audio"}]
    planned = [{"src": 1, "source": {"input_index": 0, "stream_index": 2}}]
    assert changes(planned, tracks).drops == 1
    assert changes([{"src": 1}, {"src": 2}], tracks).drops == 0


def test_imported_encode_is_an_addition_even_when_it_matches_a_dropped_layout():
    track = {
        "index": 1,
        "kind": "audio",
        "channels": 2,
        "lang": "fra",
        "title": "French",
    }
    imported = track | {
        "source": {"input_index": 1, "stream_index": 1},
        "flags": ["generated"],
    }
    tally = changes([imported], [track])
    assert tally.adds == ["French"]
    assert tally.rebuilds == []
    assert tally.drops == 1


def test_external_references_cannot_execute_before_source_preparation_exists(tmp_path):
    path = tmp_path / "target.mkv"
    path.write_bytes(b"unchanged")
    plan = Plan(str(path), streams=[OutStream(source=StreamRef(1, 0), kind="audio")])
    outcome, detail = apply_plan(plan)
    assert outcome == Outcome.FAILED
    assert "preparation" in detail
    assert path.read_bytes() == b"unchanged"
    assert list(tmp_path.iterdir()) == [path]


def test_external_reference_disables_in_place_tagging_even_after_a_local_tag():
    plan = Plan(
        "target.mkv",
        rules={"tag_original"},
        streams=[
            OutStream(source=StreamRef(0, 1), kind="audio", lang="jpn"),
            OutStream(source=StreamRef(1, 1), kind="audio", lang="fra"),
        ],
    )
    assert _tag_only(plan) is None
