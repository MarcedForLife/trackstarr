# Trackstarr

[![CI](https://github.com/MarcedForLife/trackstarr/actions/workflows/ci.yml/badge.svg)](https://github.com/MarcedForLife/trackstarr/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Image: ghcr.io](https://img.shields.io/badge/ghcr.io-trackstarr-blue.svg)](https://github.com/MarcedForLife/trackstarr/pkgs/container/trackstarr)

Trackstarr keeps the audio and subtitle tracks in a movie and TV library tidy,
driven by Radarr and Sonarr webhooks. It drops the languages you will never
play, downmixes a stereo or 5.1 track in the title's own language and any you
choose, where one is missing, at a bitrate you set, and puts what is left in a
sensible order.

## The rules

Every rule runs in one of three modes, set by its own `RULE_<NAME>` variable.
`always` acts whenever the file needs it. `never` switches the rule off.
`alongside` acts only on a file another rule is already rewriting, which is the
answer for a change worth having but not worth a 60GB rewrite of its own.

| Rule            | Unset       |                                                                                                                                                                                            |
| --------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `languages`     | `always`    | Keep audio and subtitles in `ALWAYS_KEEP_LANGS` (English by default), the title's original language, or untagged. Drop the rest. The original language comes from Radarr or Sonarr, so no TMDB key is needed |
| `commentary`    | `never`     | Drop commentary, described-audio and isolated-score tracks. They are never downmix sources either way                                                                                      |
| `sdh`           | `alongside` | Drop an SDH subtitle when the same language keeps a full one. Forced subtitles are always kept                                                                                             |
| `downmix`       | `always`    | Guarantee a non-commentary track for every layout in `DOWNMIX_LAYOUTS` (2.0 and 5.1), made from the best surviving bigger track of the same language. AAC at 320k and AC-3 at 640k out of the box |
| `regenerate`    | `never`     | Rebuild downmixes whose settings have moved on; see `REGENERATE_SCOPE`. Matroska only                                                                                                      |
| `cover_art`     | `always`    | Drop embedded artwork, which players read as a second video track                                                                                                                          |
| `junk_titles`   | `alongside` | Clear release junk from track and container titles                                                                                                                                        |
| `stray_streams` | `alongside` | Drop data and timecode streams nothing plays                                                                                                                                              |
| `order`         | `always`    | Video, then audio in `DOWNMIX_LAYOUTS` order, then subtitles, then attachments                                                                                                            |
| `remux`         | `never`     | Rewrite MP4 and M4V into Matroska, where every rule works. `alongside` converts a file something else is already rewriting                                                                 |

Every rule is idempotent, so a sweep is safe to run as often as you like. A rule
riding along never causes a rewrite, even indirectly: what the others would do
is decided first, with the ride-alongs held back. Trackstarr never transcodes
video, never upmixes, and never touches a file whose every audio track would
fail the language test.

## Quick start

```yaml
services:
  trackstarr:
    image: ghcr.io/marcedforlife/trackstarr:latest
    container_name: trackstarr
    restart: unless-stopped
    user: "${PUID:-1000}:${PGID:-1000}"
    ports:
      - "5120:5120"
    volumes:
      - /srv/media:/data
      - /srv/config/trackstarr:/config
    environment:
      MEDIA_DIRS: /data/media/movies:/data/media/tv
      TZ: Pacific/Auckland
```

1. Create the config directory as the container's user. The image never runs
   as root, so it cannot fix a root-owned volume for you:
   `install -d -o 1000 -g 1000 /srv/config/trackstarr`
2. Start the container and browse to port 5120. Sign in as `admin` with the
   password printed once in the log.
3. Add Radarr and Sonarr on the Connections page. Trackstarr registers its
   own webhooks, so nothing needs configuring in either.

`MEDIA_DIRS` is in the container's paths and has to match the mount above. A
wrong entry only warns, at startup and again as the sweep walks nothing, so
check the log after first start. Set `WEBHOOK_URL` if the *arrs reach the
container by a name other than `trackstarr`. Any setting can also go in the
environment, which pins it and greys out its field in the UI.

## How it runs

**Imports.** Radarr and Sonarr call the webhook as each file lands and it is
rewritten straight away. A file the download client still hard-links is parked
and re-checked every `HARDLINK_RECHECK` seconds, so it is done minutes after
seeding ends.

**Sweeps.** `SWEEP_AT` walks `MEDIA_DIRS` on a cron schedule. Verdicts are
cached, so after the first night an unchanged file costs a stat rather than an
ffprobe run. Changing a rule drops the cache.

**Rewrite mode** is a ladder. `report` rewrites nothing anywhere and records
what it would do, so a new install can watch a real week first. `imports`, the
default, rewrites what the *arrs deliver and leaves the sweep writing
`/config/pending.tsv`. `all` lets the sweep rewrite the existing library too.
Read `pending.tsv` before going to `all`.

**Safety.** A rewrite is staged in `WORK_DIR` and published over the original
only once its duration and stream count verify, so an interrupted job leaves
the library untouched. A file that changes mid-rewrite is deferred to the next
webhook or sweep. If an enabled *arr cannot be reached, an applying sweep
downgrades itself to report-only, since without original languages a foreign
film's own track would look like junk to drop.

**Media servers.** Configure Plex or Jellyfin (Emby speaks the same API) and
each rewrite nudges the server to refresh that file. The nudge names a path,
so when the server mounts the library elsewhere, map the difference with
`PLEX_PATH_MAP=/data/media=/srv/media`.

## Web UI

- **Overview.** The running sweep or import with per-file progress and time
  remaining, the queue behind it, and the controls: sweep now, stop after the
  current file, pause all processing (survives a restart), and stop
  everything.
- **Library.** Every title as a poster, filtered by verdict and sorted by
  name, size, last processed and more. Open a title to see its files and
  their plans, or select titles to plan or rewrite them now.
- **Events.** Every rewrite, failure, deferral, sweep and settings change,
  read back from `events.jsonl` with the settings it was judged under.
- **Settings.** General, Rules, Sweep and Connections edit every setting live,
  with a Test button per connection.

Accounts are admin or viewer. First start creates `admin` with a generated
password printed once in the log, or `ADMIN_PASSWORD` if set. `trackstarr
user` adds accounts, and `docker exec -it trackstarr trackstarr user passwd
admin` resets a forgotten password. Anonymous callers get `GET /health` and
nothing else.

Live updates ride a server-sent stream on `/api/stream`. A reverse proxy that
buffers responses needs buffering switched off for that path.

## Commands

```sh
trackstarr serve              # webhook listener plus the scheduled sweep
trackstarr sweep              # walk the library and report to /config/pending.tsv
trackstarr sweep --apply      # ... and rewrite what it finds
trackstarr fix FILE...        # plan and rewrite specific files now
trackstarr plan FILE...       # explain the decision, print the ffmpeg command
trackstarr secret NAME        # mint NAME's webhook secret and print it once
trackstarr user add NAME      # create a web UI account (--role admin|viewer)
trackstarr user passwd NAME   # reset a password and sign that account out
trackstarr user rm NAME       # delete an account; the last admin stays
trackstarr user list          # every account and its role
```

`plan` is the one to reach for when a file did something surprising. `plan`
and `fix` both take `--original LANG` for a file outside an *arr library.

```sh
$ trackstarr plan --original eng "Tears of Steel (2012).mkv"

Tears of Steel (2012).mkv
  original language : eng
  keeping languages : eng
  downmix layouts   : 2.0 (aac 320k), 5.1 (ac3 640k)
  - add 2.0 downmix from stream 3 (6ch eng)
  ffmpeg -i ... -map 0:0 -map 0:3 -map 0:1 -map 0:2 -map 0:3 ...
```

## Configuration

Every setting is an environment variable, and all but the service settings
marked below can also live in `/config/settings.json`, which is the file the
settings pages write. The environment wins where both name a setting, and a
key nothing reads refuses startup as the typo it usually is.

### Rules

| Variable                    | Default          |                                                                                                    |
| --------------------------- | ---------------- | -------------------------------------------------------------------------------------------------- |
| `ALWAYS_KEEP_LANGS`         | `eng`            | comma-separated; `en`, `eng` and `English` all work                                                |
| `KEEP_ORIGINAL_LANG`        | `true`           | also keep the title's own language                                                                 |
| `RULE_LANGUAGES` and so on  | see above        | one per rule: `never`, `alongside` or `always`                                                     |
| `ALLOWED_EXTS`              | `.mkv,.mp4,.m4v` | containers a rewrite may touch                                                                     |
| `COMMENTARY_PATTERN`        | see `config.py`  | regex; likewise `SDH_PATTERN`, `FORCED_PATTERN` and `JUNK_TITLE_PATTERN`                           |

### Downmix

| Variable                     | Default         |                                                                                                      |
| ---------------------------- | --------------- | ---------------------------------------------------------------------------------------------------- |
| `DOWNMIX_LAYOUTS`            | `2.0,5.1`       | layouts guaranteed to exist, in output order; each needs a codec and bitrate below                   |
| `AUDIO_CODEC_2_0` / `_5_1`   | `aac` / `ac3`   | one encoder per layout; `eac3`, `libopus`, `flac` or anything your ffmpeg carries                    |
| `AUDIO_BITRATE_2_0` / `_5_1` | `320k` / `640k` | one rate per layout                                                                                  |
| `DOWNMIX_ORIGINAL_LANG`      | `true`          | guarantee each layout in the title's own language                                                    |
| `DOWNMIX_LANGS`              | (unset)         | more languages guaranteed each layout                                                                |
| `REGENERATE_SCOPE`           | `generated`     | how far `RULE_REGENERATE` reaches: `generated` rebuilds this tool's own tracks when their settings change, `all` also replaces low-bitrate real tracks |
| `REGENERATE_BELOW_PERCENT`   | `50`            | how far under its layout's rate a track must report before `all` replaces it; 10 to 90               |

### Connections

| Variable                              | Default                  |                                                                            |
| ------------------------------------- | ------------------------ | -------------------------------------------------------------------------- |
| `RADARR_URL` / `RADARR_API_KEY`       | (unset)                  | omit to disable; likewise `SONARR_URL` / `SONARR_API_KEY`                  |
| `PLEX_URL` / `PLEX_TOKEN`             | (unset)                  | refresh after rewrites; likewise `JELLYFIN_URL` / `JELLYFIN_API_KEY`       |
| `PLEX_PATH_MAP` / `JELLYFIN_PATH_MAP` | (unset)                  | `LOCAL=REMOTE` pairs, comma-separated                                      |
| `RADARR_PUBLIC_URL` and so on         | (the address above)      | where a browser reaches each service, for the Open in buttons on a title   |
| `WEBHOOK_URL`                         | `http://trackstarr:5120` | how the *arrs reach the listener                                           |
| `SKIP_HARDLINKS`                      | `true`                   | park files the download client still links until it releases them          |
| `HARDLINK_RECHECK`                    | `900`                    | seconds between re-checks; 0 leaves parked files to the sweep              |

### Service

| Variable                           | Default                             |                                                                             |
| ---------------------------------- | ----------------------------------- | --------------------------------------------------------------------------- |
| `MEDIA_DIRS`                       | `/data/media/movies:/data/media/tv` | where the sweep walks, colon-separated, in the container's paths            |
| `REWRITE_MODE`                     | `imports`                           | `report`, `imports` or `all`                                                |
| `SWEEP_AT`                         | (unset)                             | cron schedule in local time (`0 4 * * *`); empty disables                   |
| `TZ`                               | (unset, so UTC)                     | IANA zone for the schedule, the log and every event stamp                   |
| `IMDB_RATINGS` *                   | `true`                              | fetch IMDb's public ratings dataset once a day for the score on a title; IMDb licenses it for personal, non-commercial use |
| `MAX_CONCURRENT_REWRITES`          | `1`                                 | shared across webhooks and sweeps; 1 suits spinning disks. If 3 is no faster, disk is the bottleneck |
| `PROBE_WORKERS`                    | `4`                                 | files a sweep probes at once                                                |
| `FFMPEG_TIMEOUT` / `PROBE_TIMEOUT` | `7200` / `180`                      | seconds                                                                     |
| `WORK_DIR` *                       | `/data/trackstarr-work`             | staging; a different filesystem from the library costs a copy per rewrite   |
| `STATE_DIR` *                      | `/config`                           | settings, cache, history, accounts, secrets and locks                       |
| `TRACKSTARR_KEY_FILE` *            | `$STATE_DIR/key`                    | seals saved credentials; see below                                          |
| `LISTEN_ADDR` / `LISTEN_PORT` *    | `0.0.0.0` / `5120`                  | bind address and port                                                       |
| `WEB_DIR` *                        | `/web` in the image                 | the built web UI; empty serves no pages                                     |
| `ADMIN_PASSWORD` *                 | (generated)                         | first run only; also takes `_FILE` / `FILE__`                               |
| `LOG_LEVEL` *                      | `INFO`                              | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`                           |

\* environment only.

### Credentials

Radarr, Sonarr, Plex and Jellyfin each need a key Trackstarr replays on every
call. Three ways to supply one, using Radarr's as the example:

| How                                             | Where the value lands           | In `docker inspect` | Editable in the UI |
| ----------------------------------------------- | ------------------------------- | ------------------- | ------------------ |
| Connections page                                | `settings.json`, sealed, `0600` | no                  | yes                |
| `RADARR_API_KEY`                                | the environment                 | yes                 | no, pinned         |
| `RADARR_API_KEY_FILE` or `FILE__RADARR_API_KEY` | the file you mount              | no                  | no, pinned         |

Naming the same credential both ways is refused at startup rather than
resolved by precedence, so a stale key cannot survive a rotation.

Keys saved through the page are sealed with a random key in `/config/key`,
minted by the first save. That means anyone holding the whole `/config`
directory holds both halves. To close that, mount a key of your own from
elsewhere and back it up:

```sh
openssl rand -hex 32 > trackstarr.key   # 0600
```

```yaml
environment:
  TRACKSTARR_KEY_FILE: /run/secrets/trackstarr_key
secrets:
  - trackstarr_key
```

A named key file is only ever read, never minted. Losing it does not stop
Trackstarr starting: each affected service reads as unset and says so in the
log, and re-entering the key on the Connections page seals it again.

Webhook secrets are kept only as SHA-256 digests in `/config/webhook-secrets`,
verified and re-provisioned at startup, so a wiped `/config` heals itself and
a copied one leaks nothing. Delete a digest to lock that caller out. Passwords
are scrypt hashes in `/config/users.json`.

## Development

```sh
uv sync                 # the dev group, at the versions CI uses
cp .env.example .env    # WORK_DIR and STATE_DIR under dev/, loaded at startup
uv run dev.py           # API and the SvelteKit UI on one port with hot reload
uv run pytest           # needs ffmpeg 8.1+ on PATH
uv run pytest --cov     # what CI measures; fails under the floor in pyproject
uv run ruff check && uv run ruff format
uv run mypy
```

The rules in `planner.py` are pure functions of ffprobe output and a `Policy`
snapshot, so `tests/test_planner.py` covers them with hand-built stream dicts
and no media at all. `command.py` turns a plan into an ffmpeg argument list and
`executor.py` runs it, which is what lets `plan` print the exact command
without touching a file. `tests/test_integration.py` generates real media
with ffmpeg.

`cryptography` is the only runtime dependency, and the bar for a second is
high. `uv.lock` is committed; after changing dependencies, run `uv lock` and
commit the result.

[web/README.md](web/README.md) covers the UI's own scripts and conventions, and
[tools/uireview](tools/uireview/README.md) the scripts for reviewing it at phone
metrics.

## Licence

MIT. Trackstarr is an independent project, not affiliated with or endorsed by
the Radarr, Sonarr, Plex, Jellyfin or Emby teams.
