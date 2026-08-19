"""Runtime configuration, all of it from the environment.

Values are read once at import. Other modules reference them as
``config.NAME`` rather than importing the names directly, so tests can
monkeypatch a single attribute without reloading anything.

A malformed value never raises here: import must succeed so the CLI can
report every problem at once through :func:`errors`. The checks that need
the rule and layout vocabulary live in :func:`trackstarr.policy.errors`;
startup exits on anything either returns.
"""

import os
import re

from . import cron
from .langs import norm_lang

#: Problems found while parsing the environment, reported via errors().
#: The malformed setting keeps its default so import always succeeds.
_LOAD_ERRORS: list[str] = []


def _list(name: str, default: str) -> list[str]:
    return [part for part in os.environ.get(name, default).split(":") if part]


def _set(name: str, default: str) -> set[str]:
    parts = os.environ.get(name, default).split(",")
    return {part.strip().lower() for part in parts if part.strip()}


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


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
    """A credential, read from the file a companion variable names.

    ``NAME_FILE`` is the convention the official images use and ``FILE__NAME``
    is linuxserver.io's. Every *arr in a normal stack is one or the other, so
    both work here rather than making it something to look up. Pointing at a
    file keeps the credential out of the compose file and out of ``docker
    inspect``, which is where these actually leak; it is still plaintext on
    disk, so it is hygiene rather than a boundary.

    Naming the same credential more than one way is refused instead of
    resolved by precedence, because which one was live would otherwise be
    invisible. A variable set to whitespace doesn't count as naming it: a
    leftover ``RADARR_API_KEY: ${RADARR_API_KEY}`` with nothing behind it
    expands to empty, and that is not a conflict worth refusing to start over.
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
        # An empty file would leave the service quietly disabled, since a
        # blank key reads the same as one that was never set.
        _LOAD_ERRORS.append(f"{variable}={value!r} is empty")
    return content


def _langs(name: str, default: str) -> set[str]:
    """A language set normalised to ISO 639-2/B like every track tag, so
    "en", "English" and "eng" all mean the same thing (norm_lang's lookup
    covers the *arr names too). An entry nothing recognises passes through
    unchanged; it matches no track and shows up verbatim in the startup
    summary rather than silently disappearing."""
    return {code for entry in _set(name, default) if (code := norm_lang(entry))}


#: Where the sweep walks, and what startup compares WORK_DIR's filesystem
#: against. Webhook and fix paths are taken as given, wherever they live.
MEDIA_DIRS = _list("MEDIA_DIRS", "/data/media/movies:/data/media/tv")

#: Where a rewrite is staged while ffmpeg writes it, before it replaces the
#: original. Any filesystem works, even another drive; executor._publish
#: keeps the replacement atomic either way. Somewhere with room for the
#: largest file in the library, and fast, since every byte of every rewrite
#: is written here.
WORK_DIR = os.environ.get("WORK_DIR", "/data/trackstarr-work")

STATE_DIR = os.environ.get("STATE_DIR", "/config")

#: Every credential also takes RADARR_API_KEY_FILE or FILE__RADARR_API_KEY
#: naming a file to read it from; see _secret.
RADARR_URL = os.environ.get("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = _secret("RADARR_API_KEY")
SONARR_URL = os.environ.get("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = _secret("SONARR_API_KEY")

#: Media servers to nudge after a rewrite, so track lists and sizes stay
#: true even when the library is on a network mount their own filesystem
#: watchers can't see. Omit to disable. The Jellyfin settings fit Emby too.
PLEX_URL = os.environ.get("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = _secret("PLEX_TOKEN")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = _secret("JELLYFIN_API_KEY")

#: Languages kept regardless of the title's original language.
ALWAYS_KEEP = _langs("ALWAYS_KEEP_LANGS", "eng")

#: Rules to switch off, for setups where another service owns part of the
#: job. Valid names are the keys of policy.RULES; startup refuses others.
DISABLED_RULES = _set("DISABLED_RULES", "")

#: Drop commentary, described-audio and isolated-score tracks outright
#: instead of only excluding them from the downmix rule. Off by default:
#: protecting commentary from being mistaken for the stereo track is the
#: reason this tool exists, so deleting it is an explicit choice.
DROP_COMMENTARY = _bool("DROP_COMMENTARY")

#: Channel layouts the downmix rule guarantees, comma-separated. A layout a
#: file misses is downmixed from the best surviving bigger track; a layout
#: with nothing bigger to make it from is skipped, nothing is ever upmixed.
#: Valid names are digit forms like 2.0, 5.1, 7.1; startup refuses others.
DOWNMIX_LAYOUTS = _set("DOWNMIX_LAYOUTS", "2.0,5.1")

AUDIO_CODEC = os.environ.get("AUDIO_CODEC", "aac")

#: Bitrate for a generated stereo track, set past transparency for ffmpeg's
#: native AAC encoder, the weakest of the mainstream ones. Bigger layouts
#: scale it by channel count, so the default 320k stereo becomes 960k for
#: a 5.1.
AUDIO_BITRATE = os.environ.get("AUDIO_BITRATE", "320k")

#: Rewrite MP4 and M4V files into Matroska, the one container that carries
#: the stream tags regeneration depends on. Off by default: MP4 direct-plays
#: on more devices, so converting a library can turn direct play into
#: server transcodes for older clients.
REMUX_TO_MKV = _bool("REMUX_TO_MKV")

#: What the downmix rule may rebuild, beyond creating missing layouts.
#: "generated" rebuilds this tool's own tracks when their recorded settings
#: no longer match config; "all" additionally replaces a real track reported
#: well below its layout's rate. Unset (the default) does neither: either
#: value queues rewrites across the library after a settings change.
#: Startup refuses anything else.
REGENERATE_DOWNMIXES = os.environ.get("REGENERATE_DOWNMIXES", "").strip().lower()

#: Containers we will rewrite. AVI and MPG are deliberately excluded: they
#: predate most of what these rules assume, and a stream copy into them with a
#: fresh AAC track is likelier to produce an unplayable file than to help.
ALLOWED_EXTS = _set("ALLOWED_EXTS", ".mkv,.mp4,.m4v")

#: Leave files with multiple hard links alone, i.e. still seeding in a
#: download client. Rewriting one is safe for the seed (it keeps the old
#: inode) but breaks the link, so the file occupies disk twice until the seed
#: is removed.
#:
#: On by default because hard-linking imports is what the standard *arr and
#: download-client layout does, so off means every import silently doubles
#: until the torrent goes — the library's whole seeding backlog, on the disk
#: it can least afford. Nothing is skipped for good: the file is parked and
#: re-stat'd (HARDLINK_RECHECK), and the sweep is the backstop.
SKIP_HARDLINKS = _bool("SKIP_HARDLINKS", "true")

#: How often (seconds) to re-stat webhook files parked by SKIP_HARDLINKS,
#: so they are processed minutes after seeding ends instead of at the next
#: sweep. 0 parks nothing and leaves skipped imports to the sweep alone.
#: Nothing reads this while SKIP_HARDLINKS is off, since nothing is parked.
HARDLINK_RECHECK = _int("HARDLINK_RECHECK", "900")

#: Matched against the track title when the muxer left the disposition flags
#: unset, which most rips do.
COMMENTARY_RE = _regex(
    "COMMENTARY_PATTERN",
    r"comment|descriptive|described\s*video|audio\s*description|"
    r"isolated\s*score|director'?s?\s*track|interview|behind\s*the\s*scenes",
)

#: Matched against a subtitle title to spot SDH (deaf and hard-of-hearing)
#: tracks when the muxer left the hearing_impaired disposition unset.
SDH_RE = _regex("SDH_PATTERN", r"\bsdh\b|\bcc\b|hearing[\s._-]*impaired")

#: Matched against a subtitle title to spot forced tracks when the muxer
#: left the forced disposition unset.
FORCED_RE = _regex("FORCED_PATTERN", r"\bforced\b")

#: Track and container titles that are release junk rather than information:
#: bitrates, resolutions, source tags, video codec names. Cleared during a
#: rewrite that happens anyway; never worth a rewrite on their own. The
#: default is deliberately conservative: a bare audio codec title ("AC3
#: 5.1") is left alone, extend the pattern to catch those too.
JUNK_TITLE_RE = _regex(
    "JUNK_TITLE_PATTERN",
    r"\d+\s*k?bps"
    r"|\bx?26[45]\b|\bhevc\b|\bavc\b"
    r"|\b(?:480|576|720|1080|2160)[pi]\b"
    r"|\b(?:blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|remux)\b",
)

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "0.0.0.0")
#: 5120 spells the two layouts the downmix rule guarantees, 5.1 and 2.0. The
#: point is that it is free: 8080 is qBittorrent's and SABnzbd's, so the one
#: port a media stack is guaranteed to have already spent is that one.
LISTEN_PORT = _int("LISTEN_PORT", "5120")

#: Advertised to Radarr and Sonarr when registering the webhook connection,
#: so it must be reachable from their containers. The default matches the
#: documented compose service name.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", f"http://trackstarr:{LISTEN_PORT}")

FFMPEG_TIMEOUT = _int("FFMPEG_TIMEOUT", "7200")
PROBE_TIMEOUT = _int("PROBE_TIMEOUT", "180")

#: Rewrites allowed to run at once, counted across the webhook workers, the
#: sweep, and any other process sharing STATE_DIR.
#:
#: One by default because the safe assumption is spinning disks, where
#: parallel rewrites fight over the heads. That assumption is often wrong: a
#: rewrite is a stream copy plus a few audio encodes, the encodes are
#: single-threaded, and on storage that isn't the bottleneck the whole job
#: is one busy core while the rest idle. The README covers how to measure
#: whether raising it pays.
MAX_CONCURRENT_REWRITES = _int("MAX_CONCURRENT_REWRITES", "1")

#: When the sweep runs, as a five-field cron schedule in local time
#: ("0 4 * * *" is 4am nightly); empty disables it. The sweep exists to
#: catch files that arrived without a webhook, so it reports by default and
#: only rewrites when SWEEP_APPLY is set.
SWEEP_AT = os.environ.get("SWEEP_AT", "").strip()
SWEEP_APPLY = _bool("SWEEP_APPLY")

#: Plan and report everywhere but rewrite nothing, overriding SWEEP_APPLY
#: and ``sweep --apply``. The *arrs are still queried for metadata and a
#: webhook import records a would-fix event instead of applying, so a new
#: install can watch a real week of traffic before being let loose.
DRY_RUN = _bool("DRY_RUN")


def errors() -> list[str]:
    """Startup-fatal configuration problems, as ready-to-log messages.

    Parse failures collected at import, plus the operational checks. The
    checks needing the rule and layout vocabulary live in
    :func:`trackstarr.policy.errors`, beside that vocabulary.
    """
    problems = list(_LOAD_ERRORS)
    if MAX_CONCURRENT_REWRITES < 1:
        problems.append(f"MAX_CONCURRENT_REWRITES={MAX_CONCURRENT_REWRITES} must be at least 1")
    if SWEEP_AT:
        try:
            # Left to the scheduler, garbage kills only its thread: serve
            # keeps running and sweeps never happen.
            cron.parse(SWEEP_AT)
        except ValueError as err:
            problems.append(f"SWEEP_AT={SWEEP_AT!r}: {err}")
    return problems
