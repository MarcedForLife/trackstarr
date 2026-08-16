"""Runtime configuration, all of it from the environment.

Values are read once at import. Other modules reference them as
``config.NAME`` rather than importing the names directly, so tests can
monkeypatch a single attribute without reloading anything.
"""

from __future__ import annotations

import os
import re


def _list(name: str, default: str) -> list[str]:
    return [part for part in os.environ.get(name, default).split(":") if part]


def _set(name: str, default: str) -> set[str]:
    parts = os.environ.get(name, default).split(",")
    return {part.strip().lower() for part in parts if part.strip()}


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


MEDIA_ROOTS = _list("MEDIA_ROOTS", "/data/media/movies:/data/media/tv")

#: Rewrites are staged here and renamed over the original. Must be on the same
#: filesystem as the media, or the rename stops being atomic and turns into a
#: copy that readers can observe half-finished.
WORK_DIR = os.environ.get("WORK_DIR", "/data/trackstarr-work")

STATE_DIR = os.environ.get("STATE_DIR", "/config")

RADARR_URL = os.environ.get("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
SONARR_URL = os.environ.get("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")

#: Media servers to nudge after a rewrite, so track lists and sizes stay
#: true even when the library is on a network mount their own filesystem
#: watchers can't see. Omit to disable. The Jellyfin settings fit Emby too.
PLEX_URL = os.environ.get("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")

#: Languages kept regardless of the title's original language.
ALWAYS_KEEP = _set("ALWAYS_KEEP_LANGS", "eng")

#: Rules to switch off, for setups where another service owns part of the
#: job. Valid names are the keys of planner.RULES; startup refuses others.
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
#: "generated" rebuilds tracks this tool made (recognised by the tag written
#: at encode time) whose recorded codec or bitrate no longer match config.
#: "all" additionally replaces any real track for a configured layout whose
#: reported bitrate sits well below the layout's own rate. Unset does
#: neither, the default: either value queues rewrites across the library
#: after a settings change. Startup refuses anything else.
REGENERATE_DOWNMIXES = os.environ.get("REGENERATE_DOWNMIXES", "").strip().lower()

#: Containers we will rewrite. AVI and MPG are deliberately excluded: they
#: predate most of what these rules assume, and a stream copy into them with a
#: fresh AAC track is likelier to produce an unplayable file than to help.
ALLOWED_EXTS = _set("ALLOWED_EXTS", ".mkv,.mp4,.m4v")

#: Leave files with multiple hard links alone, i.e. still seeding in a
#: download client. Rewriting one is safe for the seed (it keeps the old
#: inode) but breaks the link, so the file occupies disk twice. The sweep
#: picks the file up once the download client lets go.
SKIP_HARDLINKS = _bool("SKIP_HARDLINKS")

#: How often (seconds) to re-stat webhook files parked by SKIP_HARDLINKS,
#: so they are processed minutes after seeding ends instead of at the next
#: sweep. 0 parks nothing and leaves skipped imports to the sweep alone.
HARDLINK_RECHECK = int(os.environ.get("HARDLINK_RECHECK", "900"))

IMAGE_CODECS = {"mjpeg", "png", "gif", "bmp", "webp", "tiff"}

#: Matched against the track title when the muxer left the disposition flags
#: unset, which most rips do.
COMMENTARY_RE = re.compile(
    os.environ.get(
        "COMMENTARY_PATTERN",
        r"comment|descriptive|described\s*video|audio\s*description|"
        r"isolated\s*score|director'?s?\s*track|interview|behind\s*the\s*scenes",
    ),
    re.IGNORECASE,
)

#: Matched against a subtitle title to spot SDH (deaf and hard-of-hearing)
#: tracks when the muxer left the hearing_impaired disposition unset.
SDH_RE = re.compile(
    os.environ.get("SDH_PATTERN", r"\bsdh\b|\bcc\b|hearing[\s._-]*impaired"),
    re.IGNORECASE,
)

#: Matched against a subtitle title to spot forced tracks when the muxer
#: left the forced disposition unset.
FORCED_RE = re.compile(os.environ.get("FORCED_PATTERN", r"\bforced\b"), re.IGNORECASE)

#: Track and container titles that are release junk rather than information:
#: bitrates, resolutions, source tags, video codec names. Cleared during a
#: rewrite that happens anyway; never worth a rewrite on their own. The
#: default is deliberately conservative: a bare audio codec title ("AC3
#: 5.1") is left alone, extend the pattern to catch those too.
JUNK_TITLE_RE = re.compile(
    os.environ.get(
        "JUNK_TITLE_PATTERN",
        r"\d+\s*k?bps"
        r"|\bx?26[45]\b|\bhevc\b|\bavc\b"
        r"|\b(?:480|576|720|1080|2160)[pi]\b"
        r"|\b(?:blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|remux)\b",
    ),
    re.IGNORECASE,
)

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))

#: Advertised to Radarr and Sonarr when registering the webhook connection,
#: so it must be reachable from their containers. The default matches the
#: documented compose service name.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", f"http://trackstarr:{LISTEN_PORT}")

FFMPEG_TIMEOUT = int(os.environ.get("FFMPEG_TIMEOUT", "7200"))
PROBE_TIMEOUT = int(os.environ.get("PROBE_TIMEOUT", "180"))

#: Nightly sweep as HH:MM local time; empty disables it. The sweep exists to
#: catch files that arrived without a webhook, so it reports by default and
#: only rewrites when SWEEP_APPLY is set.
SWEEP_AT = os.environ.get("SWEEP_AT", "").strip()
SWEEP_APPLY = _bool("SWEEP_APPLY")
