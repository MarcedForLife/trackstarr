# Trackstarr

[![CI](https://github.com/MarcedForLife/trackstarr/actions/workflows/ci.yml/badge.svg)](https://github.com/MarcedForLife/trackstarr/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Image: ghcr.io](https://img.shields.io/badge/ghcr.io-trackstarr-blue.svg)](https://github.com/MarcedForLife/trackstarr/pkgs/container/trackstarr)
[![Demo](https://img.shields.io/badge/demo-live-brightgreen.svg)](https://marcedforlife.github.io/trackstarr/)

Trackstarr tidies audio and subtitle tracks in your movie and TV library.
It processes Radarr and Sonarr imports, removes unwanted tracks, creates missing
audio downmixes and puts the remaining tracks in your preferred order.
Video is always copied without re-encoding.

- **Audio and language rules:** keep original-language audio and selected dubs,
  add stereo or surround mixes, choose codecs and bitrates, and remove unwanted layouts.
- **Track cleanup:** remove commentary, redundant SDH subtitles, embedded artwork
  and release tags; optionally convert MP4/M4V to MKV.
- **Controlled processing:** preview plans, schedule library sweeps, hold individual
  titles, pause processing and defer hard-linked files.
- **Live web UI:** browse and search your library, inspect planned changes, edit MKV
  track tags, follow progress and review event history.
- **Media server integration:** refresh Plex, Jellyfin or Emby after a rewrite.

**[Try the demo](https://marcedforlife.github.io/trackstarr/)** — a sample library
with no backend. Any username and password work.

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
      MEDIA_DIRS: /data/movies:/data/tv
      TZ: Pacific/Auckland
```

1. Create `/srv/config/trackstarr` with ownership matching the container user,
   for example `sudo install -d -o 1000 -g 1000 /srv/config/trackstarr`.
   That user also needs write access to the media and staging directory
   (`/data/trackstarr-work` by default).
2. Save the example as `compose.yaml`, adjust the paths and timezone, and run
   `docker compose up -d`.
3. Open port 5120 in your browser. Sign in as `admin` using the password printed
   in the container log, then change it when prompted.
4. Add Radarr and Sonarr under **Settings → Connections**. Trackstarr registers
   their webhooks automatically.

`MEDIA_DIRS` and paths received from the *arrs must refer to files inside the
container. The example maps `/srv/media/movies` to `/data/movies`;
change the mount or `MEDIA_DIRS` to match your library. Missing sweep directories
produce warnings, so check the startup log. Set `WEBHOOK_URL` if the *arrs cannot
reach `http://trackstarr:5120`.

The default `REWRITE_MODE=imports` rewrites new imports but only reports changes
for library sweeps. Use `report` to preview all processing first; use `all` to
allow scheduled sweeps to rewrite the existing library. Review
`/config/pending.tsv` before enabling `all`.

## Rules

Each `RULE_<NAME>` accepts `always`, `alongside` or `never`:

- `always`: apply whenever needed.
- `alongside`: apply only when another rule already requires a rewrite.
- `never`: disable the rule.

| Rule            | Default     | Action                                                                                                                        |
| --------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `languages`     | `always`    | Remove audio and subtitles in languages absent from `LANGUAGES`. Untagged tracks stay.                                        |
| `tag_original`  | `never`     | Tag an untagged audio track with the title's original language, where nothing in the file contradicts it. Usually no rewrite. |
| `commentary`    | `never`     | Remove commentary, described audio and isolated scores. These are never downmix sources.                                      |
| `sdh`           | `alongside` | Remove an SDH subtitle if a full subtitle survives in the same language. This rule preserves forced subtitles.                |
| `regenerate`    | `never`     | Rebuild outdated downmixes or replace tracks under the configured bitrate rules. MKV only.                                    |
| `cover_art`     | `always`    | Remove embedded artwork.                                                                                                      |
| `release_tags`  | `alongside` | Clear release tags from track and container titles.                                                                           |
| `stray_streams` | `alongside` | Remove data and timecode streams.                                                                                             |
| `order`         | `always`    | Order video, audio by `AUDIO_LAYOUTS` (then other sizes by channel count), subtitles and attachments.                         |
| `remux`         | `never`     | Convert MP4/M4V to MKV, converting text subtitles to SRT. Video is copied.                                                    |

Rules are idempotent: an unchanged file does not need another rewrite.
`alongside` rules cannot trigger a rewrite indirectly. Trackstarr never upmixes
or applies a language filter that would remove every audio track. Forced
subtitles are protected from SDH cleanup, but still follow the language filter.

### Audio layouts

`AUDIO_LAYOUTS` controls which audio sizes to create, retain or remove, and their
output order. The default `2.0,5.1` creates missing stereo AAC at 320k and 5.1 AC3
at 640k.

```yaml
AUDIO_LAYOUTS: 2.0:libopus:192k,5.1:eac3:448k,7.1:remove
```

| Entry           | Meaning                                                             |
| --------------- | ------------------------------------------------------------------- |
| `5.1`           | Create a missing mix using the defaults for this layout.            |
| `5.1:eac3:448k` | Create a missing mix with this encoder and bitrate.                 |
| `7.1:keep`      | Keep this size without creating it; include it in the output order. |
| `7.1:remove`    | Remove this size after creating any replacement downmixes.          |

Bare-layout defaults are `1.0:aac:160k`, `2.0:aac:320k`, `5.1:ac3:640k`,
`6.1:aac:704k` and `7.1:aac:768k`. Other sizes need an explicit encoder and rate.
Each channel count may appear only once, including equivalent counts such as
`4.2` and `5.1`.

A missing mix uses the best eligible larger track in the same language. Without
a larger source, it cannot be created. Downmix and removal entries need no
separate `RULE_` switch: either can trigger a rewrite. Removal can use the outgoing
track as a downmix source, but never removes the last audio track. Removed tracks
are not backed up and cannot be used for future regeneration.

### Languages

`LANGUAGES` controls which languages survive and which receive downmixes.
The default is `original,eng`.

```yaml
LANGUAGES: original,eng,fre:keep
```

| Entry      | Meaning                                                                             |
| ---------- | ----------------------------------------------------------------------------------- |
| `eng`      | Keep English and create the requested downmix layouts where sources exist.          |
| `fre:keep` | Keep French without creating mixes.                                                 |
| `original` | Use the title's original language reported by Radarr or Sonarr; no TMDB key needed. |

Explicit language entries take precedence over `original`. If the *arrs cannot
identify the original language, that entry contributes no language. Names
normalise: `en`, `eng` and `English` are equivalent.

`RULE_TAG_ORIGINAL` writes the original language onto an untagged audio track,
but only where the file has one untagged track, nothing in that language already,
and that track is neither commentary nor titled as another language. A code
`LANGUAGES` would then drop is never written.

Where the tag is a file's only change it goes into the MKV header with
mkvpropedit, so nothing is re-encoded and no `alongside` rule rides along. MP4, a
hardlink the download client still holds, and an image without mkvtoolnix fall
back to a rewrite, which writes the same tag.

Unlisted languages follow `RULE_LANGUAGES`; untagged tracks always stay. If none
of the requested downmix languages can fill a layout, Trackstarr can use another
surviving language, preferring `LANGUAGES` order. A generated fallback is removed
once a requested language can supply that layout.

### Regeneration

Enable `RULE_REGENERATE` to update existing mixes in MKV files:

- `REGENERATE_SCOPE=generated` rebuilds Trackstarr's own tracks when their codec
  or bitrate settings change.
- `REGENERATE_SCOPE=all` also replaces tracks below `REGENERATE_BELOW_PERCENT`
  of the target bitrate, when a larger surviving source can improve them.
  The default `80` means below 80% of the target, not 80% below it.
- `REGENERATE_ABOVE_PERCENT` re-encodes oversized lossy tracks from themselves.
  `0` disables this; `150` means above 150% of the target bitrate. Lossless
  tracks are excluded. This option trades quality for space.

## Processing and safety

**Imports and sweeps.** Webhooks queue imported files. `SWEEP_AT` schedules a walk
of `MEDIA_DIRS` using a five-field cron expression in the configured timezone.
Unchanged files use cached verdicts; policy or version changes invalidate them.
Sweeps write actionable results to `/config/pending.tsv`.

**Hard links.** With `SKIP_HARDLINKS=true`, files with multiple hard links are
deferred. They are rechecked every `HARDLINK_RECHECK` seconds (900 by default).
Processing resumes once the extra links are removed; stopping seeding alone does
not remove a hard link. Set the interval to `0` to leave retries to sweeps.

**Holds and pause.** Hold a title for a set time or indefinitely. Held files are
still planned and reported, but rewrites wait. Holds and the global processing
pause survive restarts. The UI also supports stopping after the current file,
stopping all work and skipping an individual file.

**Verified replacement.** Rewrites are staged in `WORK_DIR`. Trackstarr checks
duration and stream count before replacing the original and defers files that
change during processing. Staging needs space for the output; a different
filesystem adds a copy. Track removal is permanent; Trackstarr does not keep a
backup of the original.

**Connection failures.** If a configured *arr library cannot be listed and the
policy uses `original`, an applying sweep falls back to report-only to avoid
removing audio based on incomplete language information.

**Media servers.** Configure Plex or Jellyfin (also used for Emby) to refresh
rewritten files. If the server uses different paths, set a mapping such as
`PLEX_PATH_MAP=/data/media=/srv/media`.

## Web UI

- **Overview:** live progress, estimated time remaining, queued work and processing controls.
- **Library:** search movies and series, filter verdicts, sort titles and inspect
  per-file plans. Missing downloads and unsupported containers have distinct
  verdicts, and an Untagged filter finds the titles whose audio carries no
  language tag. Admins can hold titles or select them for immediate planning or
  rewriting.
- **Track editing:** admins can edit MKV track languages and commentary, forced
  and SDH flags, including matching tracks across episodes. `mkvpropedit` updates
  headers in place, then Trackstarr reassesses the file.
- **Events:** rewrite, failure, deferral, sweep and settings history from
  `events.jsonl`, with the policy used for each decision.
- **Settings:** edit runtime settings and test connections. Non-empty environment
  overrides pin the corresponding fields.
- **Appearance:** light, dark or system theme, six colour palettes or one from a
  hue of your own, poster effects,
  cover-art visibility and default library filters and ordering, saved per browser.
- **Account:** change your password. Accounts have admin or viewer roles;
  administrators manage accounts through the CLI.

First start creates `admin` using `ADMIN_PASSWORD`, or a generated password logged
once. To reset it, run `docker exec -it trackstarr trackstarr user passwd admin`.
The UI and its data API require sign-in; `/health` is public and webhooks use
separate secrets. Reverse proxies must allow streaming on `/api/stream` without
response buffering for live updates.

## Commands

```sh
trackstarr serve              # web UI, API, webhooks and scheduled sweeps
trackstarr sweep              # report library changes in /config/pending.tsv
trackstarr sweep --apply      # apply library changes, unless mode is report
trackstarr fix FILE...        # plan and rewrite selected files
trackstarr plan FILE...       # explain changes and print the ffmpeg command
trackstarr secret NAME        # create a caller's webhook secret
trackstarr user add NAME      # create an account (--role admin|viewer)
trackstarr user passwd NAME   # reset a password and invalidate sessions
trackstarr user rm NAME       # delete an account; preserves the last admin
trackstarr user list          # list accounts and roles
```

`plan` and `fix` accept `--original LANG` for files outside an *arr library:

```sh
trackstarr plan --original eng "Tears of Steel (2012).mkv"
```

`REWRITE_MODE=report` prevents rewrites even with `fix` or `sweep --apply`.
Title holds also apply to these commands.

## Configuration reference

Runtime settings can be saved in `/config/settings.json` through the UI.
Non-empty environment variables take precedence. Unknown settings-file keys
produce startup warnings. The service settings in the last table are configured
through the environment and require a restart.

### Rules and audio

| Variable                                                                     | Default                                   | Purpose                                                   |
| ---------------------------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------------- |
| `LANGUAGES`                                                                  | `original,eng`                            | Languages to keep and downmix; see above.                 |
| `AUDIO_LAYOUTS`                                                              | `2.0,5.1`                                 | Layout actions, encoders, bitrates and order.             |
| `RULE_<NAME>`                                                                | See Rules                                 | `never`, `alongside` or `always`.                         |
| `ALLOWED_EXTS`                                                               | `.mkv,.mp4,.m4v`                          | Containers eligible for rewriting.                        |
| `REGENERATE_SCOPE`                                                           | `generated`                               | `generated` or `all`.                                     |
| `REGENERATE_BELOW_PERCENT`                                                   | `80`                                      | Low-bitrate threshold as a percentage of target; 10–90.   |
| `REGENERATE_ABOVE_PERCENT`                                                   | `0`                                       | Oversized-track threshold; 0 disables, otherwise 110–400. |
| `COMMENTARY_PATTERN`, `SDH_PATTERN`, `FORCED_PATTERN`, `RELEASE_TAG_PATTERN` | See [config.py](src/trackstarr/config.py) | Track-title detection regexes.                            |

### Connections

| Variable                                                                           | Default                  | Purpose                                                                   |
| ---------------------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------- |
| `RADARR_URL` / `RADARR_API_KEY`                                                    | Unset                    | Radarr connection; both required to enable.                               |
| `SONARR_URL` / `SONARR_API_KEY`                                                    | Unset                    | Sonarr connection; both required to enable.                               |
| `PLEX_URL` / `PLEX_TOKEN`                                                          | Unset                    | Plex refreshes and title links.                                           |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY`                                                | Unset                    | Jellyfin or Emby refreshes and title links.                               |
| `PLEX_PATH_MAP` / `JELLYFIN_PATH_MAP`                                              | Unset                    | Comma-separated `LOCAL=REMOTE` path prefixes.                             |
| `RADARR_PUBLIC_URL`, `SONARR_PUBLIC_URL`, `PLEX_PUBLIC_URL`, `JELLYFIN_PUBLIC_URL` | Service URL              | Browser-accessible addresses for title links.                             |
| `WEBHOOK_URL`                                                                      | `http://trackstarr:5120` | Address the *arrs use to reach Trackstarr; default follows `LISTEN_PORT`. |

### Processing

| Variable                           | Default                             | Purpose                                                                                   |
| ---------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------- |
| `MEDIA_DIRS`                       | `/data/media/movies:/data/media/tv` | Colon-separated sweep roots inside the container.                                         |
| `REWRITE_MODE`                     | `imports`                           | `report`, `imports` or `all`.                                                             |
| `SWEEP_AT`                         | Unset                               | Local-time cron schedule, e.g. `0 4 * * *`; empty disables.                               |
| `TZ`                               | Unset                               | IANA timezone; container defaults to UTC.                                                 |
| `SKIP_HARDLINKS`                   | `true`                              | Defer files with multiple hard links.                                                     |
| `HARDLINK_RECHECK`                 | `900`                               | Retry interval in seconds; 0 leaves retries to sweeps.                                    |
| `IMDB_RATINGS`                     | `true`                              | Fetch IMDb ratings daily for title scores; dataset is for personal, non-commercial use.   |
| `MAX_CONCURRENT_REWRITES`          | `1`                                 | Shared rewrite limit across imports, sweeps and processes using the same state directory. |
| `PROBE_WORKERS`                    | `4`                                 | Concurrent sweep probes.                                                                  |
| `FFMPEG_TIMEOUT` / `PROBE_TIMEOUT` | `7200` / `180`                      | Timeouts in seconds.                                                                      |

### Service environment

| Variable                      | Default                 | Purpose                                                                                 |
| ----------------------------- | ----------------------- | --------------------------------------------------------------------------------------- |
| `WORK_DIR`                    | `/data/trackstarr-work` | Rewrite staging directory.                                                              |
| `STATE_DIR`                   | `/config`               | Settings, cache, events, accounts, holds, secrets and locks.                            |
| `TRACKSTARR_KEY_FILE`         | `$STATE_DIR/key`        | Encryption key for saved credentials.                                                   |
| `LISTEN_ADDR` / `LISTEN_PORT` | `0.0.0.0` / `5120`      | Listener address and port.                                                              |
| `WEB_DIR`                     | `/web` in the image     | Static UI directory; empty disables pages.                                              |
| `ADMIN_PASSWORD`              | Generated               | Initial admin password, ignored once the user store exists. Supports secret-file forms. |
| `LOG_LEVEL`                   | `INFO`                  | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`.                                      |

### Credentials

Supply connection credentials in one of three ways:

| Method                                          | Storage                                       | Editable in UI |
| ----------------------------------------------- | --------------------------------------------- | -------------- |
| Connections page                                | Encrypted in `settings.json` with mode `0600` | Yes            |
| `RADARR_API_KEY` (or equivalent)                | Environment; visible in `docker inspect`      | No             |
| `RADARR_API_KEY_FILE` or `FILE__RADARR_API_KEY` | Mounted secret file                           | No             |

Using multiple environment forms for the same credential is a startup error.
Environment credentials override saved values.

By default, Trackstarr creates `/config/key` when first saving a credential.
Anyone with the entire config directory therefore has both the encrypted values
and their key. To keep the key separately, generate one and mount it at the path
set by `TRACKSTARR_KEY_FILE`:

```sh
(umask 077; openssl rand -hex 32 > trackstarr.key)
```

Ensure the container user can read the mounted key, and back it up. An explicitly
configured key file is never generated automatically. If it is missing or invalid,
the service starts with affected connections disabled. Restore the key, or provide
a valid replacement and re-enter the connection credentials.

Webhook secrets are stored as SHA-256 digests in `/config/webhook-secrets` and
*arr webhooks are verified and provisioned at startup. Account passwords are
stored as scrypt hashes in `/config/users.json`.

## Development

Use Python 3.14+, uv, Node.js/npm (CI uses Node 26), ffmpeg/ffprobe 8.1+ and
mkvtoolnix.

```sh
uv sync --locked
cp .env.example .env          # local state and staging under dev/
uv run dev.py                # starts API and UI with hot reload
uv run pytest
uv run pytest --cov          # enforces 100% coverage
uv run ruff check
uv run ruff format --check
uv run mypy
```

`dev.py` installs missing web dependencies on first run. Pure planner tests use
stream dictionaries; integration tests generate real media with ffmpeg. Tag-editing
tests skip if `mkvpropedit` is unavailable. `cryptography` is the only Python runtime
dependency. After dependency changes, update and commit `uv.lock`.

See [web development](web/README.md), [browser probes](web/probes/README.md) and
[UI review tools](tools/uireview/README.md) for frontend commands and conventions.

## Licence

MIT. Trackstarr is independent of Radarr, Sonarr, Plex, Jellyfin, Emby and IMDb.

The container image ships ffmpeg (GPL) and
[MKVToolNix](https://mkvtoolnix.download) (GPL), run as separate programs.
Service logos come from
[Dashboard Icons](https://github.com/homarr-labs/dashboard-icons) (Apache 2.0)
and [Simple Icons](https://simpleicons.org) (CC0). Each mark stays its owner's
trademark.
