"""The Policy snapshot and its fingerprint. No media, no network."""

import dataclasses
import json
import re

import pytest

from trackstarr import config, policy
from trackstarr.layouts import encode_settings, resolved_layouts
from trackstarr.policy import Policy


def test_fingerprint_covers_every_field():
    """Derived from the fields, so a setting added to Policy can never be forgotten
    the way a hand-maintained list could."""
    fingerprint = Policy.from_config().fingerprint()
    field_names = {field.name for field in dataclasses.fields(Policy)}
    assert set(fingerprint) == {"version", *field_names}


#: A config change per Policy field, for the variance test below. A new
#: field needs an entry here (the parametrize fails loudly without one),
#: which is the moment to make sure from_config actually populates it.
_FIELD_CHANGES = {
    "always_keep": ("ALWAYS_KEEP_LANGS", {"eng", "fre"}),
    "allowed_exts": ("ALLOWED_EXTS", {".mkv"}),
    "disabled_rules": ("DISABLED_RULES", {"sdh"}),
    "drop_commentary": ("DROP_COMMENTARY", True),
    "regenerate_downmixes": ("REGENERATE_DOWNMIXES", "generated"),
    "remux_to_mkv": ("REMUX_TO_MKV", True),
    "downmix_layouts": ("DOWNMIX_LAYOUTS", {"2.0", "7.1"}),
    "audio_codec": ("AUDIO_CODEC", "libfdk_aac"),
    "skip_hardlinks": ("SKIP_HARDLINKS", False),
    "commentary_re": ("COMMENTARY_RE", re.compile("changed", re.IGNORECASE)),
    "sdh_re": ("SDH_RE", re.compile("changed", re.IGNORECASE)),
    "forced_re": ("FORCED_RE", re.compile("changed", re.IGNORECASE)),
    "junk_title_re": ("JUNK_TITLE_RE", re.compile("changed", re.IGNORECASE)),
}


@pytest.mark.parametrize("field_name", [field.name for field in dataclasses.fields(Policy)])
def test_every_field_varies_with_its_setting(monkeypatch, field_name):
    """from_config has to populate every field. A forgotten mapping would
    fingerprint as a constant and never invalidate the cache, the exact
    failure the hand-maintained list had."""
    before = Policy.from_config().fingerprint()
    setting, changed = _FIELD_CHANGES[field_name]
    monkeypatch.setattr(config, setting, changed)
    after = Policy.from_config().fingerprint()
    assert after[field_name] != before[field_name]


def test_fingerprint_is_json_serialisable():
    json.dumps(Policy.from_config().fingerprint())


def test_the_digest_identifies_the_policy_it_was_taken_from(monkeypatch):
    """Every event carries one. Unstable across calls and one generation reads as
    two; unchanged across an edit and the trace lies."""
    assert Policy.from_config().digest() == Policy.from_config().digest()
    before = Policy.from_config().digest()
    monkeypatch.setattr(config, "AUDIO_CODEC", "libfdk_aac")
    assert Policy.from_config().digest() != before


def test_the_digest_does_not_depend_on_key_order():
    """Two dicts of the same settings have to hash alike, or a Python release that
    reorders anything splits one generation into two."""
    policy_now = Policy.from_config()
    shuffled = dict(reversed(list(policy_now.fingerprint().items())))
    assert json.dumps(shuffled, sort_keys=True) == json.dumps(
        policy_now.fingerprint(), sort_keys=True
    )


def test_the_tag_is_written_even_for_a_rate_nothing_can_parse():
    """Startup refuses such a rate on a configured layout, so only a hand-built
    stream reaches this. encode_settings writes the tag a later pass
    identifies our tracks by, and must never be what fails a rewrite."""
    assert encode_settings("aac", "0.2M") == "aac 0.2M"


def test_fingerprint_tracks_the_bitrate_through_resolved_layouts(monkeypatch):
    """The layout rates reach the fingerprint through downmix_layouts rather than
    a field of their own, and still have to invalidate on change, or a sweep
    keeps serving verdicts judged at the old rate."""
    before = Policy.from_config().fingerprint()
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "128k", "5.1": "640k"})
    assert Policy.from_config().fingerprint() != before


# What policy.errors() refuses at startup. Every one of these would otherwise
# fail silently: a typo leaves a rule on, drops a layout, or regenerates
# nothing.


def test_a_container_with_no_muxer_is_refused_at_startup(monkeypatch):
    """ALLOWED_EXTS drives the walk, so an extension ffmpeg cannot mux would be
    collected all sweep and fail one file at a time."""
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mkv", ".rmvb"})
    problems = policy.errors()
    assert any(".rmvb" in problem for problem in problems)
    assert any("no known muxer" in problem for problem in problems)


def test_unknown_rule_names_are_refused(monkeypatch):
    monkeypatch.setattr(config, "DISABLED_RULES", {"languages", "subtitles"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "subtitles" in errors[0]


def test_unknown_regenerate_mode_is_refused(monkeypatch):
    """A typo, or a bare "true", would silently regenerate nothing."""
    monkeypatch.setattr(config, "REGENERATE_DOWNMIXES", "everything")
    errors = policy.errors()
    assert len(errors) == 1
    assert "REGENERATE_DOWNMIXES" in errors[0]


def test_invalid_layouts_catch_typos(monkeypatch):
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0", "surround", "5:1", "0.0"})
    errors = policy.errors()
    assert len(errors) == 1
    assert all(bad in errors[0] for bad in ("surround", "5:1", "0.0"))
    # A name that is not a layout is never asked for a rate: it has no
    # variable to set, so saying it lacks one would send nobody anywhere.
    assert [(layout.name, layout.channels) for layout in resolved_layouts()] == [("2.0", 2)]


def test_the_shipped_layout_defaults_resolve_as_documented():
    """The two layouts the downmix rule guarantees, at the rates a library inherits
    without configuring anything."""
    assert [(layout.name, layout.bitrate) for layout in resolved_layouts()] == [
        ("2.0", "320k"),
        ("5.1", "640k"),
    ]


@pytest.mark.parametrize("rate", ["320k", "320000", " 320K "])
def test_one_rate_has_one_spelling(monkeypatch, rate):
    """The resolved rate goes into the file as a stream tag a later pass compares
    for equality, so two spellings would read as a settings change and
    regenerate every track we have made."""
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": rate})
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0"})
    assert [layout.bitrate for layout in resolved_layouts()] == ["320k"]


def test_a_layout_with_no_rate_is_refused_at_startup(monkeypatch):
    """Nothing is derived from anything else, so a layout past the two shipped
    defaults needs a rate. Skipping it would drop the layout just asked for."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"2.0", "7.1"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "7.1 with no rate" in errors[0]
    assert "AUDIO_BITRATE_7_1" in errors[0]


def test_a_layout_rate_that_is_not_a_bitrate_is_refused(monkeypatch):
    """Named separately from the layout, so the report says which is wrong."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"5.1"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"5.1": "loud"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "AUDIO_BITRATE_5_1='loud'" in errors[0]


def test_duplicate_channel_counts_are_refused_at_startup(monkeypatch):
    """Two entries for one channel count generate identical tracks and leave the
    rules judging against an arbitrary rate."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"4.2", "5.1"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"4.2": "640k", "5.1": "640k"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "4.2, 5.1" in errors[0]
