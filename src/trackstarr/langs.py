"""ISO 639 normalisation. Track tags are 639-2/B, which Matroska and ffmpeg
write; the *arrs report English names, and rips carry 639-1 and 639-2/T too.
Everything is normalised to 639-2/B before comparison."""

import re

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

#: One display name per code, in name order, for the language picker. Built
#: from the reversed table so the earlier of two names sharing a code wins:
#: "Dutch" over "Flemish". Deduplicated before sorting for the same reason.
LANG_LABELS: dict[str, str] = dict(
    sorted(
        {code: name.title() for name, code in reversed(LANG_NAMES.items())}.items(),
        key=lambda entry: entry[1],
    )
)


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

#: The tables above merged for lookup, with 639-2/B codes mapping to themselves.
_LOOKUP: dict[str, str] = (
    LANG_NAMES | LANG_ALIASES | {code: code for code in LANG_NAMES.values()}
)

#: Tags that mean "no language". These and a missing tag become None, and an
#: untagged track is always kept.
_UNDEFINED = {"", "und", "unknown", "zxx", "mis", "mul", "none", "null"}

#: Names the *arrs report that are not languages. arr.original_of warns about
#: unmapped names but not these.
ARR_NON_LANGUAGES = frozenset({"unknown", "original", "any"})


def norm_lang(tag: str | None) -> str | None:
    """Normalise a language tag to ISO 639-2/B, or None for "undefined". An
    unrecognised code comes back unchanged, so it fails the keep test and is
    logged by name."""
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


def named_in(title: str | None) -> str | None:
    """The language a track title names ("English 5.1"), or None.

    Full names, plus a title that is only a code. Two-letter codes are left
    out, since "is", "it" and "no" are words before they are languages.
    """
    if not title:
        return None
    whole = title.strip().lower()
    if len(whole) == 3 and whole in _LOOKUP:
        return _LOOKUP[whole]
    for word in re.findall(r"[a-z]+", whole):
        if code := LANG_NAMES.get(word):
            return code
    return None
