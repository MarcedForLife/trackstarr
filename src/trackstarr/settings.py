"""Settings the web UI reads and writes: the STATE_DIR/settings.json half of
:mod:`trackstarr.config`.

Only EDITABLE names and the scanned prefixes are accepted. A name the
environment pins is refused, since the file entry would sit there overridden.
Every write runs startup's validation, and a change that would refuse startup
is rolled back whole.
"""

import contextlib
import functools
import importlib
import json
import os
import re
import threading
import zoneinfo
from collections.abc import Iterable

from . import config, events, keystore, langs, policy, tracks
from .executor import audio_codec_errors
from .state import write_json

#: What the UI edits, on top of the scanned RULE_* prefix.
EDITABLE = frozenset(
    {
        "TZ",
        "REWRITE_MODE",
        "LANGUAGES",
        "AUDIO_LAYOUTS",
        "REGENERATE_SCOPE",
        "REGENERATE_BELOW_PERCENT",
        "REGENERATE_ABOVE_PERCENT",
        "ALLOWED_EXTS",
        "SKIP_HARDLINKS",
        "HARDLINK_RECHECK",
        "MAX_CONCURRENT_REWRITES",
        "PROBE_WORKERS",
        "FFMPEG_TIMEOUT",
        "PROBE_TIMEOUT",
        "COMMENTARY_PATTERN",
        "SDH_PATTERN",
        "FORCED_PATTERN",
        "RELEASE_TAG_PATTERN",
        "MEDIA_DIRS",
        "SWEEP_AT",
        "IMDB_RATINGS",
        "RADARR_URL",
        "RADARR_API_KEY",
        "RADARR_PUBLIC_URL",
        "SONARR_URL",
        "SONARR_API_KEY",
        "SONARR_PUBLIC_URL",
        "PLEX_URL",
        "PLEX_TOKEN",
        "PLEX_PATH_MAP",
        "PLEX_PUBLIC_URL",
        "JELLYFIN_URL",
        "JELLYFIN_API_KEY",
        "JELLYFIN_PATH_MAP",
        "JELLYFIN_PUBLIC_URL",
        "WEBHOOK_URL",
    }
)

#: Lists whose entries are not comma-separated. A directory name may hold a
#: comma.
SEPARATORS = {"MEDIA_DIRS": ":"}

#: Credentials, which the snapshot reports as set-or-not and never echoes:
#: /api/settings is a read any session may make.
SECRETS = frozenset({"RADARR_API_KEY", "SONARR_API_KEY", "PLEX_TOKEN", "JELLYFIN_API_KEY"})

#: Settings config holds under another name, since it keeps the pattern
#: compiled. Every other name is its own attribute.
_ATTRIBUTES = {
    "COMMENTARY_PATTERN": "COMMENTARY_RE",
    "SDH_PATTERN": "SDH_RE",
    "FORCED_PATTERN": "FORCED_RE",
    "RELEASE_TAG_PATTERN": "RELEASE_TAG_RE",
}

#: One write (and its validate-or-roll-back) at a time.
_WRITE_LOCK = threading.Lock()


def _settings_path() -> str:
    return os.path.join(config.STATE_DIR, config.SETTINGS_FILE)


def editable(name: str) -> bool:
    return name in EDITABLE or name.startswith(config.SCANNED_PREFIXES)


def env_pinned(name: str) -> bool:
    """Whether the environment states the name, which beats the file. A
    credential counts in any of the forms :func:`trackstarr.config._secret`
    reads."""
    if name == "TZ":
        # This process writes TZ itself to apply a saved zone, so config keeps
        # the deploy's own word from before that.
        return bool(config.STATED_TZ)
    forms = (name, f"{name}_FILE", f"FILE__{name}") if name in SECRETS else (name,)
    return any((os.environ.get(form) or "").strip() for form in forms)


@functools.cache
def zones() -> list[str]:
    """Every zone name this system knows, for the picker. Empty on a build
    without tzdata; the page then takes a zone as typed."""
    try:
        return sorted(zoneinfo.available_timezones())
    except zoneinfo.ZoneInfoNotFoundError:
        return []


