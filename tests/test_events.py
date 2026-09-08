"""The append-only event history. No media, no network."""

import json
import os
from datetime import datetime

from conftest import (
    audio,
    needed_plan,
    probe_data,
    read_events,
    set_langs,
    set_layouts,
    set_rules,
    subtitle,
    video,
)
from trackstarr import __version__, config, events, policy, processing, webhook
from trackstarr.executor import Outcome
from trackstarr.planner import OutStream, Plan, new_plan, plan_from_probe
from trackstarr.policy import Policy
from trackstarr.processing import Job
from trackstarr.sweep import sweep


def test_record_appends_json_lines():
    events.record("fixed", path="/a.mkv")
    events.record("failed", path="/b.mkv", detail="boom")

    entries = read_events()
    assert [entry["event"] for entry in entries] == ["fixed", "failed"]
    assert entries[0]["path"] == "/a.mkv"
    assert entries[0]["version"] == __version__
    assert entries[1]["detail"] == "boom"
    # None means unknown or not applicable; the field is dropped, not null.
    events.record("fixed", bytes_after=None)
    assert "bytes_after" not in read_events()[-1]
    # ISO 8601 with an offset, so history survives timezone changes.
    assert "T" in entries[0]["ts"] and len(entries[0]["ts"]) > len("2026-01-01T00:00:00")


def test_a_run_id_is_a_timestamp_made_unique():
    """Webhook deliveries land within a second of each other, so uniqueness
    has to come from more than the clock; the timestamp keeps runs sortable."""
    first, second = events.run_id(), events.run_id()
    assert first != second
    assert datetime.fromisoformat(first.partition("#")[0])


def test_record_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker / "under-a-file"))
    events.record("fixed", path="/a.mkv")


def make_plan(path: str, **overrides) -> Plan:
    return needed_plan(
        path, incidental=["clear junk title"], incidental_rules={"junk_titles"}, **overrides
    )


def test_fixed_file_leaves_an_event(tmp_path, stub_rewrite):
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x" * 10)
    stub_rewrite(make_plan(str(path)))

    processing.process(Job(str(path)), dry_run=False)

    (entry,) = read_events()
    assert entry["event"] == "fixed"
    assert entry["source"] == "webhook"
    assert "run" not in entry
    assert entry["path"] == str(path)
    assert entry["reasons"] == ["reorder streams"]
    assert entry["incidental"] == ["clear junk title"]
    assert entry["bytes_before"] == 10
    assert entry["seconds"] >= 0
    # A plan with no encode streams downmixes nothing; the field is absent.
    assert "downmixed" not in entry
    # Likewise from_path: the rewrite landed on the file it started from.
    assert "from_path" not in entry


def test_events_name_their_rules_as_well_as_describing_them(tmp_path, stub_rewrite):
    """`reasons` gets reworded between releases, so a stats view reading only
    that would lose its history every time the wording moved."""
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    stub_rewrite(make_plan(str(path)))

    processing.process(Job(str(path)), dry_run=False)

    (entry,) = read_events()
    assert entry["rules"] == ["order"]
    assert entry["incidental_rules"] == ["junk_titles"]
    # Beside the prose, not instead of it: the log line and pending.tsv still
    # want a sentence.
    assert entry["reasons"] == ["reorder streams"]


def _named(path: str, *streams: dict, title: str = "") -> set[str]:
    """Every rule a plan for these streams names, however it named it."""
    plan = plan_from_probe(new_plan(path, "eng"), probe_data(*streams, title=title))
    return plan.rules | plan.incidental_rules


