"""The key that seals service credentials, and what it refuses to open."""

import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from trackstarr import keystore


def test_a_sealed_credential_comes_back_and_does_not_show(tmp_path):
    key = keystore.ensure(str(tmp_path))
    sealed = keystore.seal(key, "PLEX_TOKEN", "sekrit")
    assert keystore.sealed(sealed)
    assert "sekrit" not in sealed
    assert keystore.unseal(key, "PLEX_TOKEN", sealed) == "sekrit"


def test_the_same_credential_seals_differently_every_time(tmp_path):
    """A fresh nonce per write, so the file never says two services share a
    key, or that a saved one is the value it was three saves ago."""
    key = keystore.ensure(str(tmp_path))
    first = keystore.seal(key, "PLEX_TOKEN", "sekrit")
    second = keystore.seal(key, "PLEX_TOKEN", "sekrit")
    assert first != second
    assert keystore.unseal(key, "PLEX_TOKEN", second) == "sekrit"


def test_another_key_does_not_open_it(tmp_path):
    sealed = keystore.seal(keystore.ensure(str(tmp_path)), "PLEX_TOKEN", "sekrit")
    other = keystore.ensure(str(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="different key"):
        keystore.unseal(other, "PLEX_TOKEN", sealed)


def test_a_credential_moved_to_another_name_does_not_open(tmp_path):
    """The name is authenticated with the value, so a hand edit cannot point
    Radarr at the token that belongs to Plex."""
    key = keystore.ensure(str(tmp_path))
    sealed = keystore.seal(key, "PLEX_TOKEN", "sekrit")
    with pytest.raises(ValueError, match="different key"):
        keystore.unseal(key, "RADARR_API_KEY", sealed)


def test_damage_refuses_rather_than_returning_something(tmp_path):
    key = keystore.ensure(str(tmp_path))
    sealed = keystore.seal(key, "PLEX_TOKEN", "sekrit")
    with pytest.raises(ValueError):
        keystore.unseal(key, "PLEX_TOKEN", sealed[:-4])
    with pytest.raises(ValueError, match="base64"):
        keystore.unseal(key, "PLEX_TOKEN", keystore.PREFIX + "not base64 at all!")


def test_no_key_file_is_not_an_error_until_something_is_sealed(tmp_path):
    """A deploy that sets its credentials by environment or *_FILE mount
    never seals anything, and must not grow a key file for the privilege."""
    assert keystore.read(str(tmp_path)) is None
    assert not os.path.exists(tmp_path / keystore.KEY_FILE)


def test_the_minted_key_is_private_and_stable(tmp_path):
    key = keystore.ensure(str(tmp_path))
    path = tmp_path / keystore.KEY_FILE
    assert os.stat(path).st_mode & 0o777 == 0o600
    # Minting is once: a second call must not replace the key that already
    # opens everything saved under it.
    assert keystore.ensure(str(tmp_path)) == key
    assert keystore.read(str(tmp_path)) == key


def test_a_mount_that_did_not_appear_is_refused_not_replaced(tmp_path, monkeypatch):
    """The failure mode worth naming: a docker secret that did not mount
    would otherwise mint a key that opens none of the saved credentials, and
    then re-seal them under it."""
    missing = tmp_path / "secrets" / "trackstarr_key"
    monkeypatch.setenv(keystore.KEY_VARIABLE, str(missing))
    with pytest.raises(ValueError, match=keystore.KEY_VARIABLE):
        keystore.read(str(tmp_path))
    with pytest.raises(ValueError, match=keystore.KEY_VARIABLE):
        keystore.ensure(str(tmp_path))
    assert not missing.exists()


def test_a_key_kept_outside_the_volume_is_the_one_used(tmp_path, monkeypatch):
    """A mounted key: the volume holds ciphertext and nothing that opens it. A
    named key is never minted; see the test above."""
    mounted = tmp_path / "secrets" / "trackstarr_key"
    mounted.parent.mkdir()
    mounted.write_text("11" * 32 + "\n")
    monkeypatch.setenv(keystore.KEY_VARIABLE, str(mounted))
    assert keystore.ensure(str(tmp_path)) == bytes.fromhex("11" * 32)
    assert keystore.read(str(tmp_path)) == bytes.fromhex("11" * 32)
    # And nothing was left beside the settings file it opens.
    assert not os.path.exists(tmp_path / keystore.KEY_FILE)


@pytest.mark.parametrize("content", ["", "not hex", "abcd", "zz" * 32])
def test_a_damaged_key_file_refuses_rather_than_guessing(tmp_path, content):
    """A clipped key carried on with would seal the next save under something
    no later start reproduces."""
    (tmp_path / keystore.KEY_FILE).write_text(content)
    with pytest.raises(ValueError):
        keystore.read(str(tmp_path))


def test_a_key_file_that_will_not_open_is_a_stated_error(tmp_path):
    """A directory where the key should be, or a mode nothing can read: the
    caller wants the reason in words, not an OSError from a layer down."""
    (tmp_path / keystore.KEY_FILE).mkdir()
    with pytest.raises(ValueError, match="could not be read"):
        keystore.read(str(tmp_path))


def test_the_process_that_loses_the_race_takes_the_other_key(tmp_path, monkeypatch):
    """Two starts on a fresh volume both find no key. The one that gets there
    second must seal under what the first wrote, not under its own."""
    winner = keystore.ensure(str(tmp_path))
    real_read = keystore.read
    looked = False

    def read(state_dir: str) -> bytes | None:
        """None the first time, which is the look that decided to mint."""
        nonlocal looked
        if looked:
            return real_read(state_dir)
        looked = True
        return None

    monkeypatch.setattr(keystore, "read", read)
    assert keystore.ensure(str(tmp_path)) == winner


def test_a_key_that_appears_and_vanishes_is_refused(tmp_path, monkeypatch):
    """Minting was refused because a file was already there, and then the
    file was gone. There is no key to seal under, and saying so beats
    minting a second one the first save's neighbours will not open."""
    (tmp_path / keystore.KEY_FILE).write_text("22" * 32 + "\n")
    monkeypatch.setattr(keystore, "read", lambda state_dir: None)
    with pytest.raises(ValueError, match="appeared and vanished"):
        keystore.ensure(str(tmp_path))


def test_a_sealed_value_that_is_not_text_refuses(tmp_path):
    """Nothing here seals bytes, so this is a hand-edited or corrupted entry
    whose tag still checks out. It has to fail as a value, not as a decode
    error escaping from the cipher."""
    key = keystore.ensure(str(tmp_path))
    box = ChaCha20Poly1305(key).encrypt(b"\x00" * 12, b"\xff\xfe", b"PLEX_TOKEN")
    value = keystore.PREFIX + base64.urlsafe_b64encode(b"\x00" * 12 + box).decode()
    with pytest.raises(ValueError, match="did not open as text"):
        keystore.unseal(key, "PLEX_TOKEN", value)
