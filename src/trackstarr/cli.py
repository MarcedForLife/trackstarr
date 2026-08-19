"""Command line entry point."""

import argparse
import logging
import os
import shutil
import sys
import textwrap

from . import __version__, auth, config, policy
from .app import serve
from .arr import all_arrs, match_path, path_index
from .executor import audio_codec_errors, work_dir_errors
from .langs import norm_lang
from .media import ProbeError
from .planner import build_plan, describe, ffmpeg_args
from .processing import Job, process
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
) -> list[Job]:
    """One Job per file, its language from the flag or the *arrs' index.

    The flag is normalised to ISO 639-2/B like every stream tag;
    --original ja would otherwise sit in keep_langs while the tracks all
    say jpn, and the plan would drop the very language it was told to keep.

    ``match_items`` builds the index even when the flag supplies the
    language: fix needs the matched item id so the *arr still gets its
    rescan after the rewrite, while plan skips the *arr round trip entirely,
    which is what lets it run with no *arr reachable at all.
    """
    original = norm_lang(original)
    index = path_index(all_arrs()) if match_items or not original else {}
    return [Job.from_match(path, match_path(index, path), original) for path in files]


def cmd_plan(files: list[str], original: str | None) -> int:
    failed = False
    for job in _resolve_jobs(files, original):
        path = job.path
        try:
            plan = build_plan(path, job.lang)
        except (ProbeError, OSError) as err:
            print(f"{path}\n  ERROR {err}")
            failed = True
            continue
        print(f"\n{path}")
        # One row per policy fact, blank values omitted; a new Policy field
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
            print(f"  - {reason} (rides along, never triggers on its own)")
        dest = "OUT" + os.path.splitext(plan.out_path)[1]
        print("  ffmpeg " + " ".join(ffmpeg_args(plan, dest)[1:]))
    return 1 if failed else 0


def cmd_fix(files: list[str], original: str | None) -> int:
    """Plan and rewrite specific files, wherever they live.

    The ad-hoc path beside the webhook and the sweep, where having a shell
    is the authorisation. Rewrites take the same locks, events and DRY_RUN
    latch as every other source, via process().

    Deferred fails the exit code here, unlike the sweep: the sweep's next
    run is its retry, but a one-shot command's retry is the caller, who
    must not read "not rewritten, run it again" as success.
    """
    if config.DRY_RUN:
        print("DRY_RUN is set, planning only, nothing will be rewritten")
    failed = False
    for job in _resolve_jobs(files, original, match_items=True):
        result = process(job, dry_run=False, source="cli")
        print(f"{result.status}  {job.path}")
        # A deferred plan is stale by definition, so its reasons are left
        # unprinted: they would read as work performed on a file that has
        # since changed.
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

    This is how a custom client gets a credential the listener accepts.
    Only a digest is kept, so this is the single time the secret is shown:
    losing it means rotating rather than looking it up, which is why a
    caller that already has one needs --rotate. Without that guard a second
    run would silently lock out a client that was working. Deleting its
    file under STATE_DIR/webhook-secrets revokes that caller alone.
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


#: Each command's handler and the startup checks it needs: "read" is the
#: config, policy and ffmpeg-on-PATH report; "rewrite" adds WORK_DIR and the
#: encoder. secret runs bare, it needs only a writable STATE_DIR and must
#: work on a host without ffmpeg.
COMMANDS = {
    "serve": (_run_serve, "rewrite"),
    "sweep": (_run_sweep, "rewrite"),
    "fix": (lambda args: cmd_fix(args.files, args.original), "rewrite"),
    "plan": (lambda args: cmd_plan(args.files, args.original), "read"),
    "secret": (lambda args: cmd_secret(args.name, args.rotate), None),
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    handler, checks = COMMANDS[args.cmd]
    # One report covering every startup problem at once, so a bad pattern
    # and a bad mount don't take two restarts to discover.
    problems: list[str] = []
    if checks:
        problems += config.errors() + policy.errors()
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            problems.append("ffmpeg and ffprobe must be on PATH")
    if checks == "rewrite":
        problems += work_dir_errors()
        problems += audio_codec_errors()
    if problems:
        for message in problems:
            log.error("%s", message)
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
