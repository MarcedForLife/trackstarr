"""The Policy snapshot and its fingerprint. No media, no network."""

import dataclasses
import json
import re

import pytest

from conftest import set_config, set_langs, set_layouts, set_rules
from trackstarr import config, policy, tracks
from trackstarr.policy import Policy
from trackstarr.tracks import Lang, encode_settings, resolved_langs, resolved_layouts


def _layouts() -> tuple[str, ...]:
    """AUDIO_LAYOUTS as the test left it, for the parsers that take entries."""
    return config.current().AUDIO_LAYOUTS


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
    "languages": ("LANGUAGES", ("eng", "fre:keep")),
    "allowed_exts": ("ALLOWED_EXTS", {".mkv"}),
    "rule_modes": ("RULE_MODES", {"sdh": "never"}),
    "regenerate_scope": ("REGENERATE_SCOPE", "all"),
    "regenerate_below": ("REGENERATE_BELOW_PERCENT", 40),
    "regenerate_above": ("REGENERATE_ABOVE_PERCENT", 120),
    "audio_layouts": ("AUDIO_LAYOUTS", ("2.0", "7.1")),
    "skip_hardlinks": ("SKIP_HARDLINKS", False),
    "commentary_re": ("COMMENTARY_RE", re.compile("changed", re.IGNORECASE)),
    "sdh_re": ("SDH_RE", re.compile("changed", re.IGNORECASE)),
    "forced_re": ("FORCED_RE", re.compile("changed", re.IGNORECASE)),
    "release_tag_re": ("RELEASE_TAG_RE", re.compile("changed", re.IGNORECASE)),
}


@pytest.mark.parametrize("field_name", [field.name for field in dataclasses.fields(Policy)])
def test_every_field_varies_with_its_setting(field_name):
    """from_config has to populate every field. A forgotten mapping would
    fingerprint as a constant and never invalidate the cache, the exact
    failure the hand-maintained list had."""
    before = Policy.from_config().fingerprint()
    setting, changed = _FIELD_CHANGES[field_name]
    set_config(**{setting: changed})
    after = Policy.from_config().fingerprint()
    assert after[field_name] != before[field_name]


def test_fingerprint_is_json_serialisable():
    json.dumps(Policy.from_config().fingerprint())


def test_the_digest_identifies_the_policy_it_was_taken_from():
    """Every event carries one. Unstable across calls and one generation reads as
    two; unchanged across an edit and the trace lies."""
    assert Policy.from_config().digest() == Policy.from_config().digest()
    before = Policy.from_config().digest()
    set_layouts("2.0:libfdk_aac:320k", "5.1:ac3:640k")
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


def test_fingerprint_tracks_the_bitrate_through_resolved_layouts():
    """The layout rates reach the fingerprint through audio_layouts rather than
    a field of their own, and still have to invalidate on change, or a sweep
    keeps serving verdicts judged at the old rate."""
    before = Policy.from_config().fingerprint()
    set_layouts("2.0:aac:128k", "5.1:ac3:640k")
    assert Policy.from_config().fingerprint() != before


def test_every_container_we_can_write_is_one_we_recognise():
    """The walk collects VIDEO_EXTS and leaves ALLOWED_EXTS to the plan, which
    is only safe while the second is a subset of the first. A muxable container
    missing here would be a file the rules act on that the sweep never finds."""
    assert policy.MUXERS.keys() <= policy.VIDEO_EXTS


def test_a_film_is_a_film_whatever_it_is_in_and_a_sidecar_is_not():
    """The split the sweep walks on: an AVI is a film nothing here will rewrite,
    and a .nfo beside it is not a film at all."""
    set_config(ALLOWED_EXTS={".mkv"})
    rules = Policy.from_config()
    assert rules.is_video("/media/Dune/Dune.avi")
    assert not rules.allowed_container("/media/Dune/Dune.avi")
    assert rules.is_video("/media/Dune/Dune.mkv")
    assert not rules.is_video("/media/Dune/Dune.nfo")


# What policy.errors() refuses at startup. Every one of these would otherwise
# fail silently: a typo leaves a rule on, drops a layout, or regenerates
# nothing.


