"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import textwrap

from . import __version__, config, policy
from .app import serve
from .arr import all_arrs, match_path, path_index
from .executor import work_dir_errors
from .langs import norm_lang
from .media import ProbeError
from .planner import build_plan, ffmpeg_args
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
    plan_cmd.add_argument("files", nargs="+")
    plan_cmd.add_argument(
        "--original",
        default=None,
        help="the title's original language (en, eng and English all work), "
        "skipping the *arr lookup",
    )
    return parser


def cmd_plan(files: list[str], original: str | None) -> int:
    # Normalised to ISO 639-2/B like every stream tag; --original ja would
    # otherwise sit in keep_langs while the tracks all say jpn, and the plan
    # would drop the very language it was told to keep.
    original = norm_lang(original)
    index = [] if original else path_index(all_arrs())
    failed = False
    for path in files:
        matched = match_path(index, path)
        lang = original or (matched.lang if matched else None)
        try:
            plan = build_plan(path, lang)
        except (ProbeError, OSError) as err:
            print(f"{path}\n  ERROR {err}")
            failed = True
            continue
        print(f"\n{path}")
        print(f"  original language : {plan.original_lang or 'unknown'}")
        print(f"  keeping languages : {', '.join(sorted(plan.keep_langs))}")
        if plan.policy.downmix_layouts:
            shown = ", ".join(
                f"{layout.name} ({layout.bitrate})" for layout in plan.policy.downmix_layouts
            )
            print(f"  downmix layouts   : {shown}")
        if plan.policy.disabled_rules:
            print(f"  disabled rules    : {', '.join(sorted(plan.policy.disabled_rules))}")
        if plan.policy.drop_commentary:
            print("  drop commentary   : on")
        if plan.policy.regenerate_downmixes:
            print(f"  regenerate mixes  : {plan.policy.regenerate_downmixes}")
        if plan.policy.remux_to_mkv:
            print("  remux to mkv      : on")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # One report covering every startup problem at once, so a bad pattern
    # and a bad mount don't take two restarts to discover.
    problems = config.errors() + policy.errors()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        problems.append("ffmpeg and ffprobe must be on PATH")
    # Only the commands that rewrite need WORK_DIR; plan never writes.
    if args.cmd in ("serve", "sweep"):
        problems += work_dir_errors()
    if problems:
        for message in problems:
            log.error("%s", message)
        return 1

    if args.cmd == "serve":
        serve()
        return 0

    if args.cmd == "sweep":
        counts = sweep(dry_run=not args.apply)
        # Deferred is a benign race retried by the next sweep; only real
        # failures should fail the command.
        return 0 if counts[Status.FAILED] == 0 else 1

    return cmd_plan(args.files, args.original)


if __name__ == "__main__":
    sys.exit(main())
