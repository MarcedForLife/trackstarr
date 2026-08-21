"""Runtime configuration, from the environment and STATE_DIR/settings.json.

Both sources hold the same names. The file exists so a future settings API
can write configuration; the environment wins when both name a setting, so a
compose file stays the deploy's word. Values are read once at import. Other
modules say ``config.NAME`` rather than importing the names, so a test can
monkeypatch one attribute.

Nothing here raises. Import has to succeed so the CLI can report every
problem at once through :func:`errors`.
"""

import json
import os
import re

from . import cron
from .langs import norm_lang

#: Parse failures, reported by errors(). The bad setting keeps its default.
_LOAD_ERRORS: list[str] = []

SETTINGS_FILE = "settings.json"

#: A .env in the working directory, loaded before the reads below so a source
#: checkout can set WORK_DIR and the rest the way a compose file does. Dev
#: convenience only: the image ships no .env and sets everything through the
#: environment, so a missing file is the normal case.
ENV_FILE = ".env"


def _load_dotenv(path: str = ENV_FILE) -> None:
    """Copy NAME=VALUE lines from a .env file into the environment.

    A blank line or one starting with # is skipped; any other line without an
    = is a typo that reaches errors() rather than passing unseen. Surrounding
    quotes on a value are dropped. An existing variable is never overwritten,
    so a hand-exported value, or a test's monkeypatch, stays the last word and
    the environment keeps being the deploy's.
    """
    try:
        with open(path) as env_file:
            lines = env_file.read().splitlines()
    except FileNotFoundError:
        return
    except OSError as err:
        _LOAD_ERRORS.append(f"{ENV_FILE} could not be read ({err})")
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep:
            _LOAD_ERRORS.append(f"{ENV_FILE} line is not NAME=VALUE: {raw_line!r}")
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


_load_dotenv()

#: Environment only: it says where the settings file lives.
STATE_DIR = os.environ.get("STATE_DIR", "/config")


def _settings_path() -> str:
    return os.path.join(STATE_DIR, SETTINGS_FILE)


