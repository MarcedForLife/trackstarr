"""The append-only event history. No media, no network."""

from __future__ import annotations

import json
import os

from trackstarr import __version__, config, events, processing, webhook
from trackstarr.executor import Outcome
from trackstarr.planner import OutStream, Plan
from trackstarr.processing import Job
from trackstarr.sweep import sweep


def read_events() -> list[dict]:
    return list(events.read())


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


def test_record_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker / "under-a-file"))
    events.record("fixed", path="/a.mkv")


def test_read_spans_archives_oldest_first():
    """A hand-archived chunk is read before the live file: the service only
    appends to events.jsonl, but readers glob events*.jsonl."""
    events.record("fixed", path="/new.mkv")
    with open(os.path.join(config.STATE_DIR, "events-2025.jsonl"), "w") as archive:
        archive.write(json.dumps({"event": "fixed", "path": "/old.mkv"}) + "\n")

    assert [entry["path"] for entry in events.read()] == ["/old.mkv", "/new.mkv"]


def test_read_skips_junk_lines():
    events.record("fixed", path="/a.mkv")
    with open(events.path(), "a") as events_file:
        events_file.write("not json\n\n[1, 2]\n")
    events.record("fixed", path="/b.mkv")

    assert [entry["path"] for entry in events.read()] == ["/a.mkv", "/b.mkv"]


def test_read_without_history_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "never-created"))
    assert read_events() == []


def make_plan(path: str) -> Plan:
    return Plan(path=path, reasons=["reorder streams"], incidental=["clear junk title"])


def test_fixed_file_leaves_an_event(monkeypatch, tmp_path):
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x" * 10)
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan(str(path)))
    monkeypatch.setattr(processing, "apply_plan", lambda plan: (Outcome.APPLIED, ""))

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


def test_fixed_event_names_the_downmixes_created(monkeypatch, tmp_path):
    path = tmp_path / "f.mkv"
    path.write_bytes(b"x")
    plan = Plan(
        path=str(path),
        reasons=["add 2.0 downmix from stream 3 (6ch eng)"],
        streams=[
            OutStream(src=3, kind="audio"),
            # The planner titles every encode stream with its layout's name.
            OutStream(src=3, kind="audio", encode=True, channels=2, title="2.0"),
        ],
    )
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: plan)
    monkeypatch.setattr(processing, "apply_plan", lambda plan: (Outcome.APPLIED, ""))

    processing.process(Job(str(path)), dry_run=False)

    (entry,) = read_events()
    assert entry["downmixed"] == ["2.0"]


def test_failed_rewrite_leaves_an_event(monkeypatch):
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan("/x.mkv"))
    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: (Outcome.FAILED, "ffmpeg failed")
    )

    processing.process(Job("/x.mkv"), dry_run=False, source="sweep")

    (entry,) = read_events()
    assert entry["event"] == "failed"
    assert entry["source"] == "sweep"
    assert entry["detail"] == "ffmpeg failed"
    # What the rewrite was attempting, not just how it broke.
    assert entry["reasons"] == ["reorder streams"]


def test_sweep_dry_runs_and_deferrals_leave_no_events(monkeypatch):
    """A dry sweep re-derives the same verdicts nightly; recording them per
    file would drown the history in repeats. pending.tsv holds them."""
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan("/x.mkv"))
    processing.process(Job("/x.mkv"), dry_run=True, source="sweep")

    monkeypatch.setattr(
        processing, "apply_plan", lambda plan: (Outcome.DEFERRED, "source changed")
    )
    processing.process(Job("/x.mkv"), dry_run=False)

    assert read_events() == []


def test_dry_webhook_import_records_a_would_fix_event(monkeypatch):
    """Under DRY_RUN a webhook import leaves no other trace, so the webhook
    handler records what would have happened."""
    monkeypatch.setattr(config, "DRY_RUN", True)
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
    assert entry["config"]["audio_codec"] == config.AUDIO_CODEC
    assert entry["counts"]["fixed"] == 0
    assert entry["seconds"] >= 0


def test_sweep_events_share_a_run_id(monkeypatch, tmp_path):
    """A sweep's rewrites carry its start time as a run id, so one night's
    work groups together without timestamp window arithmetic."""
    root = tmp_path / "library"
    root.mkdir()
    (root / "f.mkv").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(root)])
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan(p))
    monkeypatch.setattr(processing, "apply_plan", lambda plan: (Outcome.APPLIED, ""))

    sweep(dry_run=False)

    fixed, summary = read_events()
    assert fixed["event"] == "fixed"
    assert fixed["source"] == "sweep"
    assert summary["event"] == "sweep"
    assert fixed["run"] == summary["run"]
    assert summary["library_bytes"] == 1