def _pairs(mapping: Iterable[tuple[str, str]]) -> list[str]:
    """A parsed path map back as ``LOCAL=REMOTE`` entries."""
    return [f"{local}={remote}" for local, remote in mapping]


def _held(name: str) -> object:
    """One setting as the page reads it, from the running config.

    Shaped by type rather than by name: config's parser already chose it, so a
    tuple's order is part of the setting and a set has none of its own.
    """
    held = getattr(config, _ATTRIBUTES.get(name, name))
    if isinstance(held, re.Pattern):
        return held.pattern
    # Before the int test, which a bool passes: the page wants a boolean.
    if isinstance(held, bool):
        return held
    # Whole numbers travel as the strings the file holds; _serialise takes no
    # numbers.
    if isinstance(held, int):
        return str(held)
    # Sorted: a set's own order is a hash order, so the answer would move.
    if isinstance(held, set | frozenset):
        return sorted(held)
    if isinstance(held, tuple | list):
        # A path map holds pairs; every other list keeps its written order.
        return _pairs(held) if held and isinstance(held[0], tuple) else list(held)
    return held


def _values() -> dict[str, object]:
    """Every setting the UI edits, as the running config holds it. Credentials
    are left out; each caller adds its own form.

    Sorted, or EDITABLE's hash order would reshuffle the answer every run.
    """
    values: dict[str, object] = {name: _held(name) for name in sorted(EDITABLE - SECRETS)}
    # Every rule's effective mode, not only the stated ones.
    for rule, mode in policy.resolved_modes().items():
        values[config.rule_variable(rule)] = mode
    return values


def snapshot() -> dict:
    """The settings page's read: effective values, and who set each one."""
    values = _values()
    entries = {
        name: {"value": value, "env": env_pinned(name)} for name, value in values.items()
    }
    # Set-or-not in place of the value. Sorted, or a frozenset's iteration
    # order reshuffles the contract fixture on every run.
    for name in sorted(SECRETS):
        entries[name] = {
            "value": "",
            "env": env_pinned(name),
            "set": bool(getattr(config, name)),
        }
    return {
        # So the page keeps no second copy of the vocabulary.
        "rules": {
            name: {"default": rule.default, "summary": rule.summary}
            for name, rule in policy.RULES.items()
        },
        "modes": list(policy.MODES),
        # Codes outside this still normalise; the page takes them as typed.
        "languages": dict(langs.LANG_LABELS),
        # Empty on a build with no tzdata; the field still takes one.
        "zones": zones(),
        "containers": sorted(policy.MUXERS),
        # What a layout row may be set to, in the page's order.
        "actions": list(tracks.ACTIONS),
        # A language row's, narrower: RULE_LANGUAGES does the dropping.
        "lang_actions": list(tracks.LANG_ACTIONS),
        # The reserved LANGUAGES name for the title's own language.
        "original_lang": tracks.ORIGINAL,
        # What a row added in the page is made at, so it keeps no second copy.
        "stock": {name: list(spec) for name, spec in tracks.STOCK.items()},
        # The rates each size is offered at, low to high.
        "rates": {name: list(rates) for name, rates in tracks.RATES.items()},
        # Not a closed list: an unlisted encoder is checked against ffmpeg.
        "codecs": [
            {
                "name": codec.name,
                "max_channels": codec.max_channels,
                "containers": sorted(codec.containers),
                "lossless": codec.lossless,
            }
            for codec in tracks.CODECS.values()
        ],
        # So the page knows an empty field means "leave it alone".
        "secrets": sorted(SECRETS),
        "settings": entries,
    }