def test_the_rules_a_plan_can_name_are_exactly_the_vocabulary(monkeypatch):
    """Both directions matter. A key invented at a call site reaches the history
    as a name nothing else knows; one in RULE_NAMES that nothing emits
    promises a breakdown the data will never contain."""
    set_rules(
        monkeypatch,
        regenerate="always",
        commentary="always",
        remux="always",
    )
    # No mode of their own; the row is the switch.
    set_layouts(monkeypatch, "2.0", "5.1", "7.1:remove")
    set_langs(monkeypatch, "original", "eng")
    monkeypatch.setattr(config, "REGENERATE_SCOPE", "all")

    named = _named(
        "/x.mp4",
        video(0),
        # A junk title to clear, and a foreign track and a commentary to drop.
        audio(1, 8, "eng", "AC3 5.1 @ 640kbps"),
        audio(2, 2, "eng", "Director's Commentary"),
        audio(3, 6, "fre"),
        subtitle(4, "eng"),
        subtitle(5, "eng", "English SDH"),
        {"index": 6, "codec_type": "data"},
        title="Film 1080p BluRay",
    )
    # Cover art and a stale downmix of our own, which only Matroska carries
    # the tag for. Its 2.0 was made at a rate config no longer asks for.
    stale = audio(3, 2, "eng")
    stale["tags"]["TRACKSTARR"] = "aac 128k"
    named |= _named("/y.mkv", video(0), video(1, "mjpeg", attached_pic=1), audio(2, 8), stale)
    named |= _named("/z.mkv", video(0), audio(1, 2), subtitle(2), audio(3, 6))

    assert named == policy.RULE_NAMES


def test_fixed_event_names_the_downmixes_created(tmp_path, stub_rewrite):
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    stub_rewrite(
        Plan(
            path=str(path),
            reasons=["add 2.0 downmix from stream 3 (6ch eng)"],
            streams=[
                OutStream(src=3, kind="audio"),
                # The planner titles every encode stream with its layout's name.
                OutStream(src=3, kind="audio", encode=True, channels=2, title="2.0"),
            ],
        )
    )

    processing.process(Job(str(path)), dry_run=False)

    (entry,) = read_events()
    assert entry["downmixed"] == ["2.0"]


def test_failed_rewrite_leaves_an_event(stub_rewrite):
    stub_rewrite(make_plan("/x.mkv"), Outcome.FAILED, "ffmpeg failed")

    processing.process(Job("/x.mkv"), dry_run=False, source="sweep")

    (entry,) = read_events()
    assert entry["event"] == "failed"
    assert entry["source"] == "sweep"
    assert entry["detail"] == "ffmpeg failed"
    # What the rewrite was attempting, not just how it broke.
    assert entry["reasons"] == ["reorder streams"]


def test_a_dry_sweep_leaves_no_per_file_events(stub_rewrite):
    """A dry sweep re-derives the same verdicts nightly, which would drown the
    history in repeats. pending.tsv holds them."""
    stub_rewrite(make_plan("/x.mkv"))
    processing.process(Job("/x.mkv"), dry_run=True, source="sweep")

    assert read_events() == []


def test_deferred_rewrite_leaves_an_event(stub_rewrite):
    """One deferral is a benign race, but a file that defers every pass has
    nothing else to show for it: the sweep summary counts deferrals without
    naming the file."""
    stub_rewrite(make_plan("/x.mkv"), Outcome.DEFERRED, "source changed")

    processing.process(Job("/x.mkv"), dry_run=False, source="sweep")

    (entry,) = read_events()
    assert entry["event"] == "deferred"
    assert entry["path"] == "/x.mkv"
    assert entry["detail"] == "source changed"
    # What it was going to do, so a file stuck deferring can be judged.
    assert entry["reasons"] == ["reorder streams"]


def test_a_remux_event_names_the_file_it_replaced(tmp_path, monkeypatch, stub_rewrite):
    """A remux publishes an .mkv and deletes the source, so without this the
    history says an .mkv was fixed and nothing records the .mp4 it was."""
    source = tmp_path / "f.mp4"
    source.write_bytes(b"x")
    stub_rewrite(make_plan(str(source), remuxing=True))

    processing.process(Job(str(source)), dry_run=False)

    (entry,) = read_events()
    assert entry["path"] == str(tmp_path / "f.mkv")
    assert entry["from_path"] == str(source)


