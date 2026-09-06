"""Command line startup checks. No media, no network."""

import logging
import os
import signal
import time

import pytest

from conftest import needed_plan, set_rules
from trackstarr import auth, config, policy, sessions, sweep_cache, users
from trackstarr.arr import LibraryIndex, LibraryItem, radarr
from trackstarr.cli import handle_sigterm, main
from trackstarr.media import ProbeError
from trackstarr.planner import OutStream, Plan
from trackstarr.policy import Policy
from trackstarr.processing import ProcessResult
from trackstarr.status import Status
from trackstarr.sweep_cache import read


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        # A typo would silently leave the rule doing what it did before.
        ("RULE_MODES", {"langauges": "never"}),
        # A typo would silently drop the layout.
        ("DOWNMIX_LAYOUTS", ("surround",)),
        # A typo (or a bare "true") would silently regenerate nothing.
        ("REGENERATE_SCOPE", "true"),
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
    action and Python installs no handler. Without one, `docker stop` is
    ignored for its whole grace period and then SIGKILLs."""
    handle_sigterm()
    with pytest.raises(SystemExit) as stopped:
        os.kill(os.getpid(), signal.SIGTERM)
        # Delivery is not synchronous with the kill; the handler runs at the
        # next bytecode boundary, and sleeping is interrupted by it.
        time.sleep(5)
    assert stopped.value.code == 128 + signal.SIGTERM


def test_every_command_installs_the_sigterm_handler(startup_ok):
    """A stop has to be prompt whatever is running: a sweep holds rewrite slots
    another process is waiting on."""
    before = signal.getsignal(signal.SIGTERM)
    main(["plan", "--original", "eng", "/nowhere/missing.mkv"])
    assert signal.getsignal(signal.SIGTERM) is not before


def test_an_unusable_state_dir_stops_a_rewriting_command(
    startup_ok, monkeypatch, tmp_path, caplog
):
    """Otherwise a traceback from a restart loop. Asserted on the message: a fix
    of a missing file exits 1 anyway."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker))
    assert main(["fix", "--original", "eng", "/lib/a.mkv"]) == 1
    assert "STATE_DIR" in caplog.text
    assert "not usable" in caplog.text


def test_a_missing_media_dir_is_reported_at_startup(startup_ok, monkeypatch, tmp_path, caplog):
    """The mistake the README singles out, and a quiet one: walk_library says so
    as it walks, which under serve is whenever SWEEP_AT next fires, and with
    no schedule is never. Startup is where it can still be acted on."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "wrong-mount")])
    monkeypatch.setattr("trackstarr.cli.sweep", lambda dry_run: dict.fromkeys(Status, 0))
    with caplog.at_level(logging.WARNING):
        assert main(["sweep"]) == 0
    assert "wrong-mount" in caplog.text


def test_a_command_handed_its_files_says_nothing_about_media_dirs(
    startup_ok, monkeypatch, tmp_path, caplog
):
    """plan runs against a single file wherever it lives, so MEDIA_DIRS has nothing
    to do with it."""
    monkeypatch.setattr(config, "MEDIA_DIRS", [str(tmp_path / "wrong-mount")])
    with caplog.at_level(logging.WARNING):
        main(["plan", "--original", "eng", "/nowhere/missing.mkv"])
    assert "wrong-mount" not in caplog.text


def test_plan_fails_when_a_file_cannot_be_read(startup_ok, capsys):
    """A script watching the exit code must see a probe failure."""
    assert main(["plan", "--original", "eng", "/nowhere/missing.mkv"]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_plan_normalises_the_original_language(startup_ok, monkeypatch):
    """Stream tags are ISO 639-2/B, so a 639-1 flag has to become one too or the
    plan drops the language it was told to keep."""
    seen = []

    def capture_lang(path, lang):
        seen.append(lang)
        raise ProbeError("stop before probing")

    monkeypatch.setattr("trackstarr.cli.build_plan", capture_lang)
    main(["plan", "--original", "ja", "f.mkv"])
    assert seen == ["jpn"]


def test_fix_rewrites_files_and_reports_failures(startup_ok, monkeypatch, capsys):
    """One real rewrite path per file, through process(), and a non-zero exit
    when any was not rewritten. A deferral counts: a fix's retry is the
    caller, who must not read it as success."""
    results = {
        "/lib/a.mkv": ProcessResult(Status.FIXED, needed_plan("/lib/a.mkv")),
        "/lib/b.mkv": ProcessResult(Status.FAILED, detail="ffmpeg failed (1): boom"),
        "/lib/c.mkv": ProcessResult(Status.DEFERRED, detail="source changed"),
    }
    calls = []
    runs = []

    def fake_process(job, dry_run, source):
        calls.append((job.path, job.lang, dry_run, source))
        runs.append(job.run)
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
    # One invocation, one run, so the three files group in the history.
    assert runs[0] and len(set(runs)) == 1


def test_fix_still_matches_arr_items_when_original_is_given(startup_ok, monkeypatch):
    """--original overrides the language only. The item match has to survive, or
    the *arr never gets its rescan."""
    index = LibraryIndex({"/lib/Movie": LibraryItem("kor", 7, radarr())}, complete=True)
    monkeypatch.setattr("trackstarr.cli.path_index", lambda arrs: index)
    jobs = []
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: jobs.append(job) or ProcessResult(Status.CONFORM),
    )

    assert main(["fix", "--original", "ja", "/lib/Movie/Movie.mkv"]) == 0
    (job,) = jobs
    assert (job.lang, job.item_id, job.arr.name) == ("jpn", 7, "radarr")


def test_fix_refuses_when_an_arr_cannot_answer(startup_ok, monkeypatch, caplog):
    """lang=None would judge against ALWAYS_KEEP_LANGS alone and drop a foreign
    film's own track. A one-shot command can wait, or be told the language."""
    monkeypatch.setattr("trackstarr.cli.path_index", lambda arrs: LibraryIndex({}, False))
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: pytest.fail("nothing may be rewritten"),
    )
    assert main(["fix", "/lib/a.mkv"]) == 1
    assert "could not be listed" in caplog.text


