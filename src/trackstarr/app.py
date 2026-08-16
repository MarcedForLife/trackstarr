"""Service wiring: the webhook listener plus the scheduled sweep.

The pieces live in their own modules — :mod:`trackstarr.processing` for the
plan-and-rewrite of one file, :mod:`trackstarr.sweep` for the library walk
and its scheduler, :mod:`trackstarr.webhook` for the listener and its work
queue. This one starts the threads and the server.
"""

import logging
import threading
from http.server import ThreadingHTTPServer

from . import config
from .arr import all_arrs
from .executor import clean_work_dir, work_dir_is_remote
from .media_server import server_status
from .processing import all_slots_held
from .sweep import scheduler
from .webhook import Handler, parked_recheck_loop, parking_enabled, register_webhooks, worker

log = logging.getLogger(__name__)


# No cover: this is wiring, not logic. It starts daemon threads and blocks in
# serve_forever, so a test can only assert that the mocks it just installed were
# called. The pieces it wires up are each covered in their own module.
def serve() -> None:  # pragma: no cover
    with all_slots_held() as exclusive:
        clean_work_dir(exclusive=exclusive)
    if work_dir_is_remote():
        # Not a problem, but it doubles the writing per rewrite, so say so
        # rather than leave it to be discovered from disk activity.
        log.info(
            "WORK_DIR %s is not on the media's filesystem; each rewrite will be "
            "copied onto it before being published",
            config.WORK_DIR,
        )
    # One worker per rewrite slot, so a burst of imports can use the whole
    # budget. They share it with the sweep, which never exceeds it either.
    for number in range(config.MAX_CONCURRENT_REWRITES):
        threading.Thread(target=worker, daemon=True, name=f"worker-{number}").start()
    if parking_enabled():
        threading.Thread(target=parked_recheck_loop, daemon=True, name="parked-recheck").start()
    if config.SWEEP_AT:
        threading.Thread(target=scheduler, daemon=True, name="scheduler").start()
    srv = ThreadingHTTPServer((config.LISTEN_ADDR, config.LISTEN_PORT), Handler)
    # Saving the connection makes the *arr fire a test event at us, so this
    # only starts once the socket above is bound and listening; the test
    # request queues until serve_forever picks it up.
    threading.Thread(target=register_webhooks, daemon=True, name="register").start()
    log.info("listening on %s:%s", config.LISTEN_ADDR, config.LISTEN_PORT)
    log.info(
        "arrs=[%s] servers=[%s] always_keep=%s sweep_at=%s apply=%s dry_run=%s",
        ", ".join(f"{arr.name}={'on' if arr.enabled else 'off'}" for arr in all_arrs()),
        ", ".join(f"{name}={'on' if on else 'off'}" for name, on in server_status().items()),
        sorted(config.ALWAYS_KEEP),
        config.SWEEP_AT or "disabled",
        config.SWEEP_APPLY,
        config.DRY_RUN,
    )
    srv.serve_forever()