def _load_settings() -> dict[str, str]:
    """STATE_DIR/settings.json as name -> raw string, {} when there is none.

    Each value is the string the same-named variable would hold, so the
    parsers treat both sources alike; JSON numbers and booleans read as their
    literals ("true", "5120"). Anything else is refused per key: a dropped
    setting must reach errors(), never quietly mean its default.
    """
    try:
        with open(_settings_path()) as settings_file:
            data = json.load(settings_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as err:
        _LOAD_ERRORS.append(f"{SETTINGS_FILE} could not be read ({err})")
        return {}
    if not isinstance(data, dict):
        _LOAD_ERRORS.append(f"{SETTINGS_FILE} must hold one JSON object of settings")
        return {}
    values: dict[str, str] = {}
    for name, value in data.items():
        if isinstance(value, str):
            values[name] = value
        elif isinstance(value, bool | int | float):
            # json.dumps spells these the way the parsers read them: true, 5120.
            values[name] = json.dumps(value)
        else:
            _LOAD_ERRORS.append(
                f"{SETTINGS_FILE} value for {name} must be a string, number or boolean"
            )
    return values


_SETTINGS = _load_settings()

#: Every name a parser asked for, filled as this module loads, so warnings()
#: can report settings-file keys nothing reads. The environment gets no such
#: check; it is full of other programs' variables.
_READ_NAMES: set[str] = set()


def _raw(name: str, default: str) -> str:
    """One setting's raw value: the environment, then the settings file, then
    the default. A blank environment value is a leftover (``DRY_RUN:
    ${DRY_RUN}`` expands to empty), not a choice, so a real settings-file
    entry beats it; with no entry it keeps meaning what it does today."""
    _READ_NAMES.add(name)
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value
    if (from_file := _SETTINGS.get(name)) is not None:
        return from_file
    return default if value is None else value


def _list(name: str, default: str) -> list[str]:
    return [part for part in _raw(name, default).split(":") if part]


def _set(name: str, default: str) -> set[str]:
    parts = _raw(name, default).split(",")
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
    raw = _raw(name, default).strip()
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
    value = _raw(name, "").strip().lower()
    return "" if value in _OFF else value


def _int(name: str, default: str) -> int:
    raw = _raw(name, default).strip()
    try:
        return int(raw)
    except ValueError:
        _LOAD_ERRORS.append(f"{name}={raw!r} is not a whole number")
        return int(default)


def _regex(name: str, default: str) -> re.Pattern[str]:
    raw = _raw(name, default)
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
    ${RADARR_API_KEY}`` expands to empty. The settings file may hold the
    plain name too; any environment form beats it, and the indirect forms
    mean nothing there, a file needs no pointer to a file.
    """
    # Registered here too: when an indirect form wins, _raw is never
    # consulted and the file's plain entry would read as a typo.
    _READ_NAMES.add(name)
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
        # No environment form is set, which is exactly the case _raw's
        # precedence covers: the file entry, or blank.
        return _raw(name, "").strip()
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


def _path_map(name: str) -> list[tuple[str, str]]:
    """``LOCAL=REMOTE`` prefix pairs, longest local first.

    A media server indexes the library through its own mount, which need not
    be ours: Plex reporting ``/mnt/content/media`` while the container walks
    ``/data/media`` matches nothing, and every refresh is silently skipped.
    One pair per library where they differ, comma-separated. ``=`` rather than
    ``:`` separates the sides, since MEDIA_DIRS already spends the colon and a
    server on Windows spells its half ``D:\\Media``.
    """
    pairs: list[tuple[str, str]] = []
    for entry in _raw(name, "").split(","):
        if not entry.strip():
            continue
        local, sep, remote = entry.partition("=")
        # Trailing slashes off both sides: the prefix test joins its own, and
        # a stray one would make /data/media/ match nothing.
        local, remote = local.strip().rstrip("/"), remote.strip().rstrip("/")
        if not sep or not local or not remote:
            _LOAD_ERRORS.append(f"{name} entry {entry.strip()!r} is not LOCAL=REMOTE")
            continue
        pairs.append((local, remote))
    # Longest first, so the first prefix match is the most specific, the way
    # media_server sorts the sections it maps onto.
    pairs.sort(key=lambda pair: -len(pair[0]))
    return pairs


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
WORK_DIR = _raw("WORK_DIR", "/data/trackstarr-work")

#: Every credential also takes RADARR_API_KEY_FILE or FILE__RADARR_API_KEY
#: naming a file to read it from; see _secret.
RADARR_URL = _raw("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = _secret("RADARR_API_KEY")
SONARR_URL = _raw("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = _secret("SONARR_API_KEY")

#: Media servers to nudge after a rewrite, since their own watchers see
#: nothing on a network mount. Jellyfin's settings fit Emby too.
PLEX_URL = _raw("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = _secret("PLEX_TOKEN")
JELLYFIN_URL = _raw("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = _secret("JELLYFIN_API_KEY")

#: How each server spells the library, when it isn't how we do. Only the
#: parent path ever differs, so one ``LOCAL=REMOTE`` pair per library is
#: enough; unset means the mounts already match. See _path_map.
PLEX_PATH_MAP = _path_map("PLEX_PATH_MAP")
JELLYFIN_PATH_MAP = _path_map("JELLYFIN_PATH_MAP")

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

AUDIO_CODEC = _raw("AUDIO_CODEC", "aac")

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

    Scans both sources instead of asking once per configured layout, so a
    rate set for a layout nothing asks for reaches warnings() as the
    half-finished edit it usually is. The environment scans second, so its
    rate wins for a layout both name.
    """
    rates = dict(_DEFAULT_BITRATES)
    for source in (_SETTINGS, os.environ):
        for variable, value in source.items():
            if variable.startswith(_BITRATE_PREFIX) and (rate := value.strip()):
                rates[variable.removeprefix(_BITRATE_PREFIX).replace("_", ".").lower()] = rate
    return rates


def _stated(variable: str) -> bool:
    """Whether either source names the variable, for the half-edit check."""
    return variable in os.environ or variable in _SETTINGS


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

LISTEN_ADDR = _raw("LISTEN_ADDR", "0.0.0.0")
#: 5120 spells 5.1 and 2.0, the layouts the downmix rule guarantees. Mostly
#: it is just free: 8080 is qBittorrent's and SABnzbd's.
LISTEN_PORT = _int("LISTEN_PORT", "5120")

#: Advertised when registering the webhook, so it has to be reachable from
#: the *arrs' containers. The default is the README's compose service name.
#: Base URL only; registration appends the webhook path itself.
WEBHOOK_URL = _raw("WEBHOOK_URL", f"http://trackstarr:{LISTEN_PORT}").rstrip("/")

FFMPEG_TIMEOUT = _int("FFMPEG_TIMEOUT", "7200")
PROBE_TIMEOUT = _int("PROBE_TIMEOUT", "180")

#: Rewrites allowed at once across the webhook workers, the sweep, and any
#: other process sharing STATE_DIR. 1 by default because spinning disks
#: thrash when rewrites run in parallel; on anything faster that is usually
#: wrong, and the README says how to measure it.
MAX_CONCURRENT_REWRITES = _int("MAX_CONCURRENT_REWRITES", "1")

#: When the sweep runs, as a five-field cron schedule in local time; empty
#: disables it. It reports by default and rewrites only under SWEEP_APPLY.
SWEEP_AT = _raw("SWEEP_AT", "").strip()
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
    # Unlike the environment's, the file's names are a closed set, so an
    # unread key is a typo silently meaning its default. Refused like a
    # DISABLED_RULES typo, and here rather than warnings() so plan and fix,
    # which read the same file, report it too.
    if ignored := sorted(
        name
        for name in _SETTINGS
        if name not in _READ_NAMES and not name.startswith(_BITRATE_PREFIX)
    ):
        problems.append(f"{SETTINGS_FILE} names settings nothing reads: {', '.join(ignored)}")
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
        if name not in DOWNMIX_LAYOUTS and _stated(bitrate_variable(name))
    ]
    return problems