def test_fix_proceeds_when_the_policy_never_asks_the_language(startup_ok, monkeypatch):
    """ALWAYS_KEEP_LANGS is the whole keep-list here, so the outage withholds
    nothing a verdict needed and the fix runs without --original."""
    monkeypatch.setattr(config, "KEEP_ORIGINAL_LANG", False)
    monkeypatch.setattr("trackstarr.cli.path_index", lambda arrs: LibraryIndex({}, False))
    jobs = []
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: jobs.append(job) or ProcessResult(Status.CONFORM),
    )
    assert main(["fix", "/lib/a.mkv"]) == 0
    (job,) = jobs
    assert job.lang is None


def test_fix_with_original_proceeds_through_an_arr_outage(startup_ok, monkeypatch):
    """The flag supplies what the outage withheld, so only the rescan is lost."""
    monkeypatch.setattr("trackstarr.cli.path_index", lambda arrs: LibraryIndex({}, False))
    jobs = []
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: jobs.append(job) or ProcessResult(Status.CONFORM),
    )
    assert main(["fix", "--original", "ja", "/lib/a.mkv"]) == 0
    (job,) = jobs
    assert job.lang == "jpn"


def test_fix_exit_code_flags_a_deferral_alone(startup_ok, monkeypatch):
    """Deferred means not rewritten, and a one-shot command's retry is the caller."""
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
    """Only a digest is kept, so a second run cannot reprint. Rotating silently
    would lock out a working client, so it has to be asked for."""
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
    policy belongs on screen beside the verdict, not inferred from env."""
    plan = Plan(path="f.mkv", original_lang="jpn", keep_langs={"jpn", "eng"})
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "original language : jpn" in out
    assert "keeping languages : eng, jpn" in out
    # Every configured layout, with the bitrate each would be encoded at.
    assert "downmix layouts" in out
    assert "conforms, no action" in out


def test_plan_groups_the_rules_by_mode_and_omits_the_empty_groups(
    startup_ok, monkeypatch, capsys
):
    """A plan that did nothing is usually a rule that is off or riding along, so
    the summary has to say where each one sits. Blank rows are dropped, so a
    policy with nothing in a group spends no line on it."""
    _planned(monkeypatch, Plan(path="f.mkv", policy=Policy.from_config()))

    main(["plan", "f.mkv"])
    out = capsys.readouterr().out
    assert "rules always" in out and "languages" in out
    assert "rules alongside" in out and "junk_titles" in out
    assert "rules never" in out and "remux" in out

    set_rules(monkeypatch, **dict.fromkeys(policy.RULES, "always"))
    _planned(monkeypatch, Plan(path="f.mkv", policy=Policy.from_config()))
    main(["plan", "f.mkv"])
    assert "rules never" not in capsys.readouterr().out


def test_plan_prints_a_skip_and_stops_there(startup_ok, monkeypatch, capsys):
    plan = Plan(path="f.mkv", skip="hardlinked, left for the download client")
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "SKIP: hardlinked" in out
    assert "ffmpeg" not in out


def test_plan_names_what_the_ride_alongs_are_waiting_for(startup_ok, monkeypatch, capsys):
    """Otherwise "conforms" is the whole answer for a file with named faults
    nobody thought worth a rewrite, and the setting that would change that is
    invisible."""
    plan = Plan(
        path="f.mkv",
        incidental=["clear junk title on audio 1"],
        incidental_rules={"junk_titles"},
    )
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "conforms, no action" in out
    assert "clear junk title on audio 1 (waiting on a rewrite)" in out
    # Nothing is being rewritten, so there is no command to show.
    assert "ffmpeg" not in out


def test_plan_prints_the_reasons_and_the_command(startup_ok, monkeypatch, capsys):
    """The printed ffmpeg line is the tool showing its work, so it has to be the
    command that would really run."""
    plan = Plan(path="f.mkv", reasons=["drop audio jpn"], incidental=["strip junk title"])
    plan.streams.append(OutStream(src=0, kind="video"))
    _planned(monkeypatch, plan)

    assert main(["plan", "f.mkv"]) == 0
    out = capsys.readouterr().out
    assert "- drop audio jpn" in out
    assert "strip junk title (rides along)" in out
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
    """Every command shells out to ffprobe, so saying it once beats a subprocess
    error per file."""
    monkeypatch.setattr("trackstarr.cli.shutil.which", lambda name: None)
    assert main(["plan", "f.mkv"]) == 1
    assert "ffmpeg and ffprobe must be on PATH" in caplog.text


def test_sweep_exit_code_ignores_deferrals_but_not_failures(startup_ok, monkeypatch):
    """Opposite of fix: a sweep's own next run is the retry."""
    counts = dict.fromkeys(Status, 0)
    monkeypatch.setattr("trackstarr.cli.sweep", lambda dry_run: counts)

    counts[Status.DEFERRED] = 3
    assert main(["sweep"]) == 0
    counts[Status.FAILED] = 1
    assert main(["sweep"]) == 1


