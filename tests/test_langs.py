import pytest

from trackstarr.langs import LANG_NAMES, from_name, norm_lang


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # ISO 639-1 to 639-2/B
        ("en", "eng"),
        ("de", "ger"),
        ("ko", "kor"),
        ("pt", "por"),
        # already 639-2/B
        ("eng", "eng"),
        ("ger", "ger"),
        ("chi", "chi"),
        # 639-2/T to 639-2/B
        ("deu", "ger"),
        ("nld", "dut"),
        ("zho", "chi"),
        ("fra", "fre"),
        ("ces", "cze"),
        ("ron", "rum"),
        ("slk", "slo"),
        # locale suffixes
        ("en-US", "eng"),
        ("pt-BR", "por"),
        ("en_GB", "eng"),
        ("ZH-hans", "chi"),
        # scene oddities
        ("nob", "nor"),
        ("nno", "nor"),
        ("ptb", "por"),
        ("chs", "chi"),
        # 639-1 for languages outside the *arr table
        ("cy", "wel"),
        ("hy", "arm"),
        ("ga", "gle"),
        ("gd", "gla"),
        # Irish is not Scottish Gaelic
        ("gle", "gle"),
        ("gla", "gla"),
        # full names
        ("English", "eng"),
        ("korean", "kor"),
        # undefined
        ("und", None),
        ("", None),
        (None, None),
        ("zxx", None),
        ("mul", None),
        ("unknown", None),
        ("  ", None),
    ],
)
def test_norm_lang(tag, expected):
    assert norm_lang(tag) == expected


def test_unknown_code_passes_through():
    """An unrecognised code must not be mistaken for 'undefined'.

    Undefined tracks are kept; an unknown foreign code should be dropped, so
    it has to stay distinguishable.
    """
    assert norm_lang("xyz") == "xyz"
    assert norm_lang("xyz") is not None


def test_norm_lang_is_idempotent():
    for code in set(LANG_NAMES.values()):
        assert norm_lang(code) == code


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("English", "eng"),
        ("Korean", "kor"),
        ("Portuguese (Brazil)", "por"),
        ("Spanish (Latino)", "spa"),
        ("Flemish", "dut"),
        ("Chinese", "chi"),
        ("Norwegian", "nor"),
        # the *arrs' non-languages
        ("Unknown", None),
        ("Original", None),
        ("Any", None),
        (None, None),
        # not a language at all
        ("Klingon", None),
    ],
)
def test_from_name(name, expected):
    assert from_name(name) == expected


def test_every_arr_language_maps_to_a_three_letter_code():
    """Guards against a typo in the table silently dropping a language."""
    for name, code in LANG_NAMES.items():
        assert len(code) == 3, f"{name} -> {code!r}"
        assert code.isalpha() and code.islower(), f"{name} -> {code!r}"
