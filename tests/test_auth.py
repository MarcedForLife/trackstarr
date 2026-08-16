"""The credential store: minting, verification and revocation."""

import os
import re

import pytest

from trackstarr import auth


def stored_file(name: str) -> str:
    return os.path.join(auth._secrets_dir(), name)


def read_stored(name: str) -> str:
    with open(stored_file(name)) as handle:
        return handle.read().strip()


def test_the_secret_itself_is_never_written_down():
    secret = auth.mint("radarr")
    on_disk = read_stored("radarr")
    assert secret not in on_disk
    assert re.fullmatch(r"[0-9a-f]{64}", on_disk)  # a bare sha256 digest
    assert os.stat(stored_file("radarr")).st_mode & 0o777 == 0o600


def test_a_minted_secret_verifies_against_its_digest():
    secret = auth.mint("radarr")
    assert auth.matches("radarr", secret)
    assert auth.authorized(secret)
    assert not auth.matches("radarr", "guessed")
    assert not auth.authorized("guessed")
    assert not auth.authorized("")


def test_each_caller_authenticates_with_its_own_secret():
    radarr_secret = auth.mint("radarr")
    scanner_secret = auth.mint("my-scanner")
    assert radarr_secret != scanner_secret
    assert auth.authorized(radarr_secret) and auth.authorized(scanner_secret)
    # A secret is only ever its own caller's.
    assert not auth.matches("radarr", scanner_secret)


def test_minting_again_replaces_the_old_secret():
    """Losing a secret means rotating, so the replacement has to take effect
    at once rather than leaving both live."""
    first = auth.mint("my-scanner")
    second = auth.mint("my-scanner")
    assert first != second
    assert auth.authorized(second)
    assert not auth.authorized(first)


def test_deleting_a_secret_revokes_only_that_caller():
    radarr_secret = auth.mint("radarr")
    scanner_secret = auth.mint("my-scanner")
    os.unlink(stored_file("my-scanner"))
    assert not auth.authorized(scanner_secret)
    assert auth.authorized(radarr_secret)


def test_exists_reports_provisioning_without_disclosing_anything():
    assert not auth.exists("my-scanner")
    auth.mint("my-scanner")
    assert auth.exists("my-scanner")


def test_names_ignores_files_that_are_not_credentials():
    auth.mint("radarr")
    # An editor dropping or a half-written staging file must never be read
    # back as a caller of its own.
    open(stored_file(".radarr.123.partial"), "w").close()
    open(stored_file(".DS_Store"), "w").close()
    assert auth.names() == ["radarr"]


@pytest.mark.parametrize("name", ["../escape", "sub/dir", "", ".hidden"])
def test_a_name_that_is_not_a_bare_filename_is_refused(name):
    with pytest.raises(ValueError):
        auth.mint(name)
    assert not auth.exists(name)
    assert not auth.matches(name, "anything")


def test_a_non_ascii_secret_file_authorises_nothing():
    """compare_digest refuses a str carrying non-ASCII, so a file written by
    hand must come back as no match rather than an exception in the auth
    path, where it would surface as a broken listener."""
    auth.mint("radarr")
    with open(stored_file("radarr"), "w") as handle:
        handle.write("héllo")
    assert not auth.authorized("anything")
    assert not auth.matches("radarr", "anything")


def test_an_empty_secret_file_authorises_nothing():
    """A truncated file must not turn a blank header into a valid caller."""
    auth.mint("radarr")
    open(stored_file("radarr"), "w").close()
    assert not auth.exists("radarr")
    assert not auth.authorized("")
    assert not auth.matches("radarr", "")


def test_verification_survives_a_missing_state_dir():
    """STATE_DIR is a mount; a request arriving before it exists is a 401,
    not a crash."""
    assert not auth.authorized("anything")
    assert not auth.matches("radarr", "anything")
    assert auth.names() == []
