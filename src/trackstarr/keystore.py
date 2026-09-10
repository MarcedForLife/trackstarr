"""The key that seals the credentials in ``settings.json``.

Service credentials are replayed on every call, so they cannot be hashed like
webhook secrets; a key typed into the connections page lands in the settings
file as ciphertext. The key is machine-held, not password-derived, because a
daemon restarting at 3am needs its credentials unattended.

This protects a settings file that leaves the volume (a backup, a config
pasted into an issue), not against an attacker who has the directory, since
the default key file sits in it. ``TRACKSTARR_KEY_FILE`` can point at a
mounted secret instead.

A leaf module: it takes the directory rather than reading
:mod:`trackstarr.config`, which imports this.
"""

import base64
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

#: Marks and versions the sealed form, so a later cipher can be told from this
#: one. Nothing else may start "enc:".
PREFIX = "enc:v1:"

#: The default key file, beside the file it opens.
KEY_FILE = "key"

#: Points at a key kept outside STATE_DIR, such as a docker secret. Environment
#: only: it cannot come from the file this key opens.
KEY_VARIABLE = "TRACKSTARR_KEY_FILE"

_KEY_BYTES = 32
_NONCE_BYTES = 12


def key_path(state_dir: str) -> str:
    return os.environ.get(KEY_VARIABLE, "").strip() or os.path.join(state_dir, KEY_FILE)


def _parse(content: str, path: str) -> bytes:
    """A key file's hex as bytes. Raises ValueError on damage: a short key is a
    clipped file, and sealing under it would lose the next save."""
    try:
        key = bytes.fromhex(content.strip())
    except ValueError as err:
        raise ValueError(f"{path} is not the hex a key file holds ({err})") from err
    if len(key) != _KEY_BYTES:
        raise ValueError(f"{path} holds {len(key)} bytes; a key is {_KEY_BYTES}")
    return key


def read(state_dir: str) -> bytes | None:
    """The key, or None when no key file exists yet, which is the ordinary
    state of a deploy that has sealed nothing. Raises ValueError on damage."""
    path = key_path(state_dir)
    try:
        with open(path) as key_file:
            content = key_file.read()
    except FileNotFoundError:
        if os.environ.get(KEY_VARIABLE, "").strip():
            # Named explicitly and missing: a mount that failed. Minting one
            # would quietly re-seal every credential under a key that opens
            # nothing.
            raise ValueError(f"{KEY_VARIABLE}={path!r} does not exist") from None
        return None
    except OSError as err:
        raise ValueError(f"{path} could not be read ({err})") from err
    return _parse(content, path)


def ensure(state_dir: str) -> bytes:
    """The key, minting one when there is none. Raises ValueError, OSError.
    Called only by a write with a credential to seal."""
    existing = read(state_dir)
    if existing is not None:
        return existing
    key = secrets.token_bytes(_KEY_BYTES)
    path = key_path(state_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # O_EXCL, so two processes racing to a fresh volume cannot each mint a key.
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race; its key is the one on disk.
        raced = read(state_dir)
        if raced is None:
            raise ValueError(f"{path} appeared and vanished while minting a key") from None
        return raced
    with os.fdopen(descriptor, "w") as key_file:
        key_file.write(key.hex() + "\n")
    return key


def sealed(value: str) -> bool:
    return value.startswith(PREFIX)


def seal(key: bytes, name: str, value: str) -> str:
    """``value`` sealed for the settings file. The setting's name is
    authenticated with it, so a ciphertext moved to another name fails to
    open."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    box = ChaCha20Poly1305(key).encrypt(nonce, value.encode(), name.encode())
    return PREFIX + base64.urlsafe_b64encode(nonce + box).decode()


def unseal(key: bytes, name: str, value: str) -> str:
    """What :func:`seal` was given. Raises ValueError for the wrong key, a
    damaged value, or a ciphertext filed under another name."""
    try:
        # binascii.Error is a ValueError, so one clause covers both.
        raw = base64.urlsafe_b64decode(value.removeprefix(PREFIX))
    except ValueError as err:
        raise ValueError(f"{name} is not the base64 a sealed value is ({err})") from err
    nonce, box = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, box, name.encode()).decode()
    except InvalidTag:
        raise ValueError(
            f"{name} was sealed with a different key than {KEY_FILE} holds"
        ) from None
    except UnicodeDecodeError as err:
        raise ValueError(f"{name} did not open as text ({err})") from err
