"""The Policy snapshot and its fingerprint. No media, no network."""

import dataclasses
import json
import re

import pytest

from conftest import set_rules
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
    "keep_original_lang": ("KEEP_ORIGINAL_LANG", False),
    "allowed_exts": ("ALLOWED_EXTS", {".mkv"}),
    "rule_modes": ("RULE_MODES", {"sdh": "never"}),
    "regenerate_scope": ("REGENERATE_SCOPE", "all"),
    "regenerate_below": ("REGENERATE_BELOW_PERCENT", 40),
    "downmix_layouts": ("DOWNMIX_LAYOUTS", ("2.0", "7.1")),
    "downmix_original_lang": ("DOWNMIX_ORIGINAL_LANG", False),
    "downmix_langs": ("DOWNMIX_LANGS", {"fre"}),
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
    monkeypatch.setattr(config, "AUDIO_CODECS", {"2.0": "libfdk_aac", "5.1": "ac3"})
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


def test_every_container_we_can_write_is_one_we_recognise():
    """The walk collects VIDEO_EXTS and leaves ALLOWED_EXTS to the plan, which
    is only safe while the second is a subset of the first. A muxable container
    missing here would be a file the rules act on that the sweep never finds."""
    assert policy.MUXERS.keys() <= policy.VIDEO_EXTS


def test_a_film_is_a_film_whatever_it_is_in_and_a_sidecar_is_not(monkeypatch):
    """The split the sweep walks on: an AVI is a film nothing here will rewrite,
    and a .nfo beside it is not a film at all."""
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mkv"})
    rules = Policy.from_config()
    assert rules.is_video("/media/Dune/Dune.avi")
    assert not rules.allowed_container("/media/Dune/Dune.avi")
    assert rules.is_video("/media/Dune/Dune.mkv")
    assert not rules.is_video("/media/Dune/Dune.nfo")


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
    set_rules(monkeypatch, languages="never", subtitles="never")
    errors = policy.errors()
    assert len(errors) == 1
    assert "RULE_SUBTITLES" in errors[0]


def test_an_unknown_mode_is_refused(monkeypatch):
    """A typo, or a bare "true", would leave the rule doing what it did
    before, which for the ride-alongs is nothing anybody would notice."""
    set_rules(monkeypatch, sdh="sometimes")
    errors = policy.errors()
    assert len(errors) == 1
    assert "RULE_SDH" in errors[0]


def test_unknown_regenerate_scope_is_refused(monkeypatch):
    monkeypatch.setattr(config, "REGENERATE_SCOPE", "everything")
    errors = policy.errors()
    assert len(errors) == 1
    assert "REGENERATE_SCOPE" in errors[0]


def test_every_rule_riding_along_is_a_warning(monkeypatch):
    """Nothing can order a rewrite, so every ride-along waits on one that never
    comes and the library is silently frozen. A warning rather than an error:
    it is a legitimate way to park an install, just not a legible one."""
    set_rules(monkeypatch, **dict.fromkeys(policy.RULES, "alongside"))
    assert any("no rule is set to always" in problem for problem in policy.warnings())
    assert policy.errors() == []


def test_invalid_layouts_catch_typos(monkeypatch):
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0", "surround", "5:1", "0.0"))
    errors = policy.errors()
    assert len(errors) == 1
    assert all(bad in errors[0] for bad in ("surround", "5:1", "0.0"))
    # A name that is not a layout is never asked for a rate: it has no
    # variable to set, so saying it lacks one would send nobody anywhere.
    assert [(layout.name, layout.channels) for layout in resolved_layouts()] == [("2.0", 2)]


def test_the_shipped_layout_defaults_resolve_as_documented():
    """The two shipped layouts with their defaults. Different encoders: AAC is
    what a browser decodes, AC-3 what a receiver takes."""
    assert [(layout.name, layout.codec, layout.bitrate) for layout in resolved_layouts()] == [
        ("2.0", "aac", "320k"),
        ("5.1", "ac3", "640k"),
    ]


def test_resolved_layouts_keep_the_configured_order(monkeypatch):
    """Not sorted: the order rule reads this list straight, so sorting it here
    would quietly overrule the setting."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("5.1", "2.0"))
    assert [layout.name for layout in resolved_layouts()] == ["5.1", "2.0"]


@pytest.mark.parametrize("rate", ["320k", "320000", " 320K "])
def test_one_rate_has_one_spelling(monkeypatch, rate):
    """The resolved rate goes into the file as a stream tag a later pass compares
    for equality, so two spellings would read as a settings change and
    regenerate every track we have made."""
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": rate})
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0",))
    assert [layout.bitrate for layout in resolved_layouts()] == ["320k"]


def test_a_layout_with_no_rate_is_refused_at_startup(monkeypatch):
    """Nothing is derived from anything else, so a layout past the two shipped
    defaults needs a rate. Skipping it would drop the layout just asked for."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0", "7.1"))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"2.0": "aac", "7.1": "aac"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "7.1 with no rate" in errors[0]
    assert "AUDIO_BITRATE_7_1" in errors[0]


