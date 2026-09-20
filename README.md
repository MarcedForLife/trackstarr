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
  and release tags, and optionally convert MP4/M4V to MKV.
- **Controlled processing:** preview plans, schedule library sweeps, pause individual
  titles or files, pause processing and defer hard-linked files.
- **Live web UI:** search and filter your library, inspect plans, edit MKV track
  tags, follow progress and review history. Admins control processing and settings.
  Viewers have read-only access.
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

Mount media at the same paths used by Radarr and Sonarr, and include those roots
in `MEDIA_DIRS`. Set `WEBHOOK_URL` if they cannot reach `http://trackstarr:5120`.
Use **Test** on each connection to check its API, webhook callback and visible
library roots.

The default `REWRITE_MODE=imports` rewrites new imports but only reports changes
for library sweeps. Use `report` to preview changes or `all` to let sweeps rewrite the library. Review
`/config/pending.tsv` before enabling `all`.

## Rules

Configure rules in **Settings** or through environment variables. Each
`RULE_<NAME>` accepts `always`, `alongside` (only during another required
rewrite), or `never`.

By default, Trackstarr keeps original-language and English audio and subtitles,
creates missing stereo AAC and 5.1 AC3 mixes where a larger source exists,
removes embedded artwork and orders tracks. Untagged tracks stay. Trackstarr never upmixes or removes every audio track.

For example, keep French without creating French downmixes, use Opus for stereo,
and retain existing 7.1 tracks:

```yaml
LANGUAGES: original,eng,fre:keep
AUDIO_LAYOUTS: 2.0:libopus:192k,5.1:ac3:640k,7.1:keep
```

Original-language tagging and regeneration of existing mixes are opt-in.
See [rules and audio](docs/rules.md) for all defaults, layout syntax,
language-tagging conditions and regeneration thresholds.

## Processing and safety

**Imports and sweeps.** Webhooks queue imported files. `SWEEP_AT` schedules a walk
of `MEDIA_DIRS` using a cron expression in the configured timezone.
Sweeps write actionable results to `/config/pending.tsv`. Imports take priority
over waiting sweep work unless the queue has been manually reordered.
Running rewrites finish first.

**Hard links.** With `SKIP_HARDLINKS=true`, files with any hard links are
deferred. They are rechecked every `HARDLINK_RECHECK` seconds (900 by default).
Processing resumes once the links are removed.

**Pausing.** Hold a title for a set time or indefinitely. Paused files are
still planned, but won't be processed.

**Verified replacement.** Rewrites are staged in `WORK_DIR`. Trackstarr checks
duration and stream count before replacing the original and defers files that
change during processing. Staging needs space for the output. Track removal
is permanent.

**Connection failures.** If a configured *arr library cannot be listed and the
policy uses `original`, an applying sweep falls back to report-only to avoid
removing audio based on incomplete language information.

**Media servers.** Configure Plex or Jellyfin (also used for Emby) to refresh
rewritten files. If the server uses different paths, set a mapping such as
`PLEX_PATH_MAP=/data/media=/srv/media`.

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
trackstarr user rm NAME       # delete an account (except the last admin)
trackstarr user list          # list accounts and roles
```

`plan` and `fix` accept `--original LANG` for files outside an *arr library:

```sh
trackstarr plan --original eng "Tears of Steel (2012).mkv"
```

`REWRITE_MODE=report` prevents rewrites even with `fix` or `sweep --apply`.
Title holds also apply to these commands.

## Configuration

Save settings in the UI. Non-empty environment variables take precedence.
See the [configuration reference](docs/configuration.md) for all settings,
[multiple arr instances](docs/configuration.md#multiple-arr-instances-and-variants),
[credentials](docs/configuration.md#credentials), and
[accounts and reverse proxies](docs/configuration.md#accounts-and-reverse-proxies).

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

`dev.py` installs missing web dependencies. Integration tests need ffmpeg, and
tag-editing tests need `mkvpropedit`. After dependency changes, update and commit
`uv.lock`.

See [web development](web/README.md) for frontend setup and checks.

## Licence

MIT. Trackstarr is independent of Radarr, Sonarr, Plex, Jellyfin, Emby and IMDb.

The container image ships ffmpeg (GPL) and
[MKVToolNix](https://mkvtoolnix.download) (GPL), run as separate programs.
Service logos come from
[Dashboard Icons](https://github.com/homarr-labs/dashboard-icons) (Apache 2.0)
and [Simple Icons](https://simpleicons.org) (CC0). Each mark stays its owner's
trademark.
