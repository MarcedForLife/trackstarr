#!/usr/bin/env python3
"""Boot the API and the web dev server together for local development.

Vite takes LISTEN_PORT and serves the pages with hot reload; the listener moves
one port along and Vite proxies /api, /health and /webhook to it, so one port
answers as in the image. If either exits the other is stopped too. Not part of
the package.
"""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from trackstarr import config

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

#: How long a process gets to stop on its own before it is killed.
STOP_GRACE = 10

#: The one port development answers on: the listener's own, so a bookmark
#: does not care that a dev server is behind it.
WEB_PORT = config.current().LISTEN_PORT

#: Where the listener binds while Vite has the front door. Passed explicitly,
#: so it wins over .env.
API_PORT = WEB_PORT + 1


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    """Signal the child's whole process group, so npm takes Vite down with it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), sig)


def _shutdown(processes: dict[str, subprocess.Popen]) -> None:
    """Stop whatever is still running and reap it, killing a straggler."""
    for process in processes.values():
        if process.poll() is None:
            _signal_group(process, signal.SIGTERM)
    for process in processes.values():
        try:
            process.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait()


def _request_stop(signum: int, frame: object) -> None:
    """Turn a stop signal into the KeyboardInterrupt the loop handles. Set
    explicitly, since a background shell leaves SIGINT ignored."""
    raise KeyboardInterrupt


def main() -> int:
    if not (WEB / "package.json").exists():
        print("web/ has no package.json; run this from the repo root", file=sys.stderr)
        return 1
    # A first checkout has no node_modules, and Vite cannot start without it.
    if not (WEB / "node_modules").is_dir():
        print("web/node_modules missing, installing it once with npm install")
        subprocess.run(["npm", "install"], cwd=WEB, check=True)

    print(f"starting the UI and API on http://localhost:{WEB_PORT} (Ctrl-C stops both)")
    # Each in its own process group, so _shutdown can take npm's Vite child down
    # with it and Ctrl-C reaches only this launcher.
    processes = {
        "api": subprocess.Popen(
            [sys.executable, "-m", "trackstarr", "serve"],
            cwd=ROOT,
            # WEBHOOK_URL's default follows LISTEN_PORT, and the *arrs must call
            # the port Vite fronts.
            env=os.environ
            | {"LISTEN_PORT": str(API_PORT), "WEBHOOK_URL": config.current().WEBHOOK_URL},
            start_new_session=True,
        ),
        "web": subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=WEB,
            env=os.environ
            | {"TRACKSTARR_WEB_PORT": str(WEB_PORT), "TRACKSTARR_API_PORT": str(API_PORT)},
            start_new_session=True,
        ),
    }
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    try:
        # Poll rather than os.wait, which would reap a child Popen still owns.
        while all(process.poll() is None for process in processes.values()):
            time.sleep(0.25)
        first = next(name for name, process in processes.items() if process.poll() is not None)
        print(f"{first} exited ({processes[first].returncode}); stopping the rest")
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        _shutdown(processes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
