"""Command line entry point."""

import argparse
import logging
import os
import shutil
import signal
import sys
import textwrap
from types import FrameType

from . import __version__, auth, config, policy
from .app import serve
from .arr import LibraryIndex, all_arrs, match_path, path_index
from .command import ffmpeg_args
from .executor import audio_codec_errors, work_dir_errors
from .langs import norm_lang
from .media import ProbeError
from .planner import build_plan, describe
from .processing import Job, process, state_dir_errors
from .status import Status
from .sweep import sweep

log = logging.getLogger("trackstarr")

DESCRIPTION = (
    "Keep library audio and subtitle tracks tidy.\n\n"
    + "\n".join(
        textwrap.fill(f"{i}. {rule}", width=74, initial_indent="  ", subsequent_indent="     ")
        for i, rule in enumerate(policy.RULES.values(), 1)
    )
    + "\n\nOff by default:\n"
    + "\n".join(
        textwrap.fill(f"- {rule}", width=74, initial_indent="  ", subsequent_indent="    ")
        for rule in policy.OPT_IN_RULES.values()
    )
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trackstarr",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"trackstarr {__version__}")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))

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
    files: list[str], original: str | None, match_items: bool = False
) -> tuple[list[Job], bool]:
    """One Job per file, its language from the flag or the *arrs' index,
    plus whether every enabled *arr answered the index fetch.

    The flag is normalised like every stream tag, or ``--original ja`` would sit
    in keep_langs while the tracks all say jpn.

    ``match_items`` builds the index even when the flag supplies the language,
    because fix needs the item id for its rescan; plan skips the round trip
    and so runs with no *arr up.
    """
    original = norm_lang(original)
    if match_items or not original:
        index = path_index(all_arrs())
    else:
        index = LibraryIndex({}, complete=True)
    return [Job.from_match(path, match_path(index, path), original) for path in files], (
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
        # One row per policy fact, blank values omitted. A new Policy field
        # joins the summary by adding a row.
        rows = [
            ("original language", plan.original_lang or "unknown"),
            ("keeping languages", ", ".join(sorted(plan.keep_langs)) or "(none)"),
            (
                "downmix layouts",
                ", ".join(
                    f"{layout.name} ({layout.bitrate})"
                    for layout in plan.policy.downmix_layouts
                ),
            ),
            ("disabled rules", ", ".join(sorted(plan.policy.disabled_rules))),
            ("drop commentary", "on" if plan.policy.drop_commentary else ""),
            ("regenerate mixes", plan.policy.regenerate_downmixes),
            ("remux to mkv", "on" if plan.policy.remux_to_mkv else ""),
        ]
        for label, value in rows:
            if value:
                print(f"  {label:<18}: {value}")
        if plan.skip:
            print(f"  SKIP: {plan.skip}")
            continue
        if not plan.needed:
            print("  conforms, no action")
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

    Having a shell is the authorisation; same locks, events and DRY_RUN latch
    as everything else. Deferred fails the exit code here, unlike in the
    sweep: a one-shot command's retry is the caller, who must not read "not
    rewritten, run it again" as success.
    """
    if config.DRY_RUN:
        print("DRY_RUN is set, planning only, nothing will be rewritten")
    jobs, arrs_answered = _resolve_jobs(files, original, match_items=True)
    if not arrs_answered and not original:
        # lang=None would judge every file against ALWAYS_KEEP_LANGS alone
        # and read a foreign film's own track as junk to drop.
        log.error(
            "a *arr library could not be listed, so original languages are unknown; "
            "pass --original or retry once it answers"
        )
        return 1
    failed = False
    for job in jobs:
        result = process(job, dry_run=False, source="cli")
        print(f"{result.status}  {job.path}")
        # A deferred plan is stale by definition, so its reasons would read
        # as work done on a file that has since changed.
        if (
            result.plan
            and result.status is not Status.DEFERRED
            and (note := result.plan.skip or describe(result.plan))
        ):
            print(f"  {note}")
        if result.detail:
            print(f"  {result.detail}")
        failed = failed or result.status in (Status.FAILED, Status.DEFERRED)
    return 1 if failed else 0


def cmd_secret(name: str, rotate: bool) -> int:
    """Mint a webhook secret for a named caller and print it once.

    Only a digest is kept, so this is the one time the secret is shown and
    losing it means rotating. Hence --rotate for a caller that already has
    one: without it a second run would quietly lock out a working client.
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


# No cover: hands straight to app.serve, which blocks for ever.
def _run_serve(args: argparse.Namespace) -> int:  # pragma: no cover
    serve()
    return 0


def _run_sweep(args: argparse.Namespace) -> int:
    counts = sweep(dry_run=not args.apply)
    # Deferred is a benign race retried by the next sweep; only real
    # failures should fail the command.
    return 0 if counts[Status.FAILED] == 0 else 1


#: Each command's handler and the startup checks it needs. "read" is the
#: config, policy and ffmpeg-on-PATH report; "rewrite" adds the directories
#: and the encoder. secret runs bare, and has to work without ffmpeg.
COMMANDS = {
    "serve": (_run_serve, "rewrite"),
    "sweep": (_run_sweep, "rewrite"),
    "fix": (lambda args: cmd_fix(args.files, args.original), "rewrite"),
    "plan": (lambda args: cmd_plan(args.files, args.original), "read"),
    "secret": (lambda args: cmd_secret(args.name, args.rotate), None),
}


#: The commands that walk MEDIA_DIRS, and so want config.warnings() out loud.
#: plan and fix are handed their files wherever those live.
LIBRARY_COMMANDS = frozenset({"serve", "sweep"})


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def handle_sigterm() -> None:
    """Make SIGTERM end the process, the way SIGINT already does.

    Python installs no SIGTERM handler and the kernel skips default actions for
    PID 1, which is what the container runs. Without this, ``docker stop`` waits
    out its grace period and SIGKILLs: ten seconds every update.

    A rewrite in flight is not waited for. Its daemon thread dies with the
    interpreter and leaves the staged orphan a SIGKILL would, which startup
    clears anyway.
    """
    signal.signal(signal.SIGTERM, _on_sigterm)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handle_sigterm()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    handler, checks = COMMANDS[args.cmd]
    # Every startup problem in one report, so a bad pattern and a bad mount
    # don't take two restarts to find.
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
    # Only once nothing is fatal, and only where it applies.
    if args.cmd in LIBRARY_COMMANDS:
        for message in config.warnings():
            log.warning("%s", message)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
