"""Service wiring: the webhook listener plus the scheduled sweep.

The pieces have their own modules, :mod:`trackstarr.processing` for one
file, :mod:`trackstarr.sweep` for the library walk, :mod:`trackstarr.webhook`
for the listener. This starts the threads and the server.
"""

import logging
import threading
from http.server import ThreadingHTTPServer

from . import config, events
from .arr import all_arrs
from .executor import clean_work_dir, work_dir_is_remote
from .media_server import server_status
from .policy import Policy
from .processing import all_slots_held
from .sweep import scheduler
from .webhook import (
    Handler,
    load_parked,
    parked_recheck_loop,
    parking_enabled,
    register_webhooks,
    worker,
)

log = logging.getLogger(__name__)


# No cover: wiring. It starts daemon threads and blocks in serve_forever, so
# a test could only assert its own mocks were called.
def serve() -> None:  # pragma: no cover
    # First, so every later event's config_id resolves against something. A
    # sweep records its own, but a webhook-only install never runs one.
    policy = Policy.from_config()
    events.record("config", config=policy.fingerprint(), config_id=policy.digest())
    with all_slots_held() as exclusive:
        clean_work_dir(exclusive=exclusive)
    if work_dir_is_remote():
        # Not a problem, but it doubles the writing per rewrite. Better said
        # than discovered from disk activity.
        log.info(
            "WORK_DIR %s is not on the media's filesystem; each rewrite will be "
            "copied onto it before being published",
            config.WORK_DIR,
        )
    # One worker per rewrite slot, so a burst can use the whole budget.
    for number in range(config.MAX_CONCURRENT_REWRITES):
        threading.Thread(target=worker, daemon=True, name=f"worker-{number}").start()
    if parking_enabled():
        # Before the loop that drains it. Nothing else revisits a parked
        # file, so dropping the set strands whatever was seeding.
        load_parked()
        threading.Thread(target=parked_recheck_loop, daemon=True, name="parked-recheck").start()
    if config.SWEEP_AT:
        threading.Thread(target=scheduler, daemon=True, name="scheduler").start()
    srv = ThreadingHTTPServer((config.LISTEN_ADDR, config.LISTEN_PORT), Handler)
    # Saving the connection makes the *arr fire a test event at us, so this
    # starts only once the socket above is bound.
    threading.Thread(target=register_webhooks, daemon=True, name="register").start()
    log.info("listening on %s:%s", config.LISTEN_ADDR, config.LISTEN_PORT)
    log.info(
        "arrs=[%s] servers=[%s] always_keep=%s sweep_at=%s apply=%s dry_run=%s",
        ", ".join(f"{arr.name}={'on' if arr.enabled else 'off'}" for arr in all_arrs()),
        ", ".join(f"{name}={'on' if on else 'off'}" for name, on in server_status().items()),
        sorted(config.ALWAYS_KEEP_LANGS),
        config.SWEEP_AT or "disabled",
        config.SWEEP_APPLY,
        config.DRY_RUN,
    )
    srv.serve_forever()
