"""The verdict file's envelope. No ownership, no views, no policy."""

import json
from pathlib import Path

from trackstarr import verdict_store
from trackstarr.verdict_store import FORMAT, Document

RULES = {"remux": "always"}


def store(tmp_path) -> str:
    return str(tmp_path / "sweep-cache.json")


def test_a_missing_store_is_empty_rather_than_damaged(tmp_path):
    """Nothing has swept yet. A verdict may still be written, which is what
    tells this apart from a file that cannot be read."""
    assert verdict_store.load(store(tmp_path)) == Document()


def test_a_store_that_is_not_an_object_is_refused(tmp_path):
    """Valid JSON in nothing like the right shape. There is nowhere to file a
    verdict, so the caller leaves the file alone."""
    Path(store(tmp_path)).write_text(json.dumps(["not", "a", "store"]))
    assert verdict_store.load(store(tmp_path)) is None


def test_another_builds_entries_are_never_handed_out(tmp_path):
    """The format is reported so a caller can say why it dropped the store,
    but its entries are not, since their fields are anyone's guess."""
    Path(store(tmp_path)).write_text(
        json.dumps({"format": FORMAT - 1, "config": RULES, "files": {"/a.mkv": {}}})
    )
    document = verdict_store.load(store(tmp_path))
    assert document == Document(present=True, known=False, fingerprint=RULES)


def test_an_entry_that_is_not_an_object_is_dropped(tmp_path):
    """A hand-edit or a half-written file: one bad entry costs that file's
    verdict, not the whole store."""
    Path(store(tmp_path)).write_text(
        json.dumps({"format": FORMAT, "config": RULES, "files": {"/a.mkv": "?", "/b.mkv": {}}})
    )
    assert verdict_store.load(store(tmp_path)).entries == {"/b.mkv": {}}


def test_what_is_written_reads_back_under_the_rules_it_was_judged_by(tmp_path):
    verdict_store.write(store(tmp_path), RULES, {"/a.mkv": {"status": "conform"}})
    assert verdict_store.load(store(tmp_path)) == Document(
        {"/a.mkv": {"status": "conform"}}, present=True, fingerprint=RULES
    )
