"""Runtime configuration, all of it from the environment.

Values are read once at import. Other modules say ``config.NAME`` rather than
importing the names, so a test can monkeypatch one attribute.

Nothing here raises. Import has to succeed so the CLI can report every
problem at once through :func:`errors`.
"""

import os
import re

from . import cron
from .langs import norm_lang

#: Parse failures, reported by errors(). The bad setting keeps its default.
_LOAD_ERRORS: list[str] = []


def _list(name: str, default: str) -> list[str]:
    return [part for part in os.environ.get(name, default).split(":") if part]


def _set(name: str, default: str) -> set[str]:
    parts = os.environ.get(name, default).split(",")
    return {part.strip().lower() for part in parts if part.strip()}


def _rules(name: str, default: str) -> set[str]:
    """Rule names, with dashes read as underscores, so cover_art can be
    spelled either way. A real typo is still refused against policy.RULES."""
    return {rule.replace("-", "_") for rule in _set(name, default)}


#: Closed sets, so a typo is refused at startup rather than read as false,
#: which for DRY_RUN would rewrite a library its owner thought was safe.
#: Empty reads as unset; a leftover ``DRY_RUN: ${DRY_RUN}`` expands to empty.
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _bool(name: str, default: str = "false") -> bool:
    raw = os.environ.get(name, default).strip()
    value = raw.lower() or default
    if value not in _TRUE and value not in _FALSE:
        _LOAD_ERRORS.append(f"{name}={raw!r} is neither true nor false")
        value = default
    return value in _TRUE


#: Spellings of "off" a named-mode setting may use instead of being unset.
_OFF = frozenset({"", "off", "none", "false", "no", "0"})


def _mode(name: str) -> str:
    """A named-mode setting, with every spelling of off reduced to unset.
    The value itself is checked by :func:`trackstarr.policy.errors`."""
    value = os.environ.get(name, "").strip().lower()
    return "" if value in _OFF else value


def _int(name: str, default: str) -> int:
    raw = os.environ.get(name, default).strip()
    try:
        return int(raw)
    except ValueError:
        _LOAD_ERRORS.append(f"{name}={raw!r} is not a whole number")
        return int(default)


def _regex(name: str, default: str) -> re.Pattern[str]:
    raw = os.environ.get(name, default)
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error as err:
        _LOAD_ERRORS.append(f"{name}={raw!r} is not a valid regex ({err})")
        return re.compile(default, re.IGNORECASE)


def _secret(name: str) -> str:
    """A credential, from the environment or a file a companion variable
    names: ``NAME_FILE`` (the official images' convention) or ``FILE__NAME``
    (linuxserver.io's). A file keeps the key out of ``docker inspect``.

    Naming one credential two ways is refused; which won would be invisible.
    Whitespace-only counts as unset; a leftover ``RADARR_API_KEY:
    ${RADARR_API_KEY}`` expands to empty.
    """
    named = [
        (variable, value)
        for variable in (f"{name}_FILE", f"FILE__{name}", name)
        if (value := os.environ.get(variable, "").strip())
    ]
    if len(named) > 1:
        _LOAD_ERRORS.append(
            f"{name} is set more than one way ({', '.join(var for var, _ in named)}); keep one"
        )
        return ""
    if not named:
        return ""
    variable, value = named[0]
    if variable == name:
        return value
    try:
        with open(value) as secret_file:
            content = secret_file.read().strip()
    except OSError as err:
        _LOAD_ERRORS.append(f"{variable}={value!r} could not be read ({err})")
        return ""
    if not content:
        # A blank key reads the same as one never set, so the service would
        # just be quietly disabled.
        _LOAD_ERRORS.append(f"{variable}={value!r} is empty")
    return content


def _langs(name: str, default: str) -> set[str]:
    """Languages normalised to ISO 639-2/B like every track tag, so "en",
    "English" and "eng" all mean the same thing. Anything unrecognised passes
    through unchanged: it matches no track and shows up by name at startup."""
    return {code for entry in _set(name, default) if (code := norm_lang(entry))}


#: Where the sweep walks. Webhook and fix paths are taken as given.
MEDIA_DIRS = _list("MEDIA_DIRS", "/data/media/movies:/data/media/tv")

#: Where a rewrite is staged before it replaces the original. Any filesystem
#: works; executor._publish is atomic either way. Wants room for the biggest
#: file in the library, and speed.
WORK_DIR = os.environ.get("WORK_DIR", "/data/trackstarr-work")

STATE_DIR = os.environ.get("STATE_DIR", "/config")