def test_a_sweep_typed_into_a_paused_install_runs_and_says_so(startup_ok, monkeypatch, caplog):
    """The pause lives in the listener's memory and this is another process,
    so refusing here would refuse a command somebody meant. It sweeps, and
    the line is what stops the result reading as the pause not working."""
    monkeypatch.setattr("trackstarr.cli.runs.paused_on_disk", lambda: True)
    monkeypatch.setattr("trackstarr.cli.sweep", lambda dry_run: dict.fromkeys(Status, 0))
    assert main(["sweep"]) == 0
    assert "runs anyway" in caplog.text


def test_fix_says_so_in_report_mode(startup_ok, monkeypatch, capsys):
    """fix is the command that writes, so the bottom rung of REWRITE_MODE has to
    be stated or it reports "conforms" for files it never touched."""
    monkeypatch.setattr(config, "REWRITE_MODE", "report")
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: ProcessResult(Status.CONFORM),
    )
    main(["fix", "f.mkv"])
    assert "REWRITE_MODE is report" in capsys.readouterr().out


def test_fix_prints_why_a_file_was_skipped(startup_ok, monkeypatch, capsys):
    """ "SKIP" alone reads like a failure; the reason is what says nothing is wrong."""
    plan = Plan(path="f.mkv", skip="hardlinked, left for the download client")
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: ProcessResult(Status.SKIP, plan),
    )
    assert main(["fix", "f.mkv"]) == 0
    assert "hardlinked, left for the download client" in capsys.readouterr().out


def test_fix_books_its_verdict_where_the_collection_reads_it(startup_ok, monkeypatch, tmp_path):
    """The same gap the webhook had: a command that judges a handful of files
    has none of a sweep's bookkeeping, so its verdicts reached the history and
    nothing the library looks at."""
    media = tmp_path / "f.mkv"
    media.write_bytes(b"x" * 10)
    plan = needed_plan(str(media))
    monkeypatch.setattr(
        "trackstarr.cli.process",
        lambda job, dry_run, source: ProcessResult(Status.WOULD_FIX, plan),
    )
    assert main(["fix", str(media), "--original", "eng"]) == 0

    stored = read(sweep_cache.cache_path(), Policy.from_config().fingerprint())
    assert stored.files[str(media)]["status"] == "would-fix"


def test_secret_reports_a_state_dir_it_cannot_write(monkeypatch, caplog):
    """A read-only or unmounted /config is the usual cause, and the user needs
    telling rather than a traceback."""

    def refuse(name):
        raise OSError("read-only file system")

    monkeypatch.setattr("trackstarr.cli.auth.mint", refuse)
    assert main(["secret", "radarr"]) == 1
    assert "could not store the secret" in caplog.text