def test_a_container_with_no_muxer_is_refused_at_startup():
    """ALLOWED_EXTS drives the walk, so an extension ffmpeg cannot mux would be
    collected all sweep and fail one file at a time."""
    set_config(ALLOWED_EXTS={".mkv", ".rmvb"})
    problems = policy.errors()
    assert any(".rmvb" in problem for problem in problems)
    assert any("no known muxer" in problem for problem in problems)


def test_unknown_rule_names_are_refused():
    set_rules(languages="never", subtitles="never")
    errors = policy.errors()
    assert len(errors) == 1
    assert "RULE_SUBTITLES" in errors[0]


def test_an_unknown_mode_is_refused():
    """A typo, or a bare "true", would leave the rule doing what it did
    before, which for the ride-alongs is nothing anybody would notice."""
    set_rules(sdh="sometimes")
    errors = policy.errors()
    assert len(errors) == 1
    assert "RULE_SDH" in errors[0]


def test_unknown_regenerate_scope_is_refused():
    set_config(REGENERATE_SCOPE="everything")
    errors = policy.errors()
    assert len(errors) == 1
    assert "REGENERATE_SCOPE" in errors[0]


def test_every_rule_riding_along_is_a_warning():
    """Nothing can order a rewrite, so every ride-along waits on one that never
    comes and the library is silently frozen. A warning rather than an error:
    it is a legitimate way to park an install, just not a legible one."""
    set_rules(**dict.fromkeys(policy.RULES, "alongside"))
    assert any("no rule is set to always" in problem for problem in policy.warnings())
    assert policy.errors() == []


def test_invalid_layouts_catch_typos():
    set_config(AUDIO_LAYOUTS=("2.0", "surround", "5:1", "0.0"))
    errors = policy.errors()
    assert len(errors) == 1
    assert all(bad in errors[0] for bad in ("surround", "5:1", "0.0"))
    # A name that is not a layout is never asked for a rate: it has no
    # variable to set, so saying it lacks one would send nobody anywhere.
    resolved = resolved_layouts(config.current().AUDIO_LAYOUTS)
    assert [(layout.name, layout.channels) for layout in resolved] == [("2.0", 2)]


def test_an_entry_of_no_usable_shape_is_refused():
    """Refused before its fields are read, since a fourth field means nobody
    knows which three were meant. The page writes three at most, so this is a
    hand-edited file or a stray colon."""
    set_config(AUDIO_LAYOUTS=("2.0", "5.1:eac3:448k:extra"))
    errors = policy.errors()
    assert len(errors) == 1
    assert "5.1:eac3:448k:extra" in errors[0]
    assert "use forms like 5.1, 7.1:remove, 5.1:eac3:448k" in errors[0]
    # Left out rather than guessed at, like a name that is not a layout.
    assert [layout.name for layout in resolved_layouts(_layouts())] == ["2.0"]


def test_every_size_offers_the_rate_it_is_shipped_at():
    """The page selects a chip by value, so a stock rate missing from its size's
    notches would land a new row on nothing selected."""
    for name, (_, bitrate) in tracks.STOCK.items():
        assert bitrate in tracks.RATES[name], name


def test_the_shipped_layout_defaults_resolve_as_documented():
    """The two shipped layouts with their defaults. Different encoders: AAC is
    what a browser decodes, AC-3 what a receiver takes."""
    resolved = resolved_layouts(_layouts())
    assert [(layout.name, layout.codec, layout.bitrate) for layout in resolved] == [
        ("2.0", "aac", "320k"),
        ("5.1", "ac3", "640k"),
    ]


def test_resolved_layouts_keep_the_configured_order():
    """Not sorted: the order rule reads this list straight, so sorting it here
    would quietly overrule the setting."""
    set_config(AUDIO_LAYOUTS=("5.1", "2.0"))
    assert [layout.name for layout in resolved_layouts(_layouts())] == ["5.1", "2.0"]


@pytest.mark.parametrize("rate", ["320k", "320000", " 320K "])
def test_one_rate_has_one_spelling(rate):
    """The resolved rate goes into the file as a stream tag a later pass compares
    for equality, so two spellings would read as a settings change and
    regenerate every track we have made."""
    set_layouts(f"2.0:aac:{rate}")
    assert [layout.bitrate for layout in resolved_layouts(_layouts())] == ["320k"]