#: Every credential also takes RADARR_API_KEY_FILE or FILE__RADARR_API_KEY
#: naming a file to read it from; see _secret.
RADARR_URL = os.environ.get("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = _secret("RADARR_API_KEY")
SONARR_URL = os.environ.get("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = _secret("SONARR_API_KEY")

#: Media servers to nudge after a rewrite, since their own watchers see
#: nothing on a network mount. Jellyfin's settings fit Emby too.
PLEX_URL = os.environ.get("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = _secret("PLEX_TOKEN")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = _secret("JELLYFIN_API_KEY")

#: Languages kept regardless of the title's original language.
ALWAYS_KEEP_LANGS = _langs("ALWAYS_KEEP_LANGS", "eng")

#: Rules to switch off, for setups where something else owns part of the job.
#: Valid names are the keys of policy.RULES; startup refuses others.
DISABLED_RULES = _rules("DISABLED_RULES", "")

#: Drop commentary, described-audio and isolated-score tracks outright rather
#: than only keeping them out of the downmix rule. Off by default: protecting
#: commentary is why this tool exists.
DROP_COMMENTARY = _bool("DROP_COMMENTARY")

#: Layouts the downmix rule guarantees. A missing one is made from the best
#: surviving bigger track; with nothing bigger it is skipped, never upmixed.
#: Startup refuses a name that isn't a digit form, or one with no rate.
DOWNMIX_LAYOUTS = _set("DOWNMIX_LAYOUTS", "2.0,5.1")

AUDIO_CODEC = os.environ.get("AUDIO_CODEC", "aac")

#: Rates for the two layouts we ship, so a fresh install names none. 320k is
#: past transparency for ffmpeg's native AAC; 640k is what AC3 5.1 ships at.
_DEFAULT_BITRATES = {"2.0": "320k", "5.1": "640k"}

#: AUDIO_BITRATE_5_1 sets 5.1's rate. Dots become underscores because an
#: environment variable name cannot hold a dot.
_BITRATE_PREFIX = "AUDIO_BITRATE_"


def bitrate_variable(name: str) -> str:
    """Which variable states a layout's rate, for the startup report."""
    return _BITRATE_PREFIX + name.replace(".", "_")


def _bitrates() -> dict[str, str]:
    """Layout name -> the rate it is generated at.

    Scans the environment instead of asking once per configured layout, so a
    rate set for a layout nothing asks for reaches warnings() as the
    half-finished edit it usually is.
    """
    rates = dict(_DEFAULT_BITRATES)
    for variable, value in os.environ.items():
        if variable.startswith(_BITRATE_PREFIX) and (rate := value.strip()):
            rates[variable.removeprefix(_BITRATE_PREFIX).replace("_", ".").lower()] = rate
    return rates


#: One AUDIO_BITRATE_ variable per layout, nothing derived; layouts.py says why.
AUDIO_BITRATES = _bitrates()

#: Rewrite MP4 and M4V into Matroska, the one container carrying the tags
#: regeneration needs. Off by default: MP4 direct-plays on more devices.
REMUX_TO_MKV = _bool("REMUX_TO_MKV")

#: What the downmix rule may rebuild beyond creating missing layouts.
#: "generated" rebuilds our own tracks whose settings no longer match; "all"
#: also replaces a real track reported well below its layout's rate. Unset
#: does neither, since both queue rewrites across the library.
REGENERATE_DOWNMIXES = _mode("REGENERATE_DOWNMIXES")

#: Containers we will rewrite. AVI and MPG are left out on purpose: a stream
#: copy into one with a fresh AAC track usually produces an unplayable file.
ALLOWED_EXTS = _set("ALLOWED_EXTS", ".mkv,.mp4,.m4v")

#: Leave files the download client still hard-links alone. Rewriting one
#: breaks the link and the file costs disk twice until the torrent goes.
#: Nothing is skipped for good; see HARDLINK_RECHECK.
SKIP_HARDLINKS = _bool("SKIP_HARDLINKS", "true")

#: Seconds between re-stats of files parked by SKIP_HARDLINKS, so they are
#: processed minutes after seeding ends rather than at the next sweep. 0
#: parks nothing and leaves them to the sweep.
HARDLINK_RECHECK = _int("HARDLINK_RECHECK", "900")

#: Matched against the track title when the muxer left the disposition flags
#: unset, which most rips do.
COMMENTARY_RE = _regex(
    "COMMENTARY_PATTERN",
    r"comment|descriptive|described\s*video|audio\s*description|"
    r"isolated\s*score|director'?s?\s*track|interview|behind\s*the\s*scenes",
)

#: Spots subtitles for the deaf and hard-of-hearing by title, for the muxers
#: that leave the hearing_impaired disposition unset.
SDH_RE = _regex("SDH_PATTERN", r"\bsdh\b|\bcc\b|hearing[\s._-]*impaired")

#: Likewise for forced subtitles and the forced disposition.
FORCED_RE = _regex("FORCED_PATTERN", r"\bforced\b")

#: Release junk rather than information: bitrates, resolutions, source tags,
#: codec names. Cleared during a rewrite that was happening anyway. Kept
#: conservative, a bare "AC3 5.1" survives; extend it to catch those.
JUNK_TITLE_RE = _regex(
    "JUNK_TITLE_PATTERN",
    r"\d+\s*k?bps"
    r"|\bx?26[45]\b|\bhevc\b|\bavc\b"
    r"|\b(?:480|576|720|1080|2160)[pi]\b"
    r"|\b(?:blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|remux)\b",
)

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "0.0.0.0")
#: 5120 spells 5.1 and 2.0, the layouts the downmix rule guarantees. Mostly
#: it is just free: 8080 is qBittorrent's and SABnzbd's.
LISTEN_PORT = _int("LISTEN_PORT", "5120")

#: Advertised when registering the webhook, so it has to be reachable from
#: the *arrs' containers. The default is the README's compose service name.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", f"http://trackstarr:{LISTEN_PORT}")

FFMPEG_TIMEOUT = _int("FFMPEG_TIMEOUT", "7200")
PROBE_TIMEOUT = _int("PROBE_TIMEOUT", "180")

#: Rewrites allowed at once across the webhook workers, the sweep, and any
#: other process sharing STATE_DIR. 1 by default because spinning disks
#: thrash when rewrites run in parallel; on anything faster that is usually
#: wrong, and the README says how to measure it.
MAX_CONCURRENT_REWRITES = _int("MAX_CONCURRENT_REWRITES", "1")

#: When the sweep runs, as a five-field cron schedule in local time; empty
#: disables it. It reports by default and rewrites only under SWEEP_APPLY.
SWEEP_AT = os.environ.get("SWEEP_AT", "").strip()
SWEEP_APPLY = _bool("SWEEP_APPLY")

#: Plan and report everywhere, rewrite nothing, overriding SWEEP_APPLY and
#: ``sweep --apply``, so a new install can watch a real week first.
DRY_RUN = _bool("DRY_RUN")


def errors() -> list[str]:
    """Startup-fatal configuration problems, as ready-to-log messages.

    Parse failures from import, plus the operational checks. The ones needing
    the rule and layout vocabulary live in :func:`trackstarr.policy.errors`.
    """
    problems = list(_LOAD_ERRORS)
    # A budget below one hangs the slot pool; a timeout below one fails
    # every ffmpeg and ffprobe run as "timed out".
    for name, value in (
        ("MAX_CONCURRENT_REWRITES", MAX_CONCURRENT_REWRITES),
        ("FFMPEG_TIMEOUT", FFMPEG_TIMEOUT),
        ("PROBE_TIMEOUT", PROBE_TIMEOUT),
    ):
        if value < 1:
            problems.append(f"{name}={value} must be at least 1")
    if SWEEP_AT:
        try:
            # Left to the scheduler, a bad schedule kills only its thread:
            # serve keeps running and sweeps never happen.
            cron.parse(SWEEP_AT)
        except ValueError as err:
            problems.append(f"SWEEP_AT={SWEEP_AT!r}: {err}")
    return problems


def warnings() -> list[str]:
    """Configuration that is probably a mistake but must not refuse startup.

    A missing MEDIA_DIRS entry is legitimate (a library mounted late, a
    webhook-only install) but otherwise silent until a sweep walks it. A rate
    for a layout nothing asks for is usually half an edit, and just as silent
    because nothing reads it.
    """
    problems = [
        f"MEDIA_DIRS entry {media_dir} does not exist; the sweep will find nothing there"
        for media_dir in MEDIA_DIRS
        if not os.path.isdir(media_dir)
    ]
    problems += [
        f"{bitrate_variable(name)} is set but DOWNMIX_LAYOUTS does not ask for {name}, "
        "so nothing reads it"
        for name in sorted(AUDIO_BITRATES)
        # The variable, not just the entry: 2.0 and 5.1 are always present as
        # defaults, and dropping one from DOWNMIX_LAYOUTS is normal.
        if name not in DOWNMIX_LAYOUTS and bitrate_variable(name) in os.environ
    ]
    return problems
