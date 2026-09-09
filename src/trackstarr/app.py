"""Service wiring: starts the background threads and the HTTP server."""

import logging
import threading
from http.server import ThreadingHTTPServer

from . import config, events, library, notify, ratings, runlog, runs, users
from .arr import all_arrs, register_webhooks
from .executor import clean_work_dir, work_dir_is_remote
from .jobs import load_parked, parked_recheck_loop, start_workers
from .media_server import server_status
from .policy import Policy
from .processing import all_slots_held
from .sweep import scheduler
from .webhook import Handler

log = logging.getLogger(__name__)


# No cover: wiring that starts daemon threads and blocks in serve_forever.
def serve() -> None:  # pragma: no cover
    # First, so every later event's config_id resolves; a webhook-only install
    # never runs a sweep to record it.
    events.record_config(Policy.from_config())
    # Before the listener binds, so a generated password sits at the top of a
    # first run's log.
    users.ensure_admin()
    with all_slots_held() as exclusive:
        clean_work_dir(exclusive=exclusive)
    if work_dir_is_remote():
        # Not a problem, but it doubles the writing per rewrite.
        log.info(
            "WORK_DIR %s is not on the media's filesystem; each rewrite will be "
            "copied onto it before being published",
            config.WORK_DIR,
        )
    # Before anything picks up a file, so every worker log line is kept with
    # its file for the overview.
    runlog.capture()
    # Before the workers exist, so a restart does not undo a pause.
    runs.load_paused()
    # Topped up again after a settings save.
    start_workers()
    # Before the loop that drains it.
    load_parked()
    # Each loop runs whether or not its setting is on and re-reads it as it
    # goes, so a setting saved later takes effect without a restart.
    threading.Thread(target=parked_recheck_loop, daemon=True, name="parked-recheck").start()
    threading.Thread(target=scheduler, daemon=True, name="scheduler").start()
    # Handed the shelf's ids and forget so ratings stays a leaf module.
    threading.Thread(
        target=ratings.refresh_loop,
        args=(library.imdb_ids, library.forget),
        daemon=True,
        name="imdb-ratings",
    ).start()
    # A file watcher rather than hooks in the writers, so a CLI sweep in a
    # second process is announced too.
    threading.Thread(target=notify.watcher, daemon=True, name="stream-watcher").start()
    srv = ThreadingHTTPServer((config.LISTEN_ADDR, config.LISTEN_PORT), Handler)
    # Registration fires a test event at us, so the socket must be bound first.
    threading.Thread(target=register_webhooks, daemon=True, name="register").start()
    log.info("listening on %s:%s", config.LISTEN_ADDR, config.LISTEN_PORT)
    log.info(
        "arrs=[%s] servers=[%s] languages=%s sweep_at=%s mode=%s",
        ", ".join(f"{arr.name}={'on' if arr.enabled else 'off'}" for arr in all_arrs()),
        ", ".join(f"{name}={'on' if on else 'off'}" for name, on in server_status().items()),
        list(config.LANGUAGES),
        config.SWEEP_AT or "disabled",
        config.REWRITE_MODE,
    )
    srv.serve_forever()