def test_user_add_and_list(fast_scrypt, capsys):
    """No startup_ok: accounts must be manageable on a host without ffmpeg."""
    assert main(["user", "add", "watcher", "--password", "long enough"]) == 0
    assert main(["user", "add", "boss", "--role", "admin", "--password", "long enough"]) == 0
    assert main(["user", "list"]) == 0
    out = capsys.readouterr().out
    assert "watcher  viewer" in out
    assert "boss  admin" in out
    assert users.verify("watcher", "long enough").role == "viewer"


def test_user_add_prompts_without_echo_when_no_flag(fast_scrypt, monkeypatch):
    monkeypatch.setattr("trackstarr.cli.getpass.getpass", lambda prompt: "long enough")
    assert main(["user", "add", "watcher"]) == 0
    assert users.verify("watcher", "long enough")


def test_user_add_without_a_terminal_says_so(fast_scrypt, monkeypatch, caplog):
    """`docker exec` without -it, which is what the README's reset path used
    to be: getpass raises EOFError and a locked-out admin got a traceback."""

    def no_terminal(prompt):
        raise EOFError

    monkeypatch.setattr("trackstarr.cli.getpass.getpass", no_terminal)
    assert main(["user", "add", "watcher"]) == 1
    assert "docker exec -it" in caplog.text
    assert users.accounts() == []


def test_user_refuses_a_short_password(fast_scrypt, caplog):
    assert main(["user", "add", "watcher", "--password", "short"]) == 1
    assert "at least 8 characters" in caplog.text
    assert users.accounts() == []


def test_user_passwd_resets_and_signs_out_everywhere(fast_scrypt):
    """The recovery path for a forgotten admin password, via docker exec, so
    it must also clear a forced change left over from bootstrap."""
    users.add("watcher", "old password", "viewer", must_change=True)
    token = sessions.create(users.Account("watcher", "viewer", True))
    assert main(["user", "passwd", "watcher", "--password", "new password"]) == 0
    assert sessions.get(token) is None
    assert users.verify("watcher", "new password") == users.Account("watcher", "viewer", False)


def test_user_rm_removes_and_signs_out_but_keeps_the_last_admin(fast_scrypt, caplog):
    users.add("admin", "a password", "admin")
    users.add("watcher", "a password", "viewer")
    token = sessions.create(users.Account("watcher", "viewer", False))
    assert main(["user", "rm", "watcher"]) == 0
    assert sessions.get(token) is None
    assert main(["user", "rm", "admin"]) == 1
    assert "last admin" in caplog.text


def test_user_list_flags_a_pending_password_change(fast_scrypt, capsys):
    users.add("admin", "a password", "admin", must_change=True)
    assert main(["user", "list"]) == 0
    assert "(must change password)" in capsys.readouterr().out


def test_user_mistakes_are_messages_not_tracebacks(fast_scrypt, caplog):
    users.add("watcher", "a password", "viewer")
    assert main(["user", "add", "watcher", "--password", "long enough"]) == 1
    assert "already exists" in caplog.text


def test_user_reports_a_state_dir_it_cannot_write(monkeypatch, caplog):
    def refuse(name, password, role):
        raise OSError("read-only file system")

    monkeypatch.setattr("trackstarr.cli.users.add", refuse)
    assert main(["user", "add", "watcher", "--password", "long enough"]) == 1
    assert "could not update the account store" in caplog.text


def test_an_unknown_log_level_is_refused(capsys):
    """Read through getattr with a fallback, a typo meant INFO in silence, so a
    session asked for as DEBUG looked like a quiet one. Checked before
    basicConfig, which is why it prints rather than logs."""
    assert main(["--log-level", "verbose", "plan", "f.mkv"]) == 1
    assert "verbose" in capsys.readouterr().err


def test_a_lowercase_log_level_still_works(startup_ok, monkeypatch):
    """--log-level debug has always worked, and argparse choices= would have
    quietly taken that away."""
    monkeypatch.setattr(
        "trackstarr.cli.build_plan", lambda path, lang: Plan(path=path, skip="nothing to do")
    )
    assert main(["--log-level", "debug", "plan", "--original", "eng", "f.mkv"]) == 0


def test_a_rule_that_cannot_fire_is_reported_to_a_command_handed_its_files(
    startup_ok, monkeypatch, caplog
):
    """Unlike the MEDIA_DIRS warning, these are about rules rather than the
    library, so plan and fix want them too: the switch reads as on and the rule
    never appears in the plan being explained."""
    set_rules(monkeypatch, remux="always")
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mkv"})
    monkeypatch.setattr(
        "trackstarr.cli.build_plan", lambda path, lang: Plan(path=path, skip="nothing to do")
    )
    with caplog.at_level(logging.WARNING):
        main(["plan", "--original", "eng", "f.mkv"])
    assert "RULE_REMUX" in caplog.text