@pytest.mark.parametrize(
    ("entry", "complaint"),
    [
        ("7.1:aac:", "'7.1:aac:' states no rate"),
        ("7.1::768k", "'7.1::768k' states no encoder"),
    ],
    ids=["no rate", "no encoder"],
)
def test_a_layout_missing_half_of_itself_is_refused_at_startup(entry, complaint):
    """Nothing is derived from anything else, so a layout past the two shipped
    defaults names both. Skipping it would drop the layout just asked for."""
    set_layouts("2.0:aac:320k", entry)
    (error,) = policy.errors()
    assert complaint in error


def test_an_encoder_that_cannot_reach_the_layouts_channels_is_refused():
    """AC-3 tops out at 5.1, and ffmpeg fails outright on the -ac 8 a 7.1 layout
    asks it for. Caught here, or every rewrite of one fails hours later."""
    set_layouts("7.1:ac3:768k")
    errors = policy.errors()
    assert len(errors) == 1
    assert "asks ac3 for 8 channels" in errors[0]
    assert "it encodes at most 6" in errors[0]


def test_an_encoder_the_configured_containers_cannot_hold_is_refused():
    """Opus into MP4 produces a file the players this is all for decline."""
    set_layouts("2.0:libopus:320k")
    set_config(ALLOWED_EXTS={".mkv", ".mp4"})
    errors = policy.errors()
    assert len(errors) == 1
    assert "'2.0:libopus:320k' cannot be written into .mp4" in errors[0]
    assert ".mp4" in errors[0]


def test_remuxing_lets_a_matroska_only_encoder_through():
    """Every rewrite lands as .mkv once the remux rule runs at all, so refusing
    Opus for an MP4 that will not exist on the way out would be wrong."""
    set_layouts("2.0:libopus:320k")
    set_config(ALLOWED_EXTS={".mkv", ".mp4"})
    set_rules(remux="alongside")
    assert policy.errors() == []


def test_an_encoder_outside_the_table_is_taken_as_typed():
    """CODECS is what is known, not what is allowed. An ffmpeg carrying
    something exotic must not be refused for it being unlisted; whether the
    binary really has it is executor.audio_codec_errors' question."""
    set_layouts("2.0:libsomething:320k")
    assert policy.errors() == []
    assert [layout.codec for layout in resolved_layouts(_layouts())] == ["libsomething"]


def test_a_lossless_encoder_warns_that_the_rate_does_nothing():
    """FLAC takes the rate and ignores it, leaving a number on the settings page
    that sizes nothing. Legitimate, so a warning rather than an error."""
    set_layouts("2.0:flac:320k")
    (problem,) = policy.warnings()
    assert "makes 2.0 with flac, which is lossless" in problem


def test_a_layout_rate_that_is_not_a_bitrate_is_refused():
    """Named separately from the layout, so the report says which is wrong."""
    set_layouts("5.1:ac3:loud")
    errors = policy.errors()
    assert len(errors) == 1
    assert "states 'loud', which is not a bitrate" in errors[0]


def test_duplicate_channel_counts_are_refused_at_startup():
    """Two entries for one channel count generate identical tracks and leave the
    rules judging against an arbitrary rate."""
    set_layouts("4.2:ac3:640k", "5.1:ac3:640k")
    errors = policy.errors()
    assert len(errors) == 1
    assert "4.2:ac3:640k, 5.1:ac3:640k are all 6 channels" in errors[0]


# What policy.warnings() says about a rule that reads as on and cannot fire.
# None of these may refuse startup: each is a legitimate half-configured state,
# and each is otherwise invisible, since the rule just never appears in a plan.


def test_an_empty_list_while_the_rule_drops_the_rest_is_a_warning():
    """Only untagged tracks would survive, which is a file nobody asked for."""
    set_langs()
    set_rules(languages="always")
    (problem,) = policy.warnings()
    assert "LANGUAGES" in problem
    assert "untagged" in problem
    assert policy.errors() == []


def test_no_warning_when_the_languages_rule_keeps_the_unlisted():
    """Nothing is dropped for being unnamed, so an empty list is just "keep
    everything"."""
    set_langs()
    set_rules(languages="never")
    assert policy.warnings() == []


