"""Append-only history, one JSON line per event, in STATE_DIR/events.jsonl.

Writing is all this module does. Nothing reads it yet: recording starts now
because history nobody wrote down cannot be backfilled, and the shape a
stats view wants is that view's decision.

``reasons`` and ``incidental`` are prose for people; ``rules`` and
``incidental_rules`` say the same in :data:`trackstarr.policy.RULE_NAMES`,
so aggregating never means parsing English. ``config_id`` is the policy in
force, which ``serve`` and every sweep also record in full.
"""

import json
import logging
import os
import threading
from datetime import datetime

from . import __version__, config

log = logging.getLogger(__name__)

#: A sweep summary can land while a webhook rewrite finishes, so the lines
#: need this to stay whole.
_write_lock = threading.Lock()


def path() -> str:
    return os.path.join(config.STATE_DIR, "events.jsonl")


def timestamp() -> str:
    """Local time with offset, the format every ``ts`` carries."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record(event: str, **fields) -> None:
    """Append one event. ``ts``, ``event`` and the version come first.

    None-valued fields are dropped; absence means unknown. Never raises, and
    a lost line is not worth failing the rewrite it describes.
    """
    entry = {"ts": timestamp(), "event": event, "version": __version__}
    entry.update({name: value for name, value in fields.items() if value is not None})
    try:
        with _write_lock:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            with open(path(), "a") as events_file:
                events_file.write(json.dumps(entry) + "\n")
    except OSError as err:
        log.warning("could not record %s event: %s", event, err)
