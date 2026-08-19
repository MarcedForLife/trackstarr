"""Per-caller credentials for the webhook listener.

One file per caller under ``STATE_DIR/webhook-secrets``, holding a SHA-256
digest rather than the secret. Nothing ever reads a secret back, so a copy
of the config volume yields nothing replayable.

A bare digest, not a password hash: these are 256-bit ``token_urlsafe``
values, so there is no dictionary to salt against. Save the KDF for
credentials a human chose.
"""

import hashlib
import hmac
import os
import re
import secrets

from . import config

#: A bare filename, so a name can never reach outside the secrets directory.
#: Reads trust the same shape, so dotfiles in there are never credentials.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _secrets_dir() -> str:
    return os.path.join(config.STATE_DIR, "webhook-secrets")


def _path(name: str) -> str:
    return os.path.join(_secrets_dir(), name)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _write(name: str, stored: str) -> None:
    """Replace one secret file atomically.

    A reader authorising a request at that moment sees the old digest or the
    new one, never half a line. The staging name is a dotfile, which _NAME_RE
    rejects, so one left by a crash is never read back as a caller.
    """
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

    This return value is the only copy, so whatever asked for it has to deliver
    it now. Minting again replaces the old secret immediately.

    Raises ValueError for a name that isn't a bare filename, and OSError when
    STATE_DIR cannot hold the file: a secret we could not check later would be
    handed out and never accepted.
    """
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"secret name {name!r} must be letters, digits, dots, dashes and underscores"
        )
    secret = secrets.token_urlsafe(32)
    _write(name, _digest(secret))
    return secret


def exists(name: str) -> bool:
    """Whether ``name`` has a secret, without saying anything about it."""
    return bool(_stored(name))


def matches(name: str, given: str) -> bool:
    """Whether ``given`` is the secret held for ``name``.

    Compared as bytes: compare_digest refuses a str with non-ASCII in it,
    which a hand-written file could have.
    """
    stored = _stored(name)
    if not (given and stored):
        return False
    return hmac.compare_digest(_digest(given).encode(), stored.encode())


def authorized(given: str) -> bool:
    """Whether a presented value matches any provisioned caller's secret."""
    return any(matches(name, given) for name in names())