def test_remux_with_nothing_to_convert_from_is_a_warning():
    """Matroska in, Matroska out: the switch reads as on and every file is
    already the container it would be converted to."""
    set_rules(remux="always")
    set_config(ALLOWED_EXTS={".mkv"})
    (problem,) = policy.warnings()
    assert "RULE_REMUX" in problem
    assert policy.errors() == []


def test_trimming_away_the_rebuild_sources_is_a_warning():
    """A file trimmed to its downmixes has nothing left to rebuild them from,
    so a later rate change reaches it and nothing happens."""
    set_rules(regenerate="always")
    set_layouts("2.0:aac:192k", "5.1:remove")
    (problem,) = policy.warnings()
    assert "RULE_REGENERATE" in problem
    assert "already trimmed" in problem


def test_no_trimming_warning_once_a_track_can_be_made_from_itself():
    """REGENERATE_ABOVE_PERCENT answers it: the rate change does reach those
    files now, so the warning would be telling an owner to fix what they have."""
    set_rules(regenerate="always")
    set_layouts("2.0:aac:192k", "5.1:remove")
    set_config(REGENERATE_ABOVE_PERCENT=120)
    assert policy.warnings() == []


def test_regenerating_needs_a_container_that_keeps_the_tag():
    """Regeneration finds its own tracks by a custom stream tag, which MP4 does
    not carry, so an MP4-only library regenerates nothing."""
    set_rules(regenerate="always")
    set_config(ALLOWED_EXTS={".mp4", ".m4v"})
    (problem,) = policy.warnings()
    assert "RULE_REGENERATE" in problem
    assert ".mkv" in problem
    assert policy.errors() == []


def test_a_layout_cannot_be_added_and_removed_at_once():
    """One row per size is what makes that unrepresentable. Two rows naming one
    count would make the track every rewrite and take it away the next."""
    set_layouts("2.0", "5.1", "4.2:remove")
    (error,) = policy.errors()
    assert "AUDIO_LAYOUTS entries 4.2:remove, 5.1 are all 6 channels" in error


def test_an_unrecognised_layout_action_is_refused():
    set_layouts("2.0", "7.1:delete")
    (error,) = policy.errors()
    assert (
        "entry '7.1:delete' says 'delete', which is not one of: downmix, keep, remove" in error
    )


def test_a_removed_layout_needs_no_encoder_or_rate():
    """Nothing is made at that size, so demanding a codec for it would refuse
    startup over a field nothing reads."""
    set_layouts("2.0", "7.1:remove")
    assert policy.errors() == []


def test_the_list_changes_name_themselves_in_the_history_without_a_mode():
    """No RULE_ to set, so they are out of RULES; the events breakdown and the
    title sheet still tag them, so they are in RULE_NAMES."""
    for name in ("downmix", "drop_layouts"):
        assert name not in policy.RULES
        assert name in policy.RULE_NAMES


def test_a_rule_variable_for_a_layout_change_is_refused():
    set_config(RULE_MODES={"drop_layouts": "always"})
    (error,) = policy.errors()
    assert "RULE_DROP_LAYOUTS names no rule" in error


def test_removing_a_source_leaves_regeneration_nothing_to_rebuild_from():
    """The two undo each other, and only in the future: the rate change that
    reaches nothing comes months after the sweep that trimmed the file."""
    set_rules(regenerate="always")
    set_layouts("2.0", "5.1", "7.1:remove")
    (problem,) = policy.warnings()
    assert "AUDIO_LAYOUTS removes tracks" in problem
    assert "RULE_REGENERATE" in problem
    assert policy.errors() == []


def test_removing_below_the_added_layouts_costs_regeneration_nothing():
    """A 2.0 is nobody's rebuild source, so the pair is no warning at all."""
    set_rules(regenerate="always")
    set_layouts("2.0:remove", "5.1")
    assert policy.warnings() == []
    assert policy.errors() == []


