"""Per-caller credentials for the webhook listener.

One file per caller under ``STATE_DIR/webhook-secrets``, holding a SHA-256
digest rather than the secret itself. The listener only ever asks whether a
presented value matches, and registration only ever writes a value out, so
the plaintext has no reason to outlive the moment it is minted: a copy of
the config volume yields nothing that can be replayed.

A bare digest, not a password hash. These are 256-bit ``token_urlsafe``
values, so there is no dictionary to salt against and nothing to guess; a
KDF would only tax :func:`authorized`, which runs once per imported file.
Save those for credentials a human chose.
"""

import hashlib
import hmac
import os
import re
import secrets

from . import config

#: What may name a secret: a bare filename, so a name can never reach
#: outside the secrets directory. Reads trust the same shape, so dotfiles
#: and editor droppings in there are never mistaken for credentials.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _secrets_dir() -> str:
    return os.path.join(config.STATE_DIR, "webhook-secrets")


def _path(name: str) -> str:
    return os.path.join(_secrets_dir(), name)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _write(name: str, stored: str) -> None:
    """Replace one secret file atomically.

    A reader authorising a request at the same moment sees the old digest
    or the new one, never the half of a line that happened to be flushed.
    The staging name is a dotfile, which _NAME_RE rejects, so one left
    behind by a crash is never read back as a caller of its own.
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

    The plaintext is written nowhere, so this return value is the only copy
    and whatever asked for it must deliver it now. Minting again replaces
    the old secret, which stops being accepted immediately.

    Raises ValueError for a name that isn't a bare filename, and OSError
    when STATE_DIR cannot hold the file, because a secret we cannot check
    later would be handed out but never accepted.
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

    Compared as bytes because compare_digest refuses a str carrying
    non-ASCII, which a file written by hand could.
    """
    stored = _stored(name)
    if not (given and stored):
        return False
    return hmac.compare_digest(_digest(given).encode(), stored.encode())


def authorized(given: str) -> bool:
    """Whether a presented value matches any provisioned caller's secret."""
    return any(matches(name, given) for name in names())
