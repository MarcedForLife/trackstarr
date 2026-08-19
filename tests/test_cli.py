"""Command line startup checks. No media, no network."""

import os
import signal
import time
from dataclasses import replace

import pytest

from conftest import needed_plan
from trackstarr import auth, config
from trackstarr.arr import LibraryItem, radarr
from trackstarr.cli import handle_sigterm, main
from trackstarr.media import ProbeError
from trackstarr.planner import OutStream, Plan
from trackstarr.policy import Policy
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


def test_sigterm_ends_the_process():
    """The container runs this as PID 1, where the kernel applies no default
    action, and Python installs no handler of its own. Without one `docker
    stop` is ignored for its whole grace period and then SIGKILLs."""
    handle_sigterm()
    with pytest.raises(SystemExit) as stopped:
        os.kill(os.getpid(), signal.SIGTERM)
        # Delivery is not synchronous with the kill; the handler runs at the
        # next bytecode boundary, and sleeping is interrupted by it.
        time.sleep(5)
    assert stopped.value.code == 128 + signal.SIGTERM


def test_every_command_installs_the_sigterm_handler(startup_ok):
    """A stop must be prompt whatever is running, not only serve: a sweep
    holds rewrite slots that another process is waiting on."""
    before = signal.getsignal(signal.SIGTERM)
    main(["plan", "--original", "eng", "/nowhere/missing.mkv"])
    assert signal.getsignal(signal.SIGTERM) is not before


def test_an_unusable_state_dir_stops_a_rewriting_command(
    startup_ok, monkeypatch, tmp_path, caplog
):
    """serve would otherwise hit this as a traceback from a restart loop,
    since it takes a slot lock before it binds the listener. Asserted on the
    message, not the exit code: a fix of a missing file exits 1 anyway, so
    the code alone would pass with the check unwired."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker))
    assert main(["fix", "--original", "eng", "/lib/a.mkv"]) == 1
    assert "STATE_DIR" in caplog.text
    assert "not usable" in caplog.text


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
        "/lib/a.mkv": ProcessResult(Status.FIXED, needed_plan("/lib/a.mkv")),
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
    index = {"/lib/Movie": LibraryItem("kor", 7, radarr())}
    monkeypatch.setattr("trackstarr.cli.path_index", lambda arrs: index)
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


def _planned(monkeypatch, plan):
    """Make cmd_plan see one prepared plan instead of probing a real file."""
    monkeypatch.setattr("trackstarr.cli.build_plan", lambda path, lang: plan)


def test_plan_prints_the_policy_it_judged_under(startup_ok, monkeypatch, capsys):
    """The summary is how a user works out why a file was left alone, so the
    policy has to be on screen beside the verdict, not inferred from env."""
    plan = Plan(path="f.mkv", original_lang="jpn", keep_langs={"jpn", "eng"})
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "original language : jpn" in out
    assert "keeping languages : eng, jpn" in out
    # Every configured layout, with the bitrate each would be encoded at.
    assert "downmix layouts" in out
    assert "conforms, no action" in out


def test_plan_omits_the_rules_that_are_off(startup_ok, monkeypatch, capsys):
    """Blank rows are dropped, so the summary stays as short as the policy."""
    policy = Policy.from_config()
    plan = Plan(path="f.mkv", policy=replace(policy, drop_commentary=False, remux_to_mkv=False))
    _planned(monkeypatch, plan)

    main(["plan", "f.mkv"])
    out = capsys.readouterr().out
    assert "drop commentary" not in out
    assert "remux to mkv" not in out


def test_plan_prints_a_skip_and_stops_there(startup_ok, monkeypatch, capsys):
    plan = Plan(path="f.mkv", skip="hardlinked, left for the download client")
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "SKIP: hardlinked" in out
    assert "ffmpeg" not in out


def test_plan_prints_the_reasons_and_the_command(startup_ok, monkeypatch, capsys):
    """The printed ffmpeg line is the tool's showing of its work; it has to be
    the command that would really run, not a summary of it."""
    plan = Plan(path="f.mkv", reasons=["drop audio jpn"], incidental=["strip junk title"])
    plan.streams.append(OutStream(src=0, kind="video"))
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "- drop audio jpn" in out
    assert "rides along, never triggers on its own" in out
    # The real command, not a summary of it: the stream map is the part a user
    # would copy out to run by hand.
    assert "ffmpeg -hide_banner -nostdin -y -loglevel error -i f.mkv -map 0:0" in out
    # The staged name is a placeholder, never a path in the library.
    assert out.rstrip().endswith("OUT.mkv")


def test_plan_keeps_going_after_an_unreadable_file(startup_ok, monkeypatch, capsys):
    """One corrupt file in a directory must not hide the rest."""
    seen: list[str] = []

    def build(path, lang):
        seen.append(path)
        if path == "bad.mkv":
            raise ProbeError("moov atom not found")
        return Plan(path=path)

    monkeypatch.setattr("trackstarr.cli.build_plan", build)
    assert main(["plan", "bad.mkv", "good.mkv"]) == 1
    assert seen == ["bad.mkv", "good.mkv"]
    assert "ERROR moov atom not found" in capsys.readouterr().out


def test_missing_ffmpeg_stops_a_command_before_it_starts(monkeypatch, caplog):
    """Every command shells out to ffprobe at least; saying so once beats a
    subprocess error per file."""
    monkeypatch.setattr("trackstarr.cli.shutil.which", lambda name: None)
    assert main(["plan", "f.mkv"]) == 1
    assert "ffmpeg and ffprobe must be on PATH" in caplog.text


def test_sweep_exit_code_ignores_deferrals_but_not_failures(startup_ok, monkeypatch):
    """Opposite of fix: a sweep's own next run is the retry, so a deferral is
    not the caller's problem."""
    counts = dict.fromkeys(Status, 0)
    monkeypatch.setattr("trackstarr.cli.sweep", lambda dry_run: counts)

    counts[Status.DEFERRED] = 3
    assert main(["sweep"]) == 0
    counts[Status.FAILED] = 1
    assert main(["sweep"]) == 1


def test_fix_says_so_when_dry_run_is_set(startup_ok, monkeypatch, capsys):
    """fix is the command that writes, so a global DRY_RUN has to be stated:
    otherwise it reports "conforms" for files it never touched."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(
        "trackstarr.cli.process", lambda job, dry_run, source: ProcessResult(Status.CONFORM)
    )
    main(["fix", "f.mkv"])
    assert "DRY_RUN is set" in capsys.readouterr().out


def test_fix_prints_why_a_file_was_skipped(startup_ok, monkeypatch, capsys):
    """ "SKIP" alone reads like a failure; the reason is what tells the user
    nothing is wrong."""
    plan = Plan(path="f.mkv", skip="hardlinked, left for the download client")
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: ProcessResult(Status.SKIP, plan),
    )
    assert main(["fix", "f.mkv"]) == 0
    assert "hardlinked, left for the download client" in capsys.readouterr().out


def test_secret_reports_a_state_dir_it_cannot_write(monkeypatch, caplog):
    """A read-only or unmounted /config is the usual cause, and the user needs
    to be told that rather than shown a traceback."""

    def refuse(name):
        raise OSError("read-only file system")

    monkeypatch.setattr("trackstarr.cli.auth.mint", refuse)
    assert main(["secret", "radarr"]) == 1
    assert "could not store the secret" in caplog.text