def test_reported_webhook_import_records_a_would_fix_event(monkeypatch):
    """In report mode an import leaves no other trace, so the handler records
    what would have happened."""
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan("/x.mkv"))
    webhook._handle(Job("/x.mkv"))

    (entry,) = read_events()
    assert entry["event"] == "would-fix"
    assert entry["source"] == "webhook"
    assert entry["path"] == "/x.mkv"
    assert entry["reasons"] == ["reorder streams"]
    assert entry["incidental"] == ["clear junk title"]


def test_sweep_leaves_a_summary_event(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "empty")])
    os.makedirs(tmp_path / "empty")

    sweep(dry_run=True)

    (entry,) = read_events()
    assert entry["event"] == "sweep"
    assert entry["dry_run"] is True
    assert entry["files"] == 0
    assert entry["library_bytes"] == 0
    assert entry["config"]["audio_layouts"] == ["2.0:downmix:aac:320k", "5.1:downmix:ac3:640k"]
    assert entry["counts"]["fixed"] == 0
    assert entry["seconds"] >= 0


def test_a_rewrites_config_id_resolves_against_the_sweeps_config(
    monkeypatch, tmp_path, stub_rewrite
):
    """`version` alone cannot say what the rules were (two installs on one
    release rewrite differently), so every event carries a digest of them."""
    root = tmp_path / "library"
    root.mkdir()
    (root / "f.mkv").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    stub_rewrite(make_plan(str(root / "f.mkv")))

    sweep(dry_run=False)

    fixed, summary = read_events()
    assert fixed["config_id"] == summary["config_id"]
    assert summary["config"]["audio_layouts"] == [
        "2.0:downmix:aac:320k",
        "5.1:downmix:ac3:640k",
    ]


def test_a_webhook_only_install_can_still_resolve_its_config_ids(monkeypatch):
    """An install that never sweeps writes no other line carrying the full
    fingerprint, so without serve's its digests point at nothing."""
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan("/x.mkv"))
    started = Policy.from_config()
    events.record("config", config=started.fingerprint(), config_id=started.digest())

    webhook._handle(Job("/x.mkv"))

    startup, would_fix = read_events()
    assert startup["event"] == "config"
    assert would_fix["config_id"] == startup["config_id"]
    assert startup["config"]["audio_layouts"] == [
        "2.0:downmix:aac:320k",
        "5.1:downmix:ac3:640k",
    ]


def test_a_restart_under_unchanged_rules_records_nothing(monkeypatch):
    """The digest already resolves against the line that recorded it, so a
    second copy would only put a "rules applied" nobody applied in the feed."""
    assert events.record_config(Policy.from_config()) is True
    assert events.record_config(Policy.from_config()) is False

    (entry,) = read_events()
    assert entry["event"] == "config"

    set_rules(monkeypatch, commentary="always")
    assert events.record_config(Policy.from_config()) is True
    first, second = read_events()
    assert first["config_id"] != second["config_id"]


