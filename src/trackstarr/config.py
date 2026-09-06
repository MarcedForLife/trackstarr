"""Runtime configuration, from the environment and STATE_DIR/settings.json.

Both sources hold the same names; the environment wins, so a compose file
stays the deploy's word. Values are read once at import. Other modules say
``config.NAME`` rather than importing the names, so a test can monkeypatch one.

Nothing here raises. Import must succeed so the CLI can report every problem
at once through :func:`errors`.
"""

import json
import os
import re
import time
import zoneinfo

from . import ENV_TZ, cron, keystore
from .langs import norm_lang

#: Parse failures, reported by errors(). The bad setting keeps its default.
_LOAD_ERRORS: list[str] = []

#: Sealed credentials this deploy cannot open, reported by warnings(). Not
#: fatal: the listener must come up so the key can be re-entered through the
#: UI. The service each belongs to reads as unset meanwhile.
_SEALED_ERRORS: list[str] = []

SETTINGS_FILE = "settings.json"

#: A .env in the working directory, loaded before the reads below so a source
#: checkout can set WORK_DIR and the rest. The image ships none.
ENV_FILE = ".env"


def _load_dotenv(path: str = ENV_FILE) -> dict[str, str]:
    """Copy NAME=VALUE lines from a .env file into the environment and return
    what the file held.

    Blank and # lines are skipped; any other line without = reaches errors().
    Surrounding quotes are dropped. Existing variables are never overwritten.
    The return value is for the one name read before this runs; see STATED_TZ.
    """
    values: dict[str, str] = {}
    try:
        with open(path) as env_file:
            lines = env_file.read().splitlines()
    except FileNotFoundError:
        return values
    except OSError as err:
        _LOAD_ERRORS.append(f"{ENV_FILE} could not be read ({err})")
        return values
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
        values[name.strip()] = value
        os.environ.setdefault(name.strip(), value)
    return values


_DOTENV = _load_dotenv()

#: Environment only: it says where the settings file is.
STATE_DIR = os.environ.get("STATE_DIR", "/config")


def _settings_path() -> str:
    return os.path.join(STATE_DIR, SETTINGS_FILE)


def _load_settings() -> dict[str, str]:
    """STATE_DIR/settings.json as name -> raw string, {} when absent.

    Values are the strings the same-named variable would hold, so the parsers
    treat both sources alike; numbers and booleans read as their JSON literals.
    Anything else is refused per key so it reaches errors().
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


def _load_key() -> bytes | None:
    """The key that opens sealed credentials, or None where none exists.

    Read, never minted: a start that seals nothing should create no file, and
    one that cannot open a sealed value should say so.
    """
    try:
        return keystore.read(STATE_DIR)
    except ValueError as err:
        _SEALED_ERRORS.append(str(err))
        return None


#: Rebound by every importlib.reload(config), which is how a settings write
#: takes effect.
KEY = _load_key()

#: Every name a parser asked for, so errors() can report settings-file keys
#: nothing reads. The environment gets no such check.
_READ_NAMES: set[str] = set()


def _raw(name: str, default: str) -> str:
    """One setting's raw value: environment, then settings file, then default.

    A blank environment value is a leftover (``REWRITE_MODE: ${REWRITE_MODE}``
    expands to empty), so a settings-file entry beats it.
    """
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


def _ordered(name: str, default: str) -> tuple[str, ...]:
    """Like :func:`_set`, keeping the written order. A tuple so a Policy field
    built from it stays hashable. Repeats collapse to their first mention."""
    seen = dict.fromkeys(
        part.strip().lower() for part in _raw(name, default).split(",") if part.strip()
    )
    return tuple(seen)


#: One variable per rule (RULE_LANGUAGES, RULE_COVER_ART...), each naming a
#: mode in policy.MODES.
RULE_PREFIX = "RULE_"


def rule_variable(rule: str) -> str:
    """Which variable sets a rule's mode, for the startup report."""
    return RULE_PREFIX + rule.upper()


