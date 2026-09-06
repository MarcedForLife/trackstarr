"""The session store: opaque tokens out, digests at rest, absolute expiry."""

import json
import os

import pytest

from trackstarr import config, sessions
from trackstarr.users import Account

ADMIN = Account("admin", "admin", False)


def store_path() -> str:
    return os.path.join(config.STATE_DIR, sessions.SESSIONS_FILE)


def read_store() -> dict:
    with open(store_path()) as store_file:
        return json.load(store_file)


def write_store(records: dict) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(store_path(), "w") as store_file:
        json.dump(records, store_file)


def test_the_token_itself_is_never_written_down():
    token = sessions.create(ADMIN)
    on_disk = read_store()
    assert token not in json.dumps(on_disk)
    (digest,) = on_disk
    assert digest == sessions._digest(token)
    assert os.stat(store_path()).st_mode & 0o777 == 0o600
    assert sessions.get(token) == ADMIN


def test_the_session_carries_the_account_it_was_minted_for():
    token = sessions.create(Account("watcher", "viewer", True))
    assert sessions.get(token) == Account("watcher", "viewer", True)


def test_nothing_comes_back_for_an_empty_or_guessed_token():
    sessions.create(ADMIN)
    assert sessions.get("") is None
    assert sessions.get("guessed") is None


def test_a_session_expires_and_is_pruned_by_the_next_write(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sessions, "_now", lambda: clock[0])
    stale = sessions.create(ADMIN)
    clock[0] += sessions.TTL + 1
    assert sessions.get(stale) is None

    fresh = sessions.create(ADMIN)
    assert sessions.get(fresh) == ADMIN
    # The write that minted the fresh session swept the stale row out.
    assert list(read_store()) == [sessions._digest(fresh)]


def test_revoke_signs_out_one_browser():
    kept = sessions.create(ADMIN)
    revoked = sessions.create(ADMIN)
    sessions.revoke(revoked)
    assert sessions.get(revoked) is None
    assert sessions.get(kept) == ADMIN


def test_revoke_user_signs_out_every_browser_but_nobody_else():
    mine_here = sessions.create(ADMIN)
    mine_there = sessions.create(ADMIN)
    theirs = sessions.create(Account("watcher", "viewer", False))
    sessions.revoke_user("admin")
    assert sessions.get(mine_here) is None
    assert sessions.get(mine_there) is None
    assert sessions.get(theirs) is not None


@pytest.mark.parametrize("expires", [None, "soon", [1]], ids=["missing", "text", "list"])
def test_a_record_with_a_damaged_expiry_is_dead(expires):
    """A hand-edited row must fail closed: a session that cannot say when it
    ends does not get to never end."""
    token = sessions.create(ADMIN)
    record = {"name": "admin", "role": "admin", "must_change": False}
    if expires is not None:
        record["expires"] = expires
    write_store({sessions._digest(token): record})
    assert sessions.get(token) is None


def test_an_unwritable_state_dir_fails_the_sign_in_not_the_lookup(tmp_path, monkeypatch):
    """create has to raise, or the cookie it answered with would never work;
    reads just see no sessions."""
    blocker = tmp_path / "a-file"
    blocker.write_bytes(b"")
    monkeypatch.setattr(config, "STATE_DIR", str(blocker / "under-a-file"))
    with pytest.raises(OSError):
        sessions.create(ADMIN)
    with pytest.raises(OSError):
        sessions.revoke("token")
    assert sessions.get("token") is None