def _serialise(name: str, value) -> str | None:
    """One change as the string the file holds; None removes the entry.
    Raises ValueError on a shape the file cannot hold."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        separator = SEPARATORS.get(name, ",")
        # An entry containing the separator would read back as two, quietly.
        if any(separator in item for item in value):
            raise ValueError(
                f"{name} entries cannot contain {separator!r}, which separates them"
            )
        return separator.join(value)
    raise ValueError(f"{name} must be a string, boolean, list of strings or null")


def _problems(check_codec: bool) -> list[str]:
    """Everything wrong with the config as it stands, for update()'s
    before-and-after comparison.

    The encoder check shells out to ``ffmpeg -encoders``, so it runs only for
    a save that touches an encoder.
    """
    problems = config.errors() + policy.errors()
    if check_codec:
        problems += audio_codec_errors()
    return problems


def _read_file() -> dict | None:
    """The file's current object, or None with no file. Unreadable raises
    ValueError, since a guess could roll "back" to nothing."""
    try:
        with open(_settings_path()) as settings_file:
            data = json.load(settings_file)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"{config.SETTINGS_FILE} could not be read ({err})") from err
    if not isinstance(data, dict):
        raise ValueError(f"{config.SETTINGS_FILE} must hold one JSON object of settings")
    return data


def _write(content: dict | None) -> None:
    """Write the file (None removes it) and reload config in place, which is
    what applies the change without a restart."""
    if content is None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(_settings_path())
    else:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        # 0600 like the user and session stores: this file holds credentials.
        write_json(_settings_path(), content, mode=0o600)
    importlib.reload(config)


def _seal_secrets(merged: dict) -> None:
    """Seal every plain-text credential the file is about to hold, whether
    the page sent it or an operator hand-wrote it earlier.

    The key is minted on the first write with a credential to keep, so a deploy
    that sets them all by environment never grows a key file. Raises ValueError
    or OSError.
    """
    plain = [
        name
        for name in sorted(SECRETS)
        if (held := merged.get(name)) and isinstance(held, str) and not keystore.sealed(held)
    ]
    if not plain:
        return
    key = keystore.ensure(config.STATE_DIR)
    for name in plain:
        merged[name] = keystore.seal(key, name, merged[name])


def _recorded() -> dict[str, object]:
    """Every setting as the history keeps it: credentials as set-or-not."""
    values = _values()
    for name in SECRETS:
        values[name] = "set" if getattr(config, name) else "unset"
    return values


def _changes(before: dict, after: dict) -> dict[str, dict]:
    """What a write altered, as ``from``/``to`` per name.

    Read off the running config either side of the write, not the body: a
    field saved unchanged altered nothing, and one name can move another. Both
    sides come from :func:`_recorded`, so both hold every name and a change
    always has two sides.
    """
    return {
        name: {"from": before[name], "to": after[name]}
        for name in sorted(before)
        if before[name] != after[name]
    }


def update(changes: dict, by: str | None = None) -> list[str]:
    """Apply the UI's changes to the file, live. Raises OSError.

    Returns problems ready to show; empty means applied. Only new problems
    count, so a deploy broken some other way can still save an unrelated
    setting. ``by`` is the account the history credits.
    """
    try:
        serialised = {name: _serialise(name, value) for name, value in changes.items()}
    except ValueError as err:
        return [str(err)]
    for name in serialised:
        if not editable(name):
            return [f"{name} is not a setting the UI edits"]
        if env_pinned(name):
            return [f"{name} is set by the environment, which wins over anything saved here"]
    with _WRITE_LOCK:
        try:
            previous = _read_file()
        except ValueError as err:
            return [str(err)]
        merged = dict(previous or {})
        for name, value in serialised.items():
            if value is None:
                merged.pop(name, None)
            else:
                merged[name] = value
        try:
            _seal_secrets(merged)
        except (ValueError, OSError) as err:
            return [f"the credentials could not be sealed ({err})"]
        # Every layout's encoder is in the list now, so one name says whether
        # the save could bring a new one into use.
        check_codec = "AUDIO_LAYOUTS" in serialised
        known = _problems(check_codec)
        was = _recorded()
        _write(merged)
        problems = [problem for problem in _problems(check_codec) if problem not in known]
        if problems:
            _write(previous)
            return problems
        altered = _changes(was, _recorded())
    # Most settings are not rules, so the config line below would leave them
    # out of the history. A save that altered nothing leaves no line.
    if altered:
        events.record("settings", changed=altered, by=by)
    # No line when the rules in force are unchanged.
    events.record_config(policy.Policy.from_config())
    return []