def _rule_modes() -> dict[str, str]:
    """Rule name -> the mode its variable states.

    Scanned rather than read by name: the rule vocabulary is in
    :mod:`trackstarr.policy`, which imports this module, so unknown names and
    modes are checked in :func:`trackstarr.policy.errors`. Dashes read as
    underscores. The environment scans second, so it wins.
    """
    modes: dict[str, str] = {}
    for source in (_SETTINGS, os.environ):
        for variable, value in source.items():
            if variable.startswith(RULE_PREFIX) and (stated := value.strip()):
                rule = variable.removeprefix(RULE_PREFIX).lower().replace("-", "_")
                modes[rule] = stated.lower()
    return modes


#: Closed sets, so a typo is refused rather than read as false, which for
#: SKIP_HARDLINKS would break the link on every seeding file. Empty reads as
#: unset.
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _bool(name: str, default: str = "false") -> bool:
    raw = _raw(name, default).strip()
    value = raw.lower() or default
    if value not in _TRUE and value not in _FALSE:
        _LOAD_ERRORS.append(f"{name}={raw!r} is neither true nor false")
        value = default
    return value in _TRUE


def _choice(name: str, default: str, valid: tuple[str, ...]) -> str:
    """A setting from a closed set of names. A typo is refused, since
    REWRITE_MODE misread as its default would rewrite a library."""
    raw = _raw(name, default).strip().lower()
    value = raw or default
    if value not in valid:
        _LOAD_ERRORS.append(f"{name}={raw!r} is not one of: {', '.join(valid)}")
        return default
    return value


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


def _unseal(name: str, value: str) -> str:
    """A settings-file credential in plain text.

    Plain text is taken as written, since the file is hand-editable and anyone
    who could write it could read the sealed one; the next UI save seals it. A
    sealed value this deploy cannot open reads as unset and warnings() says
    why.
    """
    if not keystore.sealed(value):
        return value
    if KEY is None:
        _SEALED_ERRORS.append(
            f"{name} is sealed but no key was found at {keystore.key_path(STATE_DIR)}; "
            "its service is off until the key is restored or the credential re-entered"
        )
        return ""
    try:
        return keystore.unseal(KEY, name, value)
    except ValueError as err:
        _SEALED_ERRORS.append(str(err))
        return ""


def _secret(name: str) -> str:
    """A credential from the environment, or from a file named by ``NAME_FILE``
    (the official images' convention) or ``FILE__NAME`` (linuxserver.io's).

    A file keeps the key out of ``docker inspect``. Naming one credential two
    ways is refused. Whitespace-only counts as unset. The settings file may
    hold the plain name; any environment form beats it.
    """
    # Registered here too: when an indirect form wins, _raw never runs and the
    # file's entry would read as a typo.
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
        # No environment form: the file entry or blank. Only this branch can be
        # sealed.
        return _unseal(name, _raw(name, "").strip())
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
        # A blank key would quietly disable the service.
        _LOAD_ERRORS.append(f"{variable}={value!r} is empty")
    return content


def _path_map(name: str) -> list[tuple[str, str]]:
    """Comma-separated ``LOCAL=REMOTE`` prefix pairs, longest local first.

    A media server's mount need not be ours: Plex on ``/srv/media`` while we
    walk ``/data/media`` would match nothing. ``=`` because a Windows path
    holds a colon.
    """
    pairs: list[tuple[str, str]] = []
    for entry in _raw(name, "").split(","):
        if not entry.strip():
            continue
        local, sep, remote = entry.partition("=")
        # The prefix test joins its own slash; a trailing one would match nothing.
        local, remote = local.strip().rstrip("/"), remote.strip().rstrip("/")
        if not sep or not local or not remote:
            _LOAD_ERRORS.append(f"{name} entry {entry.strip()!r} is not LOCAL=REMOTE")
            continue
        pairs.append((local, remote))
    # Longest first, so the first prefix match is the most specific.
    pairs.sort(key=lambda pair: -len(pair[0]))
    return pairs


def _langs(name: str, default: str) -> set[str]:
    """Languages normalised to ISO 639-2/B like every track tag, so "en",
    "English" and "eng" all mean the same. Unrecognised entries pass through
    and are reported at startup."""
    return {code for entry in _set(name, default) if (code := norm_lang(entry))}


#: The zone the deploy stated, or empty. :data:`trackstarr.ENV_TZ` is the
#: environment at process start, before a saved zone is written into TZ. A
#: .env line counts too.
STATED_TZ = ENV_TZ or _DOTENV.get("TZ", "").strip()


