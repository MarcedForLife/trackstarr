"""Browser sessions for the web UI: opaque cookies, digests at rest.

``STATE_DIR/sessions.json`` holds each token's SHA-256 beside its account, so
a copy of the volume yields nothing replayable. Read per request rather than
cached, so ``trackstarr user`` in another process revokes instantly. Expiry
is absolute: a sliding window would rewrite the store on every request.
"""

import hashlib
import os
import secrets
import threading
import time

from . import config
from .state import read_json_records, write_json
from .users import Account, account

SESSIONS_FILE = "sessions.json"

#: How long a sign-in lasts, in seconds. Thirty days.
TTL = 30 * 24 * 3600

#: One read-modify-replace at a time, or a later save drops an earlier row.
_write_lock = threading.Lock()

#: Injection point for the tests. Wall-clock, since expiry survives restarts.
_now = time.time


def _sessions_path() -> str:
    return os.path.join(config.STATE_DIR, SESSIONS_FILE)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _expired(record: dict) -> bool:
    try:
        return float(record.get("expires") or 0) <= _now()
    except TypeError, ValueError:
        return True


def _live(records: dict[str, dict]) -> dict[str, dict]:
    """The unexpired records. Every write prunes."""
    return {digest: record for digest, record in records.items() if not _expired(record)}


def _save(records: dict[str, dict]) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    write_json(_sessions_path(), _live(records), mode=0o600)


def create(signed_in: Account) -> str:
    """A fresh session token for an account that just proved itself. The
    returned token is the only copy. Raises OSError when the store cannot be
    written."""
    token = secrets.token_urlsafe(32)
    with _write_lock:
        records = read_json_records(_sessions_path())
        records[_digest(token)] = {
            "name": signed_in.name,
            "role": signed_in.role,
            "must_change": signed_in.must_change,
            "expires": _now() + TTL,
        }
        _save(records)
    return token


def get(token: str) -> Account | None:
    """The account a cookie speaks for, or None for anything else."""
    if not token:
        return None
    record = read_json_records(_sessions_path()).get(_digest(token))
    if record is None or _expired(record):
        return None
    return account(str(record.get("name") or ""), record)


def revoke(token: str) -> None:
    """Sign one browser out. Raises OSError, since the session would still be
    honoured."""
    with _write_lock:
        records = read_json_records(_sessions_path())
        records.pop(_digest(token), None)
        _save(records)


def revoke_user(name: str) -> None:
    """Sign ``name`` out everywhere, for password changes and removals."""
    with _write_lock:
        records = read_json_records(_sessions_path())
        _save({d: record for d, record in records.items() if record.get("name") != name})