def test_a_layout_with_no_encoder_is_refused_at_startup(monkeypatch):
    """Same as the rate beside it: a layout past the two shipped defaults names
    its own encoder, and skipping it would drop the layout just asked for."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0", "7.1"))
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"2.0": "320k", "7.1": "768k"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "7.1 with no encoder" in errors[0]
    assert "AUDIO_CODEC_7_1" in errors[0]


def test_an_encoder_that_cannot_reach_the_layouts_channels_is_refused(monkeypatch):
    """AC-3 tops out at 5.1, and ffmpeg fails outright on the -ac 8 a 7.1 layout
    asks it for. Caught here, or every rewrite of one fails hours later."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("7.1",))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"7.1": "ac3"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"7.1": "768k"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "AUDIO_CODEC_7_1=ac3" in errors[0]
    assert "at most 6 channels" in errors[0]


def test_an_encoder_the_configured_containers_cannot_hold_is_refused(monkeypatch):
    """Opus into MP4 produces a file the players this is all for decline."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0",))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"2.0": "libopus"})
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mkv", ".mp4"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "AUDIO_CODEC_2_0=libopus" in errors[0]
    assert ".mp4" in errors[0]


def test_remuxing_lets_a_matroska_only_encoder_through(monkeypatch):
    """Every rewrite lands as .mkv once the remux rule runs at all, so refusing
    Opus for an MP4 that will not exist on the way out would be wrong."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0",))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"2.0": "libopus"})
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mkv", ".mp4"})
    set_rules(monkeypatch, remux="alongside")
    assert policy.errors() == []


def test_an_encoder_outside_the_table_is_taken_as_typed(monkeypatch):
    """CODECS is what is known, not what is allowed. An ffmpeg carrying
    something exotic must not be refused for it being unlisted; whether the
    binary really has it is executor.audio_codec_errors' question."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0",))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"2.0": "libsomething"})
    assert policy.errors() == []
    assert [layout.codec for layout in resolved_layouts()] == ["libsomething"]


def test_a_lossless_encoder_warns_that_the_rate_does_nothing(monkeypatch):
    """FLAC takes the rate and ignores it, leaving a number on the settings page
    that sizes nothing. Legitimate, so a warning rather than an error."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("2.0",))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"2.0": "flac"})
    (problem,) = policy.warnings()
    assert "AUDIO_CODEC_2_0=flac is lossless" in problem
    assert "AUDIO_BITRATE_2_0" in problem


def test_a_layout_rate_that_is_not_a_bitrate_is_refused(monkeypatch):
    """Named separately from the layout, so the report says which is wrong."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("5.1",))
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"5.1": "loud"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "AUDIO_BITRATE_5_1='loud'" in errors[0]


def test_duplicate_channel_counts_are_refused_at_startup(monkeypatch):
    """Two entries for one channel count generate identical tracks and leave the
    rules judging against an arbitrary rate."""
    monkeypatch.setattr(config, "DOWNMIX_LAYOUTS", ("4.2", "5.1"))
    monkeypatch.setattr(config, "AUDIO_CODECS", {"4.2": "ac3", "5.1": "ac3"})
    monkeypatch.setattr(config, "AUDIO_BITRATES", {"4.2": "640k", "5.1": "640k"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "4.2, 5.1" in errors[0]


# What policy.warnings() says about a rule that reads as on and cannot fire.
# None of these may refuse startup: each is a legitimate half-configured state,
# and each is otherwise invisible, since the rule just never appears in a plan.


def test_the_original_language_downmix_needs_the_languages_rule_to_keep_it(monkeypatch):
    """The downmix is made from a surviving track, so dropping that language
    first leaves the guarantee generating nothing at all."""
    monkeypatch.setattr(config, "DOWNMIX_ORIGINAL_LANG", True)
    monkeypatch.setattr(config, "KEEP_ORIGINAL_LANG", False)
    (problem,) = policy.warnings()
    assert "DOWNMIX_ORIGINAL_LANG" in problem
    assert "KEEP_ORIGINAL_LANG" in problem
    assert policy.errors() == []


def test_no_warning_when_the_languages_rule_is_off_entirely(monkeypatch):
    """Nothing is dropped over language, so the original track survives to be
    downmixed whatever KEEP_ORIGINAL_LANG says."""
    monkeypatch.setattr(config, "DOWNMIX_ORIGINAL_LANG", True)
    monkeypatch.setattr(config, "KEEP_ORIGINAL_LANG", False)
    set_rules(monkeypatch, languages="never")
    assert policy.warnings() == []


def test_remux_with_nothing_to_convert_from_is_a_warning(monkeypatch):
    """Matroska in, Matroska out: the switch reads as on and every file is
    already the container it would be converted to."""
    set_rules(monkeypatch, remux="always")
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mkv"})
    (problem,) = policy.warnings()
    assert "RULE_REMUX" in problem
    assert policy.errors() == []


def test_regenerating_needs_a_container_that_keeps_the_tag(monkeypatch):
    """Regeneration finds its own tracks by a custom stream tag, which MP4 does
    not carry, so an MP4-only library regenerates nothing."""
    set_rules(monkeypatch, regenerate="always")
    monkeypatch.setattr(config, "ALLOWED_EXTS", {".mp4", ".m4v"})
    (problem,) = policy.warnings()
    assert "RULE_REGENERATE" in problem
    assert ".mkv" in problem
    assert policy.errors() == []


def test_the_shipped_defaults_warn_about_nothing():
    """Every warning above is a half-configured state, so a fresh install must
    be silent or they are noise nobody reads."""
    assert policy.warnings() == []