# LANGUAGES, the same grammar on the other axis. No remove: RULE_LANGUAGES
# already drops everything unlisted.


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("eng", "eng"),  # already 639-2/B
        ("en", "eng"),  # 639-1
        ("English", "eng"),  # an *arr-style name
        ("fra", "fre"),  # 639-2/T
        ("original", "original"),  # the reserved name, not a code
        ("klingon", "klingon"),  # unrecognised: kept verbatim, matches nothing
        ("und", ""),  # a tag meaning no language at all
    ],
)
def test_a_language_entry_normalises_to_639_2b(entry, expected):
    assert tracks.lang_name(entry) == expected


def test_a_bare_language_adds_and_an_action_is_read_beside_it():
    set_langs("original", "eng", "fre:keep")
    assert resolved_langs(config.current().LANGUAGES) == [
        Lang("original", "downmix"),
        Lang("eng", "downmix"),
        Lang("fre", "keep"),
    ]
    assert policy.errors() == []


def test_a_language_cannot_be_removed():
    """The list is what survives, so a row saying otherwise would be a second
    way to spell what RULE_LANGUAGES already does."""
    set_langs("eng", "hin:remove")
    (error,) = policy.errors()
    assert "entry 'hin:remove' says 'remove', which is not one of: downmix, keep" in error
    assert "RULE_LANGUAGES drops everything the list does not name" in error


def test_the_shipped_language_defaults_resolve_as_documented():
    """The default guarantees every layout in the title's own language and in
    English, and drops nothing else by itself."""
    resolved = resolved_langs(config.current().LANGUAGES)
    assert resolved == [Lang("original", "downmix"), Lang("eng", "downmix")]


@pytest.mark.parametrize(
    ("entries", "complaint"),
    [
        (("eng", "und"), "LANGUAGES entry 'und' names no language"),
        (("eng", "fre:bin"), "entry 'fre:bin' says 'bin', which is not one of: downmix, keep"),
        (
            ("eng:keep:320k",),
            "entry 'eng:keep:320k' is not a language or a language and an action",
        ),
    ],
    ids=["names no language", "unrecognised action", "no usable shape"],
)
def test_a_malformed_language_entry_is_refused(entries, complaint):
    set_langs(*entries)
    (error,) = policy.errors()
    assert complaint in error


def test_a_language_cannot_be_named_twice():
    """Two spellings of one code are two opinions about one set of tracks."""
    set_langs("eng", "English:keep")
    (error,) = policy.errors()
    assert "LANGUAGES entries English:keep, eng are all eng" in error


def test_an_explicit_row_beats_the_original_one():
    """original is the fallback for a language nothing names, so it never
    doubles a row that does."""
    set_langs("original:keep", "eng")
    resolved = Policy.from_config().resolve("eng")
    assert resolved == (Lang("eng", "downmix"),)


def test_the_original_row_resolves_to_the_titles_language():
    set_langs("original", "eng:keep")
    assert Policy.from_config().resolve("kor") == (Lang("kor", "downmix"), Lang("eng", "keep"))


def test_an_unknown_original_language_drops_its_row():
    """An *arr outage leaves nothing to substitute, so the row cannot act."""
    set_langs("original", "eng:keep")
    assert Policy.from_config().resolve(None) == (Lang("eng", "keep"),)


def test_only_a_list_naming_original_waits_for_the_arrs():
    set_langs("original", "eng")
    assert Policy.from_config().needs_original_lang() is True
    set_langs("eng")
    assert Policy.from_config().needs_original_lang() is False


def test_a_list_that_only_orders_is_a_warning():
    """Legitimate for a library that only reorders, and also what a list edited
    a row at a time ends up as."""
    set_layouts("2.0:keep", "5.1:keep")
    (problem,) = policy.warnings()
    assert "adds and removes no layout" in problem
    assert policy.errors() == []


def test_the_shipped_defaults_warn_about_nothing():
    """Every warning above is a half-configured state, so a fresh install must
    be silent or they are noise nobody reads."""
    assert policy.warnings() == []


def test_tagging_without_original_in_the_language_list_is_a_warning():
    """The rule refuses a code the languages rule would then drop, so a list
    naming neither leaves it with nothing to do."""
    set_rules(tag_original="alongside")
    set_langs("eng")
    (problem,) = policy.warnings()
    assert "LANGUAGES does not name original" in problem
