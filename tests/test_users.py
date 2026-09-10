"""The account store: hashing, roles, bootstrap and login throttling."""

import json
import logging
import os

import pytest

from conftest import set_config
from trackstarr import config, users


def store_path() -> str:
    return os.path.join(config.STATE_DIR, users.USERS_FILE)


def read_store() -> dict:
    with open(store_path()) as store_file:
        return json.load(store_file)


def write_store(records: dict) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store_file:
        json.dump(records, store_file)


def test_passwords_are_stored_only_as_scrypt_hashes():
    """The real cost settings, once: the format has to say what it cost, and
    the password itself must never touch the disk."""
    users.add("admin", "correct horse battery", "admin")
    stored = read_store()["admin"]["hash"]
    assert stored.startswith("scrypt$32768$8$1$")
    assert "correct horse battery" not in stored
    assert os.stat(store_path()).st_mode & 0o777 == 0o600
    assert users.verify("admin", "correct horse battery") == users.Account(
        "admin", "admin", False
    )
    assert users.verify("admin", "wrong") is None


def test_an_unknown_name_answers_none_at_the_same_cost(fast_scrypt):
    """Twice, so the lazily built timing dummy is also reused."""
    users.add("admin", "a password", "admin")
    assert users.verify("nobody", "a password") is None
    assert users.verify("nobody", "a password") is None


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "nonsense",
        "md5$32768$8$1$aa$bb",  # not our scheme
        "scrypt$3$8$1$aa$bb",  # n must be a power of two
        "scrypt$1048576$8$1$aa$bb",  # parameters past the memory cap
        "scrypt$32768$8$1$zz$bb",  # salt is not hex
    ],
)
def test_a_damaged_hash_matches_nothing(stored):
    """A mangled store locks its account out; it must never crash the
    listener or let a crafted file balloon a login's memory."""
    assert not users._matches(stored, "anything")


def test_add_refuses_bad_names_roles_and_duplicates(fast_scrypt):
    with pytest.raises(ValueError, match="letters, digits"):
        users.add("../escape", "a password", "admin")
    with pytest.raises(ValueError, match="role"):
        users.add("admin", "a password", "root")
    users.add("admin", "a password", "admin")
    with pytest.raises(ValueError, match="already exists"):
        users.add("admin", "another", "viewer")


def test_unknown_roles_read_as_viewer(fast_scrypt):
    """The harmless direction for a hand-edited file."""
    users.add("someone", "a password", "viewer")
    records = read_store()
    records["someone"]["role"] = "root"
    write_store(records)
    assert users.verify("someone", "a password").role == "viewer"


def test_set_password_replaces_and_signs_off_the_forced_change(fast_scrypt):
    users.add("admin", "first password", "admin", must_change=True)
    users.set_password("admin", "second password")
    assert users.verify("admin", "first password") is None
    assert users.verify("admin", "second password") == users.Account("admin", "admin", False)
    with pytest.raises(ValueError, match="does not exist"):
        users.set_password("nobody", "whatever")


def test_remove_protects_the_last_admin(fast_scrypt):
    """A store nobody can administer only comes back by editing the volume."""
    users.add("admin", "a password", "admin")
    users.add("watcher", "a password", "viewer")
    with pytest.raises(ValueError, match="last admin"):
        users.remove("admin")
    users.remove("watcher")
    with pytest.raises(ValueError, match="does not exist"):
        users.remove("watcher")
    assert [held.name for held in users.accounts()] == ["admin"]


def test_a_second_admin_makes_the_first_removable(fast_scrypt):
    users.add("admin", "a password", "admin")
    users.add("backup", "a password", "admin")
    users.remove("admin")
    assert [held.name for held in users.accounts()] == ["backup"]


def test_accounts_lists_sorted(fast_scrypt):
    users.add("zoe", "a password", "viewer")
    users.add("amy", "a password", "admin", must_change=True)
    assert users.accounts() == [
        users.Account("amy", "admin", True),
        users.Account("zoe", "viewer", False),
    ]


def test_ensure_admin_generates_a_password_and_forces_its_change(fast_scrypt, caplog):
    """The generated password lands in docker logs, which are no secret
    store, so the first sign-in has to replace it."""
    with caplog.at_level(logging.WARNING):
        users.ensure_admin()
    (record,) = [rec for rec in caplog.records if "created the admin user" in rec.message]
    password = record.args[0]
    assert users.verify("admin", password) == users.Account("admin", "admin", True)


def test_ensure_admin_honours_admin_password(fast_scrypt, caplog):
    """An operator's chosen credential is taken verbatim and not printed."""
    set_config(ADMIN_PASSWORD="from-the-compose-file")
    with caplog.at_level(logging.INFO):
        users.ensure_admin()
    assert users.verify("admin", "from-the-compose-file") == users.Account(
        "admin", "admin", False
    )
    assert "from-the-compose-file" not in caplog.text


def test_ensure_admin_never_touches_an_existing_store(fast_scrypt):
    """After first run, passwords change in the UI or the CLI; a restart with
    ADMIN_PASSWORD still set must not quietly reset one."""
    set_config(ADMIN_PASSWORD="a newer password")
    users.add("admin", "the chosen one", "admin")
    users.ensure_admin()
    assert users.verify("admin", "the chosen one")
    assert users.verify("admin", "a newer password") is None


def test_a_record_that_is_not_an_object_is_dropped(fast_scrypt):
    write_store({"admin": "not-a-record"})
    assert users.accounts() == []
    assert users.verify("admin", "anything") is None


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_a_damaged_store_reads_as_empty(content, caplog):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store_file:
        store_file.write(content)
    assert users.accounts() == []
    assert users.USERS_FILE in caplog.text


def test_an_unreadable_store_reads_as_empty(caplog):
    os.makedirs(store_path())  # a directory where the file should be
    assert users.accounts() == []
    assert "ignoring unreadable" in caplog.text


def test_five_failures_park_a_name_for_a_minute(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(users, "_now", lambda: clock[0])
    for _ in range(users._LOCK_AFTER):
        users.note_failure("admin")
    assert users.locked("admin")
    assert not users.locked("someone-else")

    clock[0] = users._LOCK_SECONDS
    assert not users.locked("admin")


def test_a_quiet_minute_forgives_earlier_failures(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(users, "_now", lambda: clock[0])
    for _ in range(users._LOCK_AFTER - 1):
        users.note_failure("admin")
    clock[0] = users._LOCK_SECONDS
    users.note_failure("admin")  # the count restarts at one, not five
    assert not users.locked("admin")


def test_a_success_clears_the_count(monkeypatch):
    monkeypatch.setattr(users, "_now", lambda: 0.0)
    for _ in range(users._LOCK_AFTER):
        users.note_failure("admin")
    users.note_success("admin")
    assert not users.locked("admin")
