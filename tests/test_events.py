"""The append-only event history. No media, no network."""

import os

from conftest import audio, needed_plan, probe_data, read_events, subtitle, video
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


def test_record_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker / "under-a-file"))
    events.record("fixed", path="/a.mkv")


def make_plan(path: str) -> Plan:
    return needed_plan(path, incidental=["clear junk title"], incidental_rules={"junk_titles"})


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
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "all")
    monkeypatch.setattr(config, "DROP_COMMENTARY", True)
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)

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
    # Order is the one rule that reports only when nothing else does, so it
    # needs a file whose sole fault is the order of its streams.
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
    monkeypatch.setattr(config, "REMUX_TO_MKV", True)
    source = tmp_path / "f.mp4"
    source.write_bytes(b"x")
    stub_rewrite(make_plan(str(source)))

    processing.process(Job(str(source)), dry_run=False)

    (entry,) = read_events()
    assert entry["path"] == str(tmp_path / "f.mkv")
    assert entry["from_path"] == str(source)


def test_dry_webhook_import_records_a_would_fix_event(monkeypatch):
    """Under DRY_RUN an import leaves no other trace, so the handler records what
    would have happened."""
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
    assert summary["config"]["audio_codec"] == config.AUDIO_CODEC


def test_a_webhook_only_install_can_still_resolve_its_config_ids(monkeypatch):
    """An install that never sweeps writes no other line carrying the full
    fingerprint, so without serve's its digests point at nothing."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(processing, "build_plan", lambda p, lang: make_plan("/x.mkv"))
    started = Policy.from_config()
    events.record("config", config=started.fingerprint(), config_id=started.digest())

    webhook._handle(Job("/x.mkv"))

    startup, would_fix = read_events()
    assert startup["event"] == "config"
    assert would_fix["config_id"] == startup["config_id"]
    assert startup["config"]["audio_codec"] == config.AUDIO_CODEC


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
