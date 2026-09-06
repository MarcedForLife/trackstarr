"""Web UI accounts: scrypt-hashed passwords, roles, and the first-run admin.

``STATE_DIR/users.json`` maps each name to its hash, role and whether the next
sign-in must change the password. Unlike webhook secrets these are passwords a
human chose, so a KDF (key derivation function) earns its keep; each hash
carries its own parameters, so the cost can be raised without a migration.

Admins may change things, viewers only read. The API and CLI enforce roles and
password length; this module stores them.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass

from . import config
from .state import read_json_records, write_json

log = logging.getLogger(__name__)

USERS_FILE = "users.json"

ROLES = ("admin", "viewer")

#: The floor for chosen passwords. NIST's advice: length, no composition rules.
MIN_PASSWORD_LEN = 8

#: Bare identifiers, the same shape as webhook caller names.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: 32 MiB and tens of milliseconds per guess. 128*n*r sits on OpenSSL's default
#: memory ceiling, hence the explicit maxmem.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SALT_BYTES = 16
_KEY_BYTES = 32

#: Injection point for the tests.
_now = time.monotonic


@dataclass(frozen=True, slots=True)
class Account:
    """Who a request is acting as, minted by verify() or a stored session."""

    name: str
    role: str
    must_change: bool


def _users_path() -> str:
    return os.path.join(config.STATE_DIR, USERS_FILE)


def _load() -> dict[str, dict]:
    return read_json_records(_users_path())


def _save(records: dict[str, dict]) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    write_json(_users_path(), records, mode=0o600)


def account(name: str, record: dict) -> Account:
    """The Account a stored record describes. An unknown role reads as
    viewer."""
    role = record.get("role")
    return Account(name, role if role in ROLES else "viewer", bool(record.get("must_change")))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_KEY_BYTES,
    )
    parts = ("scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P), salt.hex(), key.hex())
    return "$".join(parts)


def _matches(stored: str, password: str) -> bool:
    """Whether ``password`` produces ``stored``. A mangled hash, or parameters
    over the memory cap, read as no match."""
    try:
        scheme, cost, block, lanes, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(key)
        candidate = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(cost),
            r=int(block),
            p=int(lanes),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected)


#: Hashed lazily, so only a login pays the scrypt cost.
_dummy_hash: str | None = None


def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    return _dummy_hash


def verify(name: str, password: str) -> Account | None:
    """The account when ``password`` is ``name``'s, else None. An unknown name
    still costs one scrypt, so timing never says which names exist."""
    record = _load().get(name)
    if record is None:
        _matches(_dummy(), password)
        return None
    if not _matches(str(record.get("hash") or ""), password):
        return None
    return account(name, record)


def add(name: str, password: str, role: str, must_change: bool = False) -> None:
    """Create an account. Raises ValueError for a bad or taken name or a bad
    role, and OSError when the store cannot be written."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"user name {name!r} must be letters, digits, dots, dashes and underscores"
        )
    if role not in ROLES:
        raise ValueError(f"role {role!r} must be one of: {', '.join(ROLES)}")
    records = _load()
    if name in records:
        raise ValueError(f"user {name!r} already exists")
    records[name] = {"hash": hash_password(password), "role": role, "must_change": must_change}
    _save(records)


def set_password(name: str, password: str, must_change: bool = False) -> None:
    """Replace ``name``'s password. Raises ValueError for an unknown name."""
    records = _load()
    if name not in records:
        raise ValueError(f"user {name!r} does not exist")
    records[name]["hash"] = hash_password(password)
    records[name]["must_change"] = must_change
    _save(records)


def remove(name: str) -> None:
    """Delete an account. The last admin cannot be removed."""
    records = _load()
    if name not in records:
        raise ValueError(f"user {name!r} does not exist")
    admins = [held for held, record in records.items() if account(held, record).role == "admin"]
    if admins == [name]:
        raise ValueError("the last admin cannot be removed")
    del records[name]
    _save(records)


def accounts() -> list[Account]:
    """Every account, sorted by name."""
    return [account(name, record) for name, record in sorted(_load().items())]


def ensure_admin() -> None:
    """Give a fresh install its admin, once.

    ADMIN_PASSWORD is used as given; otherwise a password is generated,
    logged, and must be changed at first sign-in. An existing store is left
    alone.
    """
    if _load():
        return
    if config.ADMIN_PASSWORD:
        add("admin", config.ADMIN_PASSWORD, "admin")
        log.info("created the admin user with the configured ADMIN_PASSWORD")
        return
    password = secrets.token_urlsafe(12)
    add("admin", password, "admin", must_change=True)
    log.warning(
        "created the admin user with password %s; it must be changed at first sign-in",
        password,
    )


#: Five wrong passwords in a row lock a name for a minute: enough to make online
#: guessing pointless, small enough not to matter to a fumbled login.
_LOCK_AFTER = 5
_LOCK_SECONDS = 60.0

_failures: dict[str, tuple[int, float]] = {}
_failures_lock = threading.Lock()


def locked(name: str) -> bool:
    """Whether sign-in attempts for ``name`` are refused right now."""
    with _failures_lock:
        count, last = _failures.get(name, (0, 0.0))
    return count >= _LOCK_AFTER and _now() - last < _LOCK_SECONDS


def note_failure(name: str) -> None:
    """Count a wrong password. A quiet minute forgives earlier ones."""
    with _failures_lock:
        count, last = _failures.get(name, (0, 0.0))
        if _now() - last >= _LOCK_SECONDS:
            count = 0
        _failures[name] = (count + 1, _now())


def note_success(name: str) -> None:
    with _failures_lock:
        _failures.pop(name, None)
