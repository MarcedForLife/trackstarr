"""ISO 639 normalisation.

Track tags are ISO 639-2/B, which is what Matroska and ffmpeg write. Radarr
and Sonarr report a title's original language as an English name, and rips in
the wild also carry 639-1 and 639-2/T codes. Everything is normalised to
639-2/B before any comparison.
"""

from __future__ import annotations

#: Every language Radarr and Sonarr can report, mapped to ISO 639-2/B.
#: Sourced from their ``/api/v3/language`` endpoint.
LANG_NAMES: dict[str, str] = {
    "afrikaans": "afr",
    "albanian": "alb",
    "arabic": "ara",
    "bengali": "ben",
    "bosnian": "bos",
    "bulgarian": "bul",
    "catalan": "cat",
    "chinese": "chi",
    "croatian": "hrv",
    "czech": "cze",
    "danish": "dan",
    "dutch": "dut",
    "english": "eng",
    "estonian": "est",
    "finnish": "fin",
    "flemish": "dut",
    "french": "fre",
    "georgian": "geo",
    "german": "ger",
    "greek": "gre",
    "hebrew": "heb",
    "hindi": "hin",
    "hungarian": "hun",
    "icelandic": "ice",
    "indonesian": "ind",
    "italian": "ita",
    "japanese": "jpn",
    "kannada": "kan",
    "korean": "kor",
    "latvian": "lav",
    "lithuanian": "lit",
    "macedonian": "mac",
    "malayalam": "mal",
    "marathi": "mar",
    "mongolian": "mon",
    "norwegian": "nor",
    "persian": "per",
    "polish": "pol",
    "portuguese": "por",
    "portuguese (brazil)": "por",
    "romanian": "rum",
    "romansh": "roh",
    "russian": "rus",
    "serbian": "srp",
    "slovak": "slo",
    "slovenian": "slv",
    "spanish": "spa",
    "spanish (latino)": "spa",
    "swedish": "swe",
    "tagalog": "tgl",
    "tamil": "tam",
    "telugu": "tel",
    "thai": "tha",
    "turkish": "tur",
    "ukrainian": "ukr",
    "urdu": "urd",
    "vietnamese": "vie",
}

LANG_ALIASES: dict[str, str] = {
    # ISO 639-1
    "af": "afr",
    "sq": "alb",
    "ar": "ara",
    "bn": "ben",
    "bs": "bos",
    "bg": "bul",
    "ca": "cat",
    "zh": "chi",
    "hr": "hrv",
    "cs": "cze",
    "da": "dan",
    "nl": "dut",
    "en": "eng",
    "et": "est",
    "fi": "fin",
    "fr": "fre",
    "ka": "geo",
    "de": "ger",
    "el": "gre",
    "he": "heb",
    "hi": "hin",
    "hu": "hun",
    "is": "ice",
    "id": "ind",
    "it": "ita",
    "ja": "jpn",
    "kn": "kan",
    "ko": "kor",
    "lv": "lav",
    "lt": "lit",
    "mk": "mac",
    "ml": "mal",
    "mr": "mar",
    "mn": "mon",
    "no": "nor",
    "nb": "nor",
    "nn": "nor",
    "fa": "per",
    "pl": "pol",
    "pt": "por",
    "ro": "rum",
    "rm": "roh",
    "ru": "rus",
    "sr": "srp",
    "sk": "slo",
    "sl": "slv",
    "es": "spa",
    "sv": "swe",
    "tl": "tgl",
    "ta": "tam",
    "te": "tel",
    "th": "tha",
    "tr": "tur",
    "uk": "ukr",
    "ur": "urd",
    "vi": "vie",
    # 639-1 for languages the *arrs never report but track tags still carry
    "bo": "tib",
    "cy": "wel",
    "eu": "baq",
    "ga": "gle",
    "gd": "gla",
    "hy": "arm",
    "mi": "mao",
    "ms": "may",
    "my": "bur",
    # ISO 639-2/T, where it differs from 639-2/B
    "sqi": "alb",
    "zho": "chi",
    "ces": "cze",
    "nld": "dut",
    "fra": "fre",
    "kat": "geo",
    "deu": "ger",
    "ell": "gre",
    "isl": "ice",
    "mkd": "mac",
    "fas": "per",
    "ron": "rum",
    "slk": "slo",
    "hye": "arm",
    "eus": "baq",
    "bod": "tib",
    "cym": "wel",
    "msa": "may",
    "mya": "bur",
    "mri": "mao",
    # Non-standard codes that turn up in scene releases
    "nob": "nor",
    "nno": "nor",
    "pob": "por",
    "ptb": "por",
    "chs": "chi",
    "cht": "chi",
    "zht": "chi",
    "zhs": "chi",
}

#: The three tables above, merged for lookup. 639-2/B codes map to themselves.
#: The source tables stay separate because they document different things.
_LOOKUP: dict[str, str] = (
    LANG_NAMES | LANG_ALIASES | {code: code for code in LANG_NAMES.values()}
)

#: Tags that positively mean "no language", as distinct from a missing tag.
#: Both end up as None; an untagged track is always kept.
_UNDEFINED = {"", "und", "unknown", "zxx", "mis", "mul", "none", "null"}

#: Names the *arrs report that deliberately aren't a language. Shared with
#: arr.original_of, which warns about unmapped names but must not warn on
#: these.
ARR_NON_LANGUAGES = frozenset({"unknown", "original", "any"})


def norm_lang(tag: str | None) -> str | None:
    """Normalise a language tag to ISO 639-2/B.

    Returns None for anything that means "undefined". An unrecognised code is
    returned unchanged so it fails the keep test and shows up in logs by name
    rather than being silently treated as undefined and kept.
    """
    if not tag:
        return None
    code = tag.strip().lower().replace("_", "-")
    code = code.split("-")[0]  # en-US -> en, pt-BR -> pt
    if code in _UNDEFINED:
        return None
    return _LOOKUP.get(code, code)


def from_name(name: str | None) -> str | None:
    """Map an *arr language name (``"Korean"``) to ISO 639-2/B."""
    if not name:
        return None
    key = name.strip().lower()
    if not key or key in ARR_NON_LANGUAGES:
        return None
    return LANG_NAMES.get(key)