def test_a_sweeps_fingerprint_pins_the_rules_for_the_next_restart(monkeypatch, tmp_path):
    """A sweep summary carries the whole fingerprint, so the digest it puts on
    record resolves without a config line repeating it."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "empty")])
    os.makedirs(tmp_path / "empty")

    sweep(dry_run=True)

    assert events.record_config(Policy.from_config()) is False
    (entry,) = read_events()
    assert entry["event"] == "sweep"


def test_rules_older_than_the_lookback_are_recorded_again(monkeypatch):
    """A busy install can put thousands of rewrites between two restarts.
    Searching all of them for a line the next sweep writes anyway is not worth
    it, so falling off the window costs a duplicate, not a wrong digest."""
    assert events.record_config(Policy.from_config()) is True
    for _ in range(events._CONFIG_LOOKBACK):
        events.record("fixed", path="/a.mkv")

    assert events.record_config(Policy.from_config()) is True
    first, last = read_events()[0], read_events()[-1]
    assert last["event"] == "config"
    assert last["config_id"] == first["config_id"]


def test_sweep_events_share_a_run_id(monkeypatch, tmp_path, stub_rewrite):
    """A sweep's rewrites carry its start time, so one night's work groups without
    timestamp window arithmetic."""
    root = tmp_path / "library"
    root.mkdir()
    (root / "f.mkv").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    stub_rewrite(make_plan(str(root / "f.mkv")))

    sweep(dry_run=False)

    fixed, summary = read_events()
    assert fixed["event"] == "fixed"
    assert fixed["source"] == "sweep"
    assert summary["event"] == "sweep"
    assert fixed["run"] == summary["run"]
    assert summary["library_bytes"] == 1


def test_read_answers_newest_first():
    """The feed's first screen is what just happened, so the newest line is
    the one the walk starts from."""
    for name in "abc":
        events.record("fixed", path=f"/{name}.mkv")

    entries, cursor = events.read()

    assert [entry["path"] for entry in entries] == ["/c.mkv", "/b.mkv", "/a.mkv"]
    # The whole history fit in the page, so there is nothing older to ask for.
    assert cursor is None


def test_read_pages_backwards_through_the_history():
    for index in range(5):
        events.record("fixed", path=f"/{index}.mkv")

    first, cursor = events.read(limit=2)
    assert [entry["path"] for entry in first] == ["/4.mkv", "/3.mkv"]

    second, cursor = events.read(limit=2, before=cursor)
    assert [entry["path"] for entry in second] == ["/2.mkv", "/1.mkv"]

    last, cursor = events.read(limit=2, before=cursor)
    assert [entry["path"] for entry in last] == ["/0.mkv"]
    # A short page means the walk ran out of file, so the feed stops here
    # rather than asking for a page it would get empty for ever.
    assert cursor is None


def test_a_page_ending_on_the_oldest_event_asks_for_no_more():
    """A full page can still be the last one, when the file holds exactly as
    many events as the page has room for."""
    events.record("fixed", path="/a.mkv")
    events.record("fixed", path="/b.mkv")

    entries, cursor = events.read(limit=2)

    assert [entry["path"] for entry in entries] == ["/b.mkv", "/a.mkv"]
    assert cursor is None


def test_read_walks_back_across_chunk_boundaries(monkeypatch):
    """A page usually arrives in one read; a long history takes several, and a
    line straddling the boundary must be neither halved nor dropped."""
    monkeypatch.setattr(events, "_CHUNK", 8)
    for index in range(20):
        events.record("fixed", path=f"/{index}.mkv")

    entries, cursor = events.read()

    assert [entry["path"] for entry in entries] == [f"/{i}.mkv" for i in reversed(range(20))]
    assert cursor is None


def test_read_steps_over_damage_rather_than_stopping():
    """STATE_DIR is a volume anyone can edit, and a read racing an append sees
    the newest line half written. Neither may hide the history behind it."""
    events.record("fixed", path="/a.mkv")
    with open(events.path(), "a") as history:
        history.write("half a line, no closing brace\n")
        history.write("[1, 2, 3]\n")
        history.write('{"event": "fixed", "path": "/b.mkv"}\n')

    entries, _ = events.read()

    assert [entry["path"] for entry in entries] == ["/b.mkv", "/a.mkv"]


def test_a_cursor_past_the_end_of_the_history_is_clamped():
    """Nothing truncates the file today, but a cursor is a number a caller can
    spell for itself, and one past the end must not read the page as empty."""
    events.record("fixed", path="/a.mkv")

    entries, cursor = events.read(before=10_000_000)

    assert [entry["path"] for entry in entries] == ["/a.mkv"]
    assert cursor is None


def at(ts: str, path: str) -> None:
    """One event at a stated moment, for the window tests, since record()
    stamps with the clock."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(events.path(), "a") as history:
        history.write(json.dumps({"ts": ts, "event": "fixed", "path": path}) + "\n")


def test_read_stops_at_the_near_edge_of_a_window():
    """The file is written in order and walked backwards, so the first line
    older than `since` is the end of the read rather than one line to skip."""
    for day in range(1, 6):
        at(f"2026-03-0{day}T12:00:00+00:00", f"/{day}.mkv")

    entries, cursor = events.read(since=events.moment("2026-03-03T00:00:00+00:00"))

    assert [entry["path"] for entry in entries] == ["/5.mkv", "/4.mkv", "/3.mkv"]
    # Nothing older *in range*, which is the same answer to the feed as the
    # beginning of the file: there is no page after this one to ask for.
    assert cursor is None