def _tz_error(zone: str) -> str:
    """Why a zone name cannot be used, or "" when it can."""
    try:
        zoneinfo.ZoneInfo(zone)
    except KeyError, ValueError, OSError:
        return f"TZ={zone!r} is not a zone this system knows, such as Pacific/Auckland"
    return ""


def _timezone() -> str:
    """The zone the service runs in, applied to the process as it is read.

    libc reads TZ, and the log, event stamps and cron follow it, so a saved
    zone must reach the environment to mean anything. The environment's own
    wins.
    """
    _READ_NAMES.add("TZ")
    if STATED_TZ:
        return STATED_TZ
    zone = (_SETTINGS.get("TZ") or "").strip()
    if zone and _tz_error(zone):
        # Not applied: libc reads an unknown zone as UTC, silently. errors()
        # reports it and rolls a saved one back.
        return zone
    if zone:
        os.environ["TZ"] = zone
    else:
        # Ours, left behind by a removed entry; a stated zone never reaches here.
        os.environ.pop("TZ", None)
    time.tzset()
    return zone


#: The clock the schedule, log and event stamps use. An IANA name such as
#: Pacific/Auckland; unset means UTC in a container.
TZ = _timezone()

#: Where the sweep walks. Webhook and fix paths are taken as given.
MEDIA_DIRS = _list("MEDIA_DIRS", "/data/media/movies:/data/media/tv")

#: Where a rewrite is staged before replacing the original. Any filesystem
#: works; wants room for the biggest file and speed.
WORK_DIR = _raw("WORK_DIR", "/data/trackstarr-work")

