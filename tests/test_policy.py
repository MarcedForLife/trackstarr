"""The Policy snapshot and its fingerprint. No media, no network."""

import dataclasses
import json
import re

import pytest

from trackstarr import config, policy
from trackstarr.layouts import resolved_layouts
from trackstarr.policy import Policy


def test_fingerprint_covers_every_field():
    """Derived from the fields, so a setting added to Policy can never be
    forgotten by the sweep cache the way a hand-maintained list could."""
    fingerprint = Policy.from_config().fingerprint()
    field_names = {field.name for field in dataclasses.fields(Policy)}
    assert set(fingerprint) == {"version", *field_names}


#: A config change per Policy field, for the variance test below. A new
#: field needs an entry here (the parametrize fails loudly without one),
#: which is the moment to make sure from_config actually populates it.
_FIELD_CHANGES = {
    "always_keep": ("ALWAYS_KEEP", {"eng", "fre"}),
    "allowed_exts": ("ALLOWED_EXTS", {".mkv"}),
    "disabled_rules": ("DISABLED_RULES", {"sdh"}),
    "drop_commentary": ("DROP_COMMENTARY", True),
    "regenerate_downmixes": ("REGENERATE_DOWNMIXES", "generated"),
    "remux_to_mkv": ("REMUX_TO_MKV", True),
    "downmix_layouts": ("DOWNMIX_LAYOUTS", {"2.0", "7.1"}),
    "audio_codec": ("AUDIO_CODEC", "libfdk_aac"),
    "skip_hardlinks": ("SKIP_HARDLINKS", True),
    "commentary_re": ("COMMENTARY_RE", re.compile("changed", re.IGNORECASE)),
    "sdh_re": ("SDH_RE", re.compile("changed", re.IGNORECASE)),
    "forced_re": ("FORCED_RE", re.compile("changed", re.IGNORECASE)),
    "junk_title_re": ("JUNK_TITLE_RE", re.compile("changed", re.IGNORECASE)),
}

#: Not config-driven: a code constant, covered by the version field.
_CONSTANT_FIELDS = {"image_codecs"}


@pytest.mark.parametrize(
    "field_name",
    [field.name for field in dataclasses.fields(Policy) if field.name not in _CONSTANT_FIELDS],
)
def test_every_field_varies_with_its_setting(monkeypatch, field_name):
    """from_config must populate every field from config. A field with a
    forgotten mapping would fingerprint as a constant and never invalidate
    the cache, the exact failure the hand-maintained list had."""
    before = Policy.from_config().fingerprint()
    setting, changed = _FIELD_CHANGES[field_name]
    monkeypatch.setattr(config, setting, changed)
    after = Policy.from_config().fingerprint()
    assert after[field_name] != before[field_name]


def test_fingerprint_is_json_serialisable():
    json.dumps(Policy.from_config().fingerprint())


def test_fingerprint_tracks_the_bitrate_through_resolved_layouts(monkeypatch):
    """AUDIO_BITRATE is not a field of its own; it reaches the fingerprint
    through the resolved layout rates, and must still invalidate on change."""
    before = Policy.from_config().fingerprint()
    monkeypatch.setattr(config, "AUDIO_BITRATE", "128k")
    assert Policy.from_config().fingerprint() != before


# What policy.errors() refuses at startup. Every one of these would otherwise
# fail silently: a typo leaves a rule on, drops a layout, or regenerates
# nothing.


def test_a_container_with_no_muxer_is_refused_at_startup(monkeypatch):
    """ALLOWED_EXTS drives the walk, so an extension ffmpeg cannot mux would
    be collected all sweep and then fail one file at a time."""
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
    monkeypatch.setattr(
        config, "DOWNMIX_LAYOUTS", {"2.0", "surround", "5:1", "0.0", "5.1:640x"}
    )
    errors = policy.errors()
    assert len(errors) == 1
    assert all(bad in errors[0] for bad in ("surround", "5:1", "0.0", "5.1:640x"))
    assert [(layout.name, layout.channels) for layout in resolved_layouts()] == [("2.0", 2)]


def test_duplicate_channel_counts_are_refused_at_startup(monkeypatch):
    """Two entries for one channel count generate identical tracks and leave
    the rules judging against an arbitrary one of the rates."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", {"5.1", "5.1:640k"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "5.1, 5.1:640k" in errors[0]