def test_a_full_page_cut_short_by_since_still_asks_for_no_more():
    """A window's last page can fill exactly, and a cursor handed back for it
    would fetch a page of lines the window has already ruled out."""
    for day in range(1, 6):
        at(f"2026-03-0{day}T12:00:00+00:00", f"/{day}.mkv")

    entries, cursor = events.read(limit=2, since=events.moment("2026-03-04T00:00:00+00:00"))

    assert [entry["path"] for entry in entries] == ["/5.mkv", "/4.mkv"]
    assert cursor is None


def test_read_walks_past_the_far_edge_of_a_window():
    """`until` costs the scan `since` does not: an append-only file cannot be
    asked for the offset of a moment, so the newer lines are stepped over."""
    for day in range(1, 6):
        at(f"2026-03-0{day}T12:00:00+00:00", f"/{day}.mkv")

    entries, cursor = events.read(until=events.moment("2026-03-03T00:00:00+00:00"))

    assert [entry["path"] for entry in entries] == ["/2.mkv", "/1.mkv"]
    assert cursor is None


def test_a_window_pages_backwards_like_any_other_read():
    """The cursor stays a byte offset inside a window, so the second page of
    a custom range costs what the second page of the history costs."""
    for day in range(1, 7):
        at(f"2026-03-0{day}T12:00:00+00:00", f"/{day}.mkv")

    window = {
        "since": events.moment("2026-03-02T00:00:00+00:00"),
        "until": events.moment("2026-03-05T00:00:00+00:00"),
    }
    first, cursor = events.read(limit=2, **window)
    assert [entry["path"] for entry in first] == ["/4.mkv", "/3.mkv"]
    assert cursor is not None

    second, cursor = events.read(limit=2, before=cursor, **window)
    assert [entry["path"] for entry in second] == ["/2.mkv"]
    assert cursor is None


def test_a_window_compares_moments_rather_than_the_text_of_a_stamp():
    """Two stamps either side of a daylight-saving change carry different
    offsets, and the text then sorts in an order the clock disagrees with."""
    at("2026-01-01T00:30:00+01:00", "/before.mkv")  # 23:30 UTC
    at("2025-12-31T23:45:00+00:00", "/after.mkv")  # 23:45 UTC, and later

    entries, _ = events.read(since=events.moment("2025-12-31T23:40:00+00:00"))

    assert [entry["path"] for entry in entries] == ["/after.mkv"]


def test_a_stamp_that_will_not_parse_survives_a_window():
    """A line nobody can date is not evidence it falls outside one, and the
    history outlives the build that wrote it."""
    at("2026-03-01T12:00:00+00:00", "/dated.mkv")
    at("the day before yesterday", "/undated.mkv")

    entries, _ = events.read(since=events.moment("2026-03-05T00:00:00+00:00"))

    assert [entry["path"] for entry in entries] == ["/undated.mkv"]


def test_a_moment_with_no_offset_is_read_in_the_service_clock():
    """That is the clock the history was written by, so it is the one a
    stamp saying nothing about its own should be read in."""
    assert events.moment("2026-03-01T12:00:00").tzinfo is not None
    assert events.moment("2026-03-01T12:00:00") == datetime(2026, 3, 1, 12).astimezone()


def test_reading_a_history_that_is_not_there_is_an_empty_page():
    """An install that has decided nothing yet, which is every install on its
    first morning."""
    assert events.read() == ([], None)


def test_reading_an_empty_history_is_an_empty_page():
    os.makedirs(config.STATE_DIR)
    with open(events.path(), "w") as history:
        history.write("")

    assert events.read() == ([], None)


def test_read_never_raises():
    """The feed is worth less than the request it hangs off; an unreadable
    history answers empty rather than 500."""
    os.makedirs(events.path())

    assert events.read() == ([], None)