#: Every credential also takes RADARR_API_KEY_FILE or FILE__RADARR_API_KEY
#: naming a file to read it from; see _secret.
RADARR_URL = _raw("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = _secret("RADARR_API_KEY")
SONARR_URL = _raw("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = _secret("SONARR_API_KEY")

#: Media servers to refresh after a rewrite, since their watchers see nothing
#: on a network mount. Jellyfin's settings fit Emby too.
PLEX_URL = _raw("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = _secret("PLEX_TOKEN")
JELLYFIN_URL = _raw("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = _secret("JELLYFIN_API_KEY")

#: Where a browser reaches the same four, for the title sheet's "Open in"
#: links. The addresses above are this container's, and ``http://plex:32400``
#: on a compose network is not a browser's. Unset means the address above is.
RADARR_PUBLIC_URL = _raw("RADARR_PUBLIC_URL", "").rstrip("/")
SONARR_PUBLIC_URL = _raw("SONARR_PUBLIC_URL", "").rstrip("/")
PLEX_PUBLIC_URL = _raw("PLEX_PUBLIC_URL", "").rstrip("/")
JELLYFIN_PUBLIC_URL = _raw("JELLYFIN_PUBLIC_URL", "").rstrip("/")

#: How each server spells the library when it differs from ours. One
#: ``LOCAL=REMOTE`` pair per library; unset means the mounts match. See
#: _path_map.
PLEX_PATH_MAP = _path_map("PLEX_PATH_MAP")
JELLYFIN_PATH_MAP = _path_map("JELLYFIN_PATH_MAP")

#: Whether to fetch IMDb's public ratings dataset daily, for the title sheet's
#: score. Off leaves every title unscored. Optional because IMDb licences the
#: dataset for personal, non-commercial use.
IMDB_RATINGS = _bool("IMDB_RATINGS", "true")

#: Languages kept regardless of the title's original language.
ALWAYS_KEEP_LANGS = _langs("ALWAYS_KEEP_LANGS", "eng")

#: Keep the title's original language, as the *arrs report it, on top of
#: ALWAYS_KEEP_LANGS. Off is for a library watched entirely dubbed.
KEEP_ORIGINAL_LANG = _bool("KEEP_ORIGINAL_LANG", "true")

#: What each rule may do: never, alongside a rewrite something else ordered, or
#: always. Only rules whose variable is set appear; the rest keep policy.RULES'
#: default.
RULE_MODES = _rule_modes()

#: Layouts the downmix rule guarantees. A missing one is made from the best
#: surviving bigger track, never upmixed. The written order is also the track
#: order: 2.0 first means a disposition-blind player lands on stereo.
DOWNMIX_LAYOUTS = _ordered("DOWNMIX_LAYOUTS", "2.0,5.1")

#: Guarantee every layout in the title's original language rather than in
#: whichever language the best source is in.
DOWNMIX_ORIGINAL_LANG = _bool("DOWNMIX_ORIGINAL_LANG", "true")

#: Languages guaranteed every layout besides the original. A language no
#: surviving track speaks generates nothing. See planner._choose_downmixes.
DOWNMIX_LANGS = _langs("DOWNMIX_LANGS", "")

#: Defaults for the two shipped layouts. AAC is what every phone and browser
#: decodes, and 320k is past transparency for ffmpeg's encoder; AC-3 is what
#: every receiver takes over HDMI, at the rate discs ship it. layouts.py says
#: why each layout carries its own.
_DEFAULT_CODECS = {"2.0": "aac", "5.1": "ac3"}
_DEFAULT_BITRATES = {"2.0": "320k", "5.1": "640k"}

#: AUDIO_CODEC_5_1 and AUDIO_BITRATE_5_1 set 5.1's encoder and rate. Dots
#: become underscores in a variable name.
CODEC_PREFIX = "AUDIO_CODEC_"
BITRATE_PREFIX = "AUDIO_BITRATE_"

#: For the unread-name check and the settings API's allow-list, which can only
#: ask whether a name is per-layout since the set follows DOWNMIX_LAYOUTS.
LAYOUT_PREFIXES = (CODEC_PREFIX, BITRATE_PREFIX)

#: Prefixes scanned for rather than read by name, and so not in _READ_NAMES.
SCANNED_PREFIXES = (*LAYOUT_PREFIXES, RULE_PREFIX)


def codec_variable(name: str) -> str:
    """Which variable states a layout's encoder, for the startup report."""
    return CODEC_PREFIX + name.replace(".", "_")


def bitrate_variable(name: str) -> str:
    """Which variable states a layout's rate, for the startup report."""
    return BITRATE_PREFIX + name.replace(".", "_")


def _per_layout(prefix: str, defaults: dict[str, str]) -> dict[str, str]:
    """Layout name -> what its ``prefix`` variable states.

    Scans both sources so a value for a layout nothing asks for reaches
    warnings(). The environment scans second, so it wins.
    """
    values = dict(defaults)
    for source in (_SETTINGS, os.environ):
        for variable, value in source.items():
            if variable.startswith(prefix) and (stated := value.strip()):
                values[variable.removeprefix(prefix).replace("_", ".").lower()] = stated
    return values


def _stated(variable: str) -> bool:
    """Whether either source names the variable, for the half-edit check."""
    return variable in os.environ or variable in _SETTINGS


#: One AUDIO_CODEC_ and one AUDIO_BITRATE_ per layout; layouts.py says why.
AUDIO_CODECS = _per_layout(CODEC_PREFIX, _DEFAULT_CODECS)
AUDIO_BITRATES = _per_layout(BITRATE_PREFIX, _DEFAULT_BITRATES)

#: How far the regenerate rule reaches. "generated" rebuilds our own tracks
#: whose settings changed; "all" also replaces a real track well below its
#: layout's rate. Validated against policy.REGENERATE_SCOPES.
REGENERATE_SCOPE = _raw("REGENERATE_SCOPE", "generated").strip().lower()

#: How far under its layout's rate, in percent, a real track must be before
#: "all" calls it low-bitrate. A 448k AC-3 5.1 must not read as low against
#: 640k.
REGENERATE_BELOW_PERCENT = _int("REGENERATE_BELOW_PERCENT", "50")

#: Its valid range. At 100 every variable-rate track reads as low; near 0
#: none does.
REGENERATE_BELOW_BAND = (10, 90)

#: Containers we will rewrite. AVI and MPG are left out: a stream copy into
#: one with a fresh AAC track is usually unplayable.
ALLOWED_EXTS = _set("ALLOWED_EXTS", ".mkv,.mp4,.m4v")

#: Leave hard-linked files alone: rewriting one breaks the link and doubles
#: its disk cost until the torrent goes. See HARDLINK_RECHECK.
SKIP_HARDLINKS = _bool("SKIP_HARDLINKS", "true")

#: Seconds between re-checks of files parked by SKIP_HARDLINKS. 0 parks nothing
#: and leaves them to the sweep.
HARDLINK_RECHECK = _int("HARDLINK_RECHECK", "900")

#: Matched against the track title when the disposition flags are unset, as in
#: most rips.
COMMENTARY_RE = _regex(
    "COMMENTARY_PATTERN",
    r"comment|descriptive|described\s*video|audio\s*description|"
    r"isolated\s*score|director'?s?\s*track|interview|behind\s*the\s*scenes",
)

#: Subtitles for the deaf and hard-of-hearing (SDH) by title, for muxers that
#: leave the hearing_impaired disposition unset.
SDH_RE = _regex("SDH_PATTERN", r"\bsdh\b|\bcc\b|hearing[\s._-]*impaired")

#: Likewise for forced subtitles and the forced disposition.
FORCED_RE = _regex("FORCED_PATTERN", r"\bforced\b")

#: Release junk in track titles: bitrates, resolutions, source tags, codec
#: names. Cleared during a rewrite. Conservative: a bare "AC3 5.1" survives.
JUNK_TITLE_RE = _regex(
    "JUNK_TITLE_PATTERN",
    r"\d+\s*k?bps"
    r"|\bx?26[45]\b|\bhevc\b|\bavc\b"
    r"|\b(?:480|576|720|1080|2160)[pi]\b"
    r"|\b(?:blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|remux)\b",
)

#: The built web UI, served on every GET the API does not claim; empty serves
#: nothing. The image points this at its build of web/.
WEB_DIR = _raw("WEB_DIR", "")

#: First run only: the admin account's starting password. Unset, one is
#: generated and logged, to be changed at first sign-in. Ignored once a user
#: store exists.
ADMIN_PASSWORD = _secret("ADMIN_PASSWORD")

#: Environment only: logging is configured once at start, and every setting in
#: the file applies live. :func:`trackstarr.cli.main` refuses an unknown name.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "").strip().upper() or "INFO"

LISTEN_ADDR = _raw("LISTEN_ADDR", "0.0.0.0")
#: 5120 spells 5.1 and 2.0, and is clear of the ports the rest of the stack
#: claims.
LISTEN_PORT = _int("LISTEN_PORT", "5120")

#: Advertised when registering the webhook, so it must be reachable from the
#: *arrs' containers. Base URL only; registration appends the path.
WEBHOOK_URL = _raw("WEBHOOK_URL", f"http://trackstarr:{LISTEN_PORT}").rstrip("/")

FFMPEG_TIMEOUT = _int("FFMPEG_TIMEOUT", "7200")
PROBE_TIMEOUT = _int("PROBE_TIMEOUT", "180")

#: Rewrites allowed at once across the webhook workers, the sweep and any
#: process sharing STATE_DIR. 1 because spinning disks thrash; the README says
#: how to measure a faster one.
MAX_CONCURRENT_REWRITES = _int("MAX_CONCURRENT_REWRITES", "1")

#: Files the sweep probes at once. Separate from the rewrite budget: a disk
#: that wants one rewrite at a time still takes several probes, and a cold
#: sweep at one probe at a time is hours.
PROBE_WORKERS = _int("PROBE_WORKERS", "4")

#: When the sweep runs, as a five-field cron schedule in local time; empty
#: disables it. REWRITE_MODE says what it may do.
SWEEP_AT = _raw("SWEEP_AT", "").strip()

#: What this install may rewrite, as a ladder. ``report`` rewrites nothing,
#: even under ``sweep --apply``, so a new install can watch a week first.
#: ``imports`` rewrites webhook deliveries and leaves the scheduled sweep
#: reporting. ``all`` lets the scheduled sweep rewrite too.
REWRITE_MODES = ("report", "imports", "all")
REWRITE_MODE = _choice("REWRITE_MODE", "imports", REWRITE_MODES)


#: Every setting holding a base URL, for the scheme check below.
_URL_SETTINGS = (
    "RADARR_URL",
    "SONARR_URL",
    "PLEX_URL",
    "JELLYFIN_URL",
    "RADARR_PUBLIC_URL",
    "SONARR_PUBLIC_URL",
    "PLEX_PUBLIC_URL",
    "JELLYFIN_PUBLIC_URL",
    "WEBHOOK_URL",
)

#: Each URL and its credential. Setting one alone leaves the service off,
#: which is a warning: clearing an address is how a service is switched off.
_CREDENTIALLED = (
    ("RADARR_URL", "RADARR_API_KEY"),
    ("SONARR_URL", "SONARR_API_KEY"),
    ("PLEX_URL", "PLEX_TOKEN"),
    ("JELLYFIN_URL", "JELLYFIN_API_KEY"),
)


def errors() -> list[str]:
    """Startup-fatal configuration problems, as ready-to-log messages.

    Checks needing the rule and layout vocabulary are in
    :func:`trackstarr.policy.errors`.
    """
    problems = list(_LOAD_ERRORS)
    # Without a scheme urllib raises something unhelpful on a background
    # thread, once per call.
    problems += [
        f"{name}={value!r} must start with http:// or https://"
        for name in _URL_SETTINGS
        if (value := globals()[name]) and not value.startswith(("http://", "https://"))
    ]
    # Fatal: an unknown zone reads as UTC, so a 04:00 sweep runs at the wrong
    # hour and every stamp agrees with it.
    if TZ and (problem := _tz_error(TZ)):
        problems.append(problem)
    # The file's names are a closed set, so an unread key is a typo silently
    # meaning its default. The full path and the fix are spelled out because
    # nobody can reach the settings pages to correct this one.
    if ignored := sorted(
        name
        for name in _SETTINGS
        if name not in _READ_NAMES and not name.startswith(SCANNED_PREFIXES)
    ):
        problems.append(
            f"{_settings_path()} names settings nothing reads: {', '.join(ignored)}; "
            "remove or rename them"
        )
    # Below one: the slot pool hangs, the probe pool refuses to start, and
    # every ffmpeg run times out.
    for name, value in (
        ("MAX_CONCURRENT_REWRITES", MAX_CONCURRENT_REWRITES),
        ("PROBE_WORKERS", PROBE_WORKERS),
        ("FFMPEG_TIMEOUT", FFMPEG_TIMEOUT),
        ("PROBE_TIMEOUT", PROBE_TIMEOUT),
    ):
        if value < 1:
            problems.append(f"{name}={value} must be at least 1")
    # 0 is a real answer (park nothing); only negative is wrong.
    if HARDLINK_RECHECK < 0:
        problems.append(f"HARDLINK_RECHECK={HARDLINK_RECHECK} cannot be negative")
    least, most = REGENERATE_BELOW_BAND
    if not least <= REGENERATE_BELOW_PERCENT <= most:
        problems.append(
            f"REGENERATE_BELOW_PERCENT={REGENERATE_BELOW_PERCENT} must be between "
            f"{least} and {most}"
        )
    if SWEEP_AT:
        try:
            # Left to the scheduler, a bad schedule would kill only its thread.
            cron.parse(SWEEP_AT)
        except ValueError as err:
            problems.append(f"SWEEP_AT={SWEEP_AT!r}: {err}")
    return problems


def warnings() -> list[str]:
    """Probable mistakes that must not refuse startup.

    A missing MEDIA_DIRS entry may be a library mounted late. An encoder or
    rate for a layout nothing asks for is usually half an edit.
    """
    problems = list(_SEALED_ERRORS)
    problems += [
        f"MEDIA_DIRS entry {media_dir} does not exist; the sweep will find nothing there"
        for media_dir in MEDIA_DIRS
        if not os.path.isdir(media_dir)
    ]
    problems += [
        f"{variable(name)} is set but DOWNMIX_LAYOUTS does not ask for {name}, "
        "so nothing reads it"
        for values, variable in (
            (AUDIO_CODECS, codec_variable),
            (AUDIO_BITRATES, bitrate_variable),
        )
        for name in sorted(values)
        # The variable, not the entry: 2.0 and 5.1 are always present as
        # defaults.
        if name not in DOWNMIX_LAYOUTS and _stated(variable(name))
    ]
    problems += [
        f"{set_name} is set but {unset_name} is not, so that service stays switched off"
        for url_name, key_name in _CREDENTIALLED
        for set_name, unset_name in [(url_name, key_name), (key_name, url_name)]
        if globals()[set_name] and not globals()[unset_name]
    ]
    return problems
