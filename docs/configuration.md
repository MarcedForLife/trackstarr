# Configuration reference

[Back to README](../README.md) · [Rules and audio](rules.md)

Save settings in the UI. They are stored in `/config/settings.json`. Non-empty
environment variables take precedence. [Service settings](#service-environment)
use environment variables and require a restart.

## Rules and audio

| Variable                                                                     | Default                                      | Purpose                                                         |
| ---------------------------------------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------- |
| `LANGUAGES`                                                                  | `original,eng`                               | Languages to keep and downmix. See [rules](rules.md#languages). |
| `AUDIO_LAYOUTS`                                                              | `2.0,5.1`                                    | Layout actions, encoders, bitrates and order.                   |
| `RULE_DV_STRIP` | `never` | Remove Dolby Vision from HEVC profile 8.1. See [video compatibility](rules.md#video-compatibility). |
| `RULE_<NAME>`                                                                | [Rules](rules.md)                            | `never`, `alongside` or `always`.                               |
| `ALLOWED_EXTS`                                                               | `.mkv,.mp4,.m4v`                             | Containers eligible for rewriting.                              |
| `REGENERATE_SCOPE`                                                           | `generated`                                  | `generated` or `all`.                                           |
| `REGENERATE_BELOW_PERCENT`                                                   | `80`                                         | Low-bitrate threshold, 10–90% of target.                        |
| `REGENERATE_ABOVE_PERCENT`                                                   | `0`                                          | Oversized-track threshold, 110–400% of target. 0 disables.      |
| `COMMENTARY_PATTERN`, `SDH_PATTERN`, `FORCED_PATTERN`, `RELEASE_TAG_PATTERN` | See [config.py](../src/trackstarr/config.py) | Track-title detection regexes.                                  |

## Multiple arr instances and variants

Add Radarr or Sonarr servers under **Settings → Connections → New connection**.
For environment configuration, use `RADARR_<ID>_*` or `SONARR_<ID>_*`:

```env
RADARR_4K_URL=http://radarr4k:7878
RADARR_4K_API_KEY=your-key
RADARR_4K_PUBLIC_URL=https://radarr4k.example.com
RADARR_4K_NAME=UHD shelf
```

IDs use letters and digits (`PUBLIC` is reserved). Names and public URLs are
optional. Use `RADARR_NAME` and `SONARR_NAME` for the default instances.
A blank name displays the service type and any readable ID suffix (for example,
“Radarr 4k”). Generated IDs display just the service type; the fallback is never
saved as the name. Credentials also support [secret files](#credentials).

The same movie or series across instances appears as one title, with a variant
selector for each movie or episode. Title actions apply to all variants. Use a
file's menu to plan, process or pause it individually. The Library flags folders
shared by multiple connections. Variants are processed independently.

## Connections

| Variable                                                                           | Default                  | Purpose                                                       |
| ---------------------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------- |
| `RADARR_URL` / `RADARR_API_KEY`                                                    | Unset                    | Radarr connection. Both values are required.                  |
| `SONARR_URL` / `SONARR_API_KEY`                                                    | Unset                    | Sonarr connection. Both values are required.                  |
| `PLEX_URL` / `PLEX_TOKEN`                                                          | Unset                    | Plex refreshes and title links.                               |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY`                                                | Unset                    | Jellyfin or Emby refreshes and title links.                   |
| `PLEX_PATH_MAP` / `JELLYFIN_PATH_MAP`                                              | Unset                    | Comma-separated `LOCAL=REMOTE` path prefixes.                 |
| `RADARR_PUBLIC_URL`, `SONARR_PUBLIC_URL`, `PLEX_PUBLIC_URL`, `JELLYFIN_PUBLIC_URL` | Service URL              | Addresses to use for title links.                             |
| `WEBHOOK_URL`                                                                      | `http://trackstarr:5120` | Webhook callback address. Default port follows `LISTEN_PORT`. |

## Processing

| Variable                           | Default                             | Purpose                                                        |
| ---------------------------------- | ----------------------------------- | -------------------------------------------------------------- |
| `MEDIA_DIRS`                       | `/data/media/movies:/data/media/tv` | Colon-separated sweep roots inside the container.              |
| `REWRITE_MODE`                     | `imports`                           | `report`, `imports` or `all`.                                  |
| `SWEEP_AT`                         | Unset                               | Cron schedule in `TZ`, e.g. `0 4 * * *`. Empty disables.       |
| `TZ`                               | Unset                               | IANA timezone. Defaults to UTC in the container.               |
| `SKIP_HARDLINKS`                   | `true`                              | Defer files with multiple hard links.                          |
| `HARDLINK_RECHECK`                 | `900`                               | Retry interval in seconds. 0 leaves retries to sweeps.         |
| `IMDB_RATINGS`                     | `true`                              | Daily IMDb ratings. For personal, non-commercial use.          |
| `MAX_CONCURRENT_REWRITES`          | `1`                                 | Maximum rewrites at once across processes sharing `STATE_DIR`. |
| `PROBE_WORKERS`                    | `4`                                 | Concurrent import and sweep probes.                            |
| `FFMPEG_TIMEOUT` / `PROBE_TIMEOUT` | `7200` / `180`                      | Timeouts in seconds.                                           |

## Service environment

| Variable                      | Default                 | Purpose                                                       |
| ----------------------------- | ----------------------- | ------------------------------------------------------------- |
| `WORK_DIR`                    | `/data/trackstarr-work` | Rewrite staging directory.                                    |
| `STATE_DIR`                   | `/config`               | Settings, cache, events, accounts, pauses, secrets and locks. |
| `TRACKSTARR_KEY_FILE`         | `$STATE_DIR/key`        | Encryption key for saved credentials.                         |
| `LISTEN_ADDR` / `LISTEN_PORT` | `0.0.0.0` / `5120`      | Listener address and port.                                    |
| `WEB_DIR`                     | `/web` in the image     | Static UI directory. Empty disables pages.                    |
| `ADMIN_PASSWORD`              | Generated               | Initial admin password. Supports secret files.                |
| `LOG_LEVEL`                   | `INFO`                  | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`.            |

## Credentials

Supply connection credentials in one of three ways:

| Method                                          | Storage                                  | Editable in UI |
| ----------------------------------------------- | ---------------------------------------- | -------------- |
| Connections page                                | Encrypted in `settings.json`             | Yes            |
| `RADARR_API_KEY` (or equivalent)                | Environment, visible in `docker inspect` | No             |
| `RADARR_API_KEY_FILE` or `FILE__RADARR_API_KEY` | Mounted secret file                      | No             |

Use one environment form per credential. Environment credentials override
saved values.

Trackstarr saves the encryption key at `/config/key` by default. To store it
separately, generate a key and mount it at `TRACKSTARR_KEY_FILE`:

```sh
(umask 077; openssl rand -hex 32 > trackstarr.key)
```

Back up the key and make it readable by the container user. If a configured key
is missing or invalid, affected connections are disabled. Restore it, or replace
it and re-enter the credentials.

## Accounts and reverse proxies

Viewers have read-only access. Admins can change settings and process files.
Manage accounts through the CLI.

On first start, Trackstarr creates `admin` using `ADMIN_PASSWORD` or prints a
generated password in the log. To reset it, run:

```sh
docker exec -it trackstarr trackstarr user passwd admin
```

The UI and API require sign-in. `/health` is public, and webhooks use separate
secrets. Disable reverse proxy buffering on `/api/stream` for live updates.
