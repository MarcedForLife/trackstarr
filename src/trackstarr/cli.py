"""Command line entry point."""

import argparse
import getpass
import logging
import os
import shutil
import signal
import sys
import textwrap
from types import FrameType

from . import __version__, auth, config, events, policy, runs, sessions, users
from .app import serve
from .arr import LibraryIndex, all_arrs, match_path, path_index
from .command import ffmpeg_args
from .executor import audio_codec_errors, work_dir_errors
from .langs import norm_lang
from .media import ProbeError
from .planner import build_plan, describe
from .processing import Job, process, state_dir_errors
from .status import Status
from .sweep import remember, sweep
from .sweep_cache import cache_key

log = logging.getLogger("trackstarr")

DESCRIPTION = (
    "Keep library audio and subtitle tracks tidy.\n\n"
    "Each rule runs never, alongside a rewrite another rule ordered, or always.\n"
    "Set one with RULE_LANGUAGES, RULE_COVER_ART and so on; the default is in\n"
    "brackets.\n\n"
    + "\n".join(
        textwrap.fill(
            f"{name} [{rule.default}] {rule.summary}",
            width=74,
            initial_indent="  ",
            subsequent_indent="    ",
        )
        for name, rule in policy.RULES.items()
    )
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trackstarr",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"trackstarr {__version__}")
    # Checked in main() so a lowercase --log-level debug still works.
    parser.add_argument(
        "--log-level",
        default=config.LOG_LEVEL,
        help=f"one of {', '.join(config.LOG_LEVELS)} (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the webhook listener")

    sweep_cmd = sub.add_parser("sweep", help="walk the library")
    sweep_cmd.add_argument(
        "--apply", action="store_true", help="rewrite files (default is to report only)"
    )

    plan_cmd = sub.add_parser("plan", help="explain the decision for specific files")
    _add_files_arguments(plan_cmd)

    fix_cmd = sub.add_parser(
        "fix", help="plan and rewrite specific files now, wherever they live"
    )
    _add_files_arguments(fix_cmd)

    secret_cmd = sub.add_parser(
        "secret", help="mint a caller's webhook secret and print it once"
    )
    secret_cmd.add_argument("name", help="who the secret is for, e.g. a custom client")
    secret_cmd.add_argument(
        "--rotate",
        action="store_true",
        help="replace an existing secret; the old one stops working immediately",
    )

    user_cmd = sub.add_parser("user", help="manage the web UI's accounts")
    user_sub = user_cmd.add_subparsers(dest="action", required=True)
    add_cmd = user_sub.add_parser("add", help="create an account")
    add_cmd.add_argument("name")
    add_cmd.add_argument(
        "--role",
        choices=users.ROLES,
        default="viewer",
        help="admins change things, viewers only read (default: viewer)",
    )
    passwd_cmd = user_sub.add_parser(
        "passwd", help="reset an account's password and sign it out everywhere"
    )
    passwd_cmd.add_argument("name")
    for cmd in (add_cmd, passwd_cmd):
        cmd.add_argument(
            "--password",
            default=None,
            help="for scripts and docker exec; omit to be prompted without echo",
        )
    rm_cmd = user_sub.add_parser("rm", help="delete an account and sign it out everywhere")
    rm_cmd.add_argument("name")
    user_sub.add_parser("list", help="show every account and its role")
    return parser


def _add_files_arguments(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("files", nargs="+")
    cmd.add_argument(
        "--original",
        default=None,
        help="the title's original language (en, eng and English all work), "
        "skipping the *arr lookup",
    )


def _resolve_jobs(
    files: list[str], original: str | None, match_items: bool = False, run: str | None = None
) -> tuple[list[Job], bool]:
    """One Job per file, plus whether every enabled *arr answered.

    The flag is normalised like every stream tag, or ``--original ja`` would
    never match jpn. ``match_items`` builds the *arr index even when the flag
    supplies the language: fix needs the item id for its rescan, plan does not.
    """
    original = norm_lang(original)
    if match_items or not original:
        index = path_index(all_arrs())
    else:
        index = LibraryIndex({}, complete=True)
    return [Job.from_match(path, match_path(index, path), original, run) for path in files], (
        index.complete
    )


def cmd_plan(files: list[str], original: str | None) -> int:
    failed = False
    jobs, _ = _resolve_jobs(files, original)
    for job in jobs:
        path = job.path
        try:
            plan = build_plan(path, job.lang)
        except (ProbeError, OSError) as err:
            print(f"{path}\n  ERROR {err}")
            failed = True
            continue
        print(f"\n{path}")
        # One row per policy fact, blank values omitted.
        rows = [
            ("original language", plan.original_lang or "unknown"),
            # One row per list, each entry saying what happens to it. Languages
            # are the resolved ones, so "original" reads as the code it meant.
            (
                "languages",
                ", ".join(f"{lang.name} ({lang.action})" for lang in plan.langs) or "(none)",
            ),
            (
                "audio layouts",
                ", ".join(
                    f"{layout.name} ({layout.codec} {layout.bitrate})"
                    if layout.downmixes
                    else f"{layout.name} ({layout.action})"
                    for layout in plan.policy.audio_layouts
                ),
            ),
            # Grouped by mode, strongest first.
            *(
                (f"rules {mode}", ", ".join(sorted(plan.policy.rules_in(mode))))
                for mode in reversed(policy.MODES)
            ),
            ("regenerate scope", plan.policy.regenerate_scope),
        ]
        for label, value in rows:
            if value:
                print(f"  {label:<18}: {value}")
        if plan.skip:
            print(f"  SKIP: {plan.skip}")
            continue
        if not plan.needed:
            print("  conforms, no action")
            # What the ride-alongs would have done, or "conforms" hides them.
            for reason in plan.incidental:
                print(f"  - {reason} (waiting on a rewrite)")
            continue
        for reason in plan.reasons:
            print(f"  - {reason}")
        for reason in plan.incidental:
            print(f"  - {reason} (rides along)")
        dest = "OUT" + os.path.splitext(plan.out_path)[1]
        print("  ffmpeg " + " ".join(ffmpeg_args(plan, dest)[1:]))
    return 1 if failed else 0


def cmd_fix(files: list[str], original: str | None) -> int:
    """Plan and rewrite specific files, wherever they live.

    Same locks, events and REWRITE_MODE latch as everything else. Deferred
    fails the exit code, unlike in the sweep: the caller is the retry.
    """
    if config.current().REWRITE_MODE == "report":
        print("REWRITE_MODE is report, planning only, nothing will be rewritten")
    # One run per invocation, so a multi-file fix groups in the history.
    jobs, arrs_answered = _resolve_jobs(files, original, match_items=True, run=events.run_id())
    if not arrs_answered and not original and policy.Policy.from_config().needs_original_lang():
        # lang=None leaves the original row unresolved, dropping a foreign
        # film's own track.
        log.error(
            "a *arr library could not be listed, so original languages are unknown; "
            "pass --original or retry once it answers"
        )
        return 1
    failed = False
    for job in jobs:
        # Before the probe, so the verdict is keyed to the file as it was.
        key = cache_key(job.path, job.lang)
        result = process(job, dry_run=False, source="cli")
        remember(job.path, key, result)
        print(f"{result.status}  {job.path}")
        # A deferred plan is stale, so its reasons would mislead.
        if (
            result.plan
            and result.status is not Status.DEFERRED
            and (note := describe(result.plan))
        ):
            print(f"  {note}")
        if result.detail:
            print(f"  {result.detail}")
        failed = failed or result.status in (Status.FAILED, Status.DEFERRED)
    return 1 if failed else 0


def cmd_secret(name: str, rotate: bool) -> int:
    """Mint a webhook secret for a named caller and print it once.

    Only a digest is kept, so losing it means rotating. --rotate is required
    for a caller that already has one, or a second run would lock out a
    working client.
    """
    if auth.exists(name) and not rotate:
        log.error(
            "%s already has a secret and it cannot be shown again; pass --rotate to replace it",
            name,
        )
        return 1
    try:
        print(auth.mint(name))
    except ValueError as err:
        log.error("%s", err)
        return 1
    except OSError as err:
        log.error("could not store the secret: %s", err)
        return 1
    return 0


def cmd_user(args: argparse.Namespace) -> int:
    """Account management for the web UI: add, passwd, rm and list.

    passwd and rm revoke the account's sessions too. Runs against serve's
    STATE_DIR via docker exec, which is the recovery path for a forgotten
    admin password.
    """
    try:
        if args.action == "list":
            for held in users.accounts():
                note = "  (must change password)" if held.must_change else ""
                print(f"{held.name}  {held.role}{note}")
            return 0
        if args.action == "rm":
            users.remove(args.name)
            sessions.revoke_user(args.name)
            print(f"removed {args.name}")
            return 0
        try:
            password = args.password or getpass.getpass(f"new password for {args.name}: ")
        except EOFError:
            # `docker exec` without -it: no terminal to prompt on.
            log.error(
                "no terminal to read a password from; "
                "run this under `docker exec -it`, or pass --password"
            )
            return 1
        if len(password) < users.MIN_PASSWORD_LEN:
            log.error("the password needs at least %d characters", users.MIN_PASSWORD_LEN)
            return 1
        if args.action == "add":
            users.add(args.name, password, args.role)
            print(f"added {args.name} ({args.role})")
        else:
            users.set_password(args.name, password)
            sessions.revoke_user(args.name)
            print(f"new password set for {args.name}")
        return 0
    except ValueError as err:
        log.error("%s", err)
        return 1
    except OSError as err:
        log.error("could not update the account store: %s", err)
        return 1


# No cover: hands straight to app.serve, which blocks for ever.
def _run_serve(args: argparse.Namespace) -> int:  # pragma: no cover
    serve()
    return 0


def _run_sweep(args: argparse.Namespace) -> int:
    # Only the pause flag is shared between processes. Having a shell is the
    # authorisation, so this sweeps anyway and says so.
    if runs.paused_on_disk():
        log.warning("the service is paused; this sweep runs anyway")
    counts = sweep(dry_run=not args.apply)
    # Deferred is a benign race the next sweep retries.
    return 0 if counts[Status.FAILED] == 0 else 1


#: Each command's handler and the startup checks it needs. "read" checks
#: config, policy and ffmpeg on PATH; "rewrite" adds the directories and the
#: encoders. secret and user run bare.
COMMANDS = {
    "serve": (_run_serve, "rewrite"),
    "sweep": (_run_sweep, "rewrite"),
    "fix": (lambda args: cmd_fix(args.files, args.original), "rewrite"),
    "plan": (lambda args: cmd_plan(args.files, args.original), "read"),
    "secret": (lambda args: cmd_secret(args.name, args.rotate), None),
    "user": (cmd_user, None),
}


#: The commands that walk MEDIA_DIRS, and so want config.media_dir_warnings().
LIBRARY_COMMANDS = frozenset({"serve", "sweep"})


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def handle_sigterm() -> None:
    """Make SIGTERM end the process, as SIGINT does.

    The kernel skips default signal actions for PID 1, so without this
    ``docker stop`` waits out its grace period and SIGKILLs. A rewrite under
    way dies with the interpreter; startup clears the staged orphan.
    """
    signal.signal(signal.SIGTERM, _on_sigterm)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handle_sigterm()

    # Before basicConfig, so it prints rather than logs. An unknown level would
    # otherwise silently read as INFO.
    level = args.log_level.strip().upper()
    if level not in config.LOG_LEVELS:
        print(
            f"log level {args.log_level!r} is not one of: {', '.join(config.LOG_LEVELS)}",
            file=sys.stderr,
        )
        return 1
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    handler, checks = COMMANDS[args.cmd]
    # Every startup problem in one report.
    problems: list[str] = []
    if checks:
        problems += config.errors() + policy.errors()
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            problems.append("ffmpeg and ffprobe must be on PATH")
    if checks == "rewrite":
        problems += work_dir_errors()
        problems += state_dir_errors()
        problems += audio_codec_errors()
    if problems:
        for message in problems:
            log.error("%s", message)
        return 1
    # A setting nothing reads, or a rule that cannot fire, changes the plan.
    messages = config.warnings() + policy.warnings() if checks else []
    if args.cmd in LIBRARY_COMMANDS:
        messages += config.media_dir_warnings()
    for message in messages:
        log.warning("%s", message)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
