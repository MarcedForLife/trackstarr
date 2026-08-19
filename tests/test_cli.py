"""Command line startup checks. No media, no network."""

from __future__ import annotations

import pytest

from trackstarr import auth, config
from trackstarr.arr import LibraryItem, radarr
from trackstarr.cli import main
from trackstarr.media import ProbeError
from trackstarr.planner import Plan
from trackstarr.processing import ProcessResult
from trackstarr.status import Status


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        # A typo would silently leave the rule on.
        ("DISABLED_RULES", {"langauges"}),
        # A typo would silently drop the layout.
        ("DOWNMIX_LAYOUTS", {"surround"}),
        # A typo (or a bare "true") would silently regenerate nothing.
        ("REGENERATE_DOWNMIXES", "true"),
    ],
)
def test_bad_config_fails_fast(monkeypatch, setting, value):
    monkeypatch.setattr(config, setting, value)
    assert main(["plan", "f.mkv"]) == 1


@pytest.fixture
def startup_ok(monkeypatch, tmp_path):
    """Pass main()'s environment checks: ffmpeg "on PATH", writable WORK_DIR."""
    monkeypatch.setattr("trackstarr.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))


def test_plan_fails_when_a_file_cannot_be_read(startup_ok, capsys):
    """A script watching the exit code must see a probe failure."""
    assert main(["plan", "--original", "eng", "/nowhere/missing.mkv"]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_plan_normalises_the_original_language(startup_ok, monkeypatch):
    """Stream tags are ISO 639-2/B, so a 639-1 flag value must become one
    too or the plan drops the very language it was told to keep."""
    seen = []

    def capture_lang(path, lang):
        seen.append(lang)
        raise ProbeError("stop before probing")

    monkeypatch.setattr("trackstarr.cli.build_plan", capture_lang)
    main(["plan", "--original", "ja", "f.mkv"])
    assert seen == ["jpn"]


def test_fix_rewrites_files_and_reports_failures(startup_ok, monkeypatch, capsys):
    """One real rewrite path per file, through process() like every other
    source, and a non-zero exit when any of them was not rewritten. A
    deferral counts: the sweep's next run is its retry, a fix's retry is
    the caller, who must not read it as success."""
    results = {
        "/lib/a.mkv": ProcessResult(
            Status.FIXED, Plan(path="/lib/a.mkv", reasons=["reorder streams"])
        ),
        "/lib/b.mkv": ProcessResult(Status.FAILED, detail="ffmpeg failed (1): boom"),
        "/lib/c.mkv": ProcessResult(Status.DEFERRED, detail="source changed"),
    }
    calls = []

    def fake_process(job, dry_run, source):
        calls.append((job.path, job.lang, dry_run, source))
        return results[job.path]

    monkeypatch.setattr("trackstarr.cli.process", fake_process)
    assert main(["fix", "--original", "en", "/lib/a.mkv", "/lib/b.mkv", "/lib/c.mkv"]) == 1

    out = capsys.readouterr().out
    assert "reorder streams" in out
    assert "ffmpeg failed" in out
    assert "source changed" in out
    # The flag is normalised and the rewrite is a real one, labelled cli.
    assert calls == [
        ("/lib/a.mkv", "eng", False, "cli"),
        ("/lib/b.mkv", "eng", False, "cli"),
        ("/lib/c.mkv", "eng", False, "cli"),
    ]


def test_fix_still_matches_arr_items_when_original_is_given(startup_ok, monkeypatch):
    """--original overrides the language only. The item match must survive,
    or the *arr never gets its rescan and keeps pre-rewrite media info."""
    item = LibraryItem("/lib/Movie", "kor", 7, radarr())
    monkeypatch.setattr("trackstarr.cli.path_index", lambda arrs: [item])
    jobs = []
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: jobs.append(job) or ProcessResult(Status.CONFORM),
    )

    assert main(["fix", "--original", "ja", "/lib/Movie/Movie.mkv"]) == 0
    (job,) = jobs
    assert (job.lang, job.item_id, job.arr.name) == ("jpn", 7, "radarr")


def test_fix_exit_code_flags_a_deferral_alone(startup_ok, monkeypatch):
    """Deferred means not rewritten. A one-shot command's retry is the
    caller, so it must not exit 0 even when nothing outright failed."""
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: ProcessResult(Status.DEFERRED, detail="source changed"),
    )
    assert main(["fix", "--original", "en", "/lib/c.mkv"]) == 1


def test_secret_prints_a_credential_once(capsys):
    """No startup_ok: minting must work on a host without ffmpeg."""
    assert main(["secret", "my-scanner"]) == 0
    minted = capsys.readouterr().out.strip()
    assert minted
    assert auth.authorized(minted)


def test_secret_will_not_replace_a_working_one_by_accident(capsys):
    """Only a digest is kept, so a second run cannot reprint. Rotating
    silently would lock out a client that was working, so it must be asked
    for."""
    assert main(["secret", "my-scanner"]) == 0
    minted = capsys.readouterr().out.strip()

    assert main(["secret", "my-scanner"]) == 1
    assert capsys.readouterr().out == ""
    assert auth.authorized(minted)


def test_secret_rotate_replaces_the_old_credential(capsys):
    assert main(["secret", "my-scanner"]) == 0
    minted = capsys.readouterr().out.strip()

    assert main(["secret", "my-scanner", "--rotate"]) == 0
    rotated = capsys.readouterr().out.strip()
    assert rotated != minted
    assert auth.authorized(rotated)
    assert not auth.authorized(minted)


def test_secret_refuses_a_path_shaped_name():
    assert main(["secret", "../escape"]) == 1
