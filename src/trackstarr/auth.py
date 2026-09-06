"""Per-caller secrets for the webhook listener.

One file per caller under ``STATE_DIR/webhook-secrets``, holding a SHA-256
digest, so a copy of the volume yields nothing replayable. A bare digest, not
a password hash: these are 256-bit random tokens with no dictionary to salt
against.
"""

import hashlib
import hmac
import os
import re
import secrets

from . import config

#: A bare filename, so a name cannot reach outside the directory and a dotfile
#: is never read as a credential.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _secrets_dir() -> str:
    return os.path.join(config.STATE_DIR, "webhook-secrets")


def _path(name: str) -> str:
    return os.path.join(_secrets_dir(), name)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _write(name: str, stored: str) -> None:
    """Replace one secret file atomically. The staging name is a dotfile, so
    one left by a crash is never read as a caller."""
    os.makedirs(_secrets_dir(), exist_ok=True)
    partial = _path(f".{name}.{os.getpid()}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as secret_file:
        secret_file.write(stored)
    os.replace(partial, _path(name))


def _stored(name: str) -> str:
    """One caller's digest, empty when there is none."""
    if not _NAME_RE.fullmatch(name):
        return ""
    try:
        with open(_path(name)) as secret_file:
            return secret_file.read().strip()
    except OSError:
        return ""


def names() -> list[str]:
    """Every provisioned caller."""
    try:
        return sorted(name for name in os.listdir(_secrets_dir()) if _NAME_RE.fullmatch(name))
    except OSError:
        return []


def mint(name: str) -> str:
    """A fresh secret for ``name``, stored as a digest and returned once.
    Minting again replaces the old one.

    Raises ValueError for a name that is not a bare filename, and OSError when
    the file cannot be written.
    """
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"secret name {name!r} must be letters, digits, dots, dashes and underscores"
        )
    secret = secrets.token_urlsafe(32)
    _write(name, _digest(secret))
    return secret


def exists(name: str) -> bool:
    """Whether ``name`` has a secret."""
    return bool(_stored(name))


def matches(name: str, given: str) -> bool:
    """Whether ``given`` is the secret held for ``name``. Compared as bytes,
    since compare_digest refuses non-ASCII str."""
    stored = _stored(name)
    if not (given and stored):
        return False
    return hmac.compare_digest(_digest(given).encode(), stored.encode())


def authorized(given: str) -> bool:
    """Whether a presented value matches any provisioned caller's secret."""
    return any(matches(name, given) for name in names())
