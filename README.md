# Trackstarr

Keeps a media library's audio and subtitle tracks tidy, driven by Radarr and
Sonarr webhooks. Stream copy only, no video transcoding, no GPU.

Built to replace a Tdarr plugin stack that was doing nothing but track
selection, and to fix the one thing that stack kept getting wrong, a 2.0
commentary track is not a stereo track. Hence the name, it excels at
tracks.

## The rules

1. **Language.** Keep audio and subtitle tracks whose language is English, the
   title's original language, or untagged. Drop the rest. The original
   language comes from Radarr/Sonarr's `originalLanguage` field, so there is
   no TMDB key to configure and nothing to rate-limit.
2. **Downmix.** Guarantee a non-commentary track for each channel layout in
   `DOWNMIX_LAYOUTS`, 2.0 and 5.1 by default, so a 7.1-only file gains both.
   Each missing layout is downmixed from the best surviving bigger track,
   nothing is upmixed. Commentary, isolated scores and audio description
   never count toward a layout, whether flagged in the container's
   disposition bits or only named in the track title. Each layout is
   encoded at the rate its own `AUDIO_BITRATE_` variable states, 320k for
   2.0 and 640k for 5.1 out of the box; added tracks cost what they weigh,
   so a two-hour film gaining both grows by roughly 860MB.
3. **Cover art.** Drop embedded artwork, which players otherwise read as a
   second video track.
4. **Order.** Video, then audio by ascending channel count (2.0, 5.1, 7.1),
   then subtitles, then attachments.
5. **SDH.** Drop an SDH subtitle when the same language keeps a full one.
   Forced subtitles are always kept.

Rule 5 never causes a rewrite by itself, and neither does clearing release
junk from track titles ("AC3 5.1 @ 640kbps") or dropping a stray data stream.
Rewriting a 60GB remux for a text track costs more than the track does, so
those changes ride along when a real rule forces a rewrite anyway.

Every rule is idempotent, so the sweep is safe to run as often as you like.
Rules you don't want switch off by name (`DISABLED_RULES=languages,sdh`), and
`DROP_COMMENTARY` removes commentary tracks outright.

Generated tracks carry a tag recording the codec and bitrate they were made
with. `REGENERATE_DOWNMIXES=generated` rebuilds any whose settings no longer
match, fresh from their original source, so changing a layout's rate
propagates without generation loss. `all` also replaces a real track
reported below half its layout's configured rate, only ever with a fresh
downmix from a bigger track, never by re-encoding in place, and a track
whose bitrate isn't reported (no mkvmerge statistics tags) is left alone.
Both are off by default because they queue rewrites across the library
after a settings change, and both only touch Matroska files, the one
container that keeps the tag.

`REMUX_TO_MKV` rewrites MP4 and M4V into Matroska (mov_text subtitles become
SRT, everything else is stream copied), so every feature above works on the
result. Opt-in because MP4 direct-plays on more devices.

Things it deliberately does not do: transcode video, upmix, rewrite a file
just to fix a title, or touch a file whose every audio track would fail the
language test.

[muxarr](https://github.com/KirovAir/muxarr) covers similar ground with a
web UI and per-directory profiles, but only ever stream copies. Pick it if
you want a GUI, trackstarr if you want the downmixes.

## Running it

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
      - /mnt/content:/data
      - /mnt/config/trackstarr:/config
    environment:
      TZ: Pacific/Auckland
      MEDIA_DIRS: /data/media/movies:/data/media/tv
      RADARR_URL: http://radarr:7878
      RADARR_API_KEY: ${RADARR_API_KEY}
      SONARR_URL: http://sonarr:8989
      SONARR_API_KEY: ${SONARR_API_KEY}
      SWEEP_AT: "0 4 * * *"
```

`MEDIA_DIRS` is where the sweep walks, in the container's paths, so it has
to match your own layout under the mount above. It is spelled out here
rather than left to its default because a wrong one otherwise costs you a
night: `serve` and `sweep` warn at startup about an entry that isn't there,
and the sweep says so again as it walks, but nothing fails. Webhook imports
keep working either way, they carry their own paths.

`user:` takes PUID and PGID from your `.env` when they're defined. The image
itself defaults to `1000:1000`, so nothing here runs as root whether or not
you set it, and trackstarr therefore can't fix `/config` ownership for you.
Create it before first start, Docker would create it root-owned:

```sh
install -d -o 1000 -g 1000 /mnt/config/trackstarr   # or mkdir + chown
```

Nothing needs configuring in Radarr or Sonarr, trackstarr registers its own
webhook connections at startup, each with a generated secret the listener
requires on every call (the custom-headers field carrying it needs Sonarr
v4 / Radarr 4.3 or newer). Set `WEBHOOK_URL` if the *arrs reach the
container by some name other than `trackstarr`; it is the base URL, the
`/webhook` path is appended at registration.

The port mapping is optional, `GET /health` is all an unauthenticated
caller can reach. 5120 is the two layouts the downmix rule guarantees,
picked mainly because 8080 is already qBittorrent's.

Every API key and token can also be read from a file, `RADARR_API_KEY_FILE`
and `FILE__RADARR_API_KEY` both work, keeping keys out of the compose file
and `docker inspect`. Naming the same credential both ways is refused at
startup rather than resolved by precedence.

A rewrite is staged in `WORK_DIR` as a hidden `.partial` file and published
over the original only once its duration and stream count verify, so an
interrupted job leaves the library untouched. `WORK_DIR` can be on any
filesystem and publishing is atomic either way, so mergerfs, unRAID and
split mounts need no configuration, though a cross-filesystem `WORK_DIR`
writes every rewrite twice (startup says so when it detects it).

A few behaviours worth knowing:

- The sweep remembers its verdicts in `sweep-cache.json`. A file that hasn't
  changed isn't probed again, so after the first night a sweep costs stats,
  not ffprobe runs. Changing any rule setting drops the cache by itself.
  Each verdict carries the file's track summaries, so the cache doubles as
  the library index a media view can read.
- Every rewrite, failure, deferral and sweep appends a JSON line to
  `events.jsonl`, the history a future stats view will aggregate, kept from
  day one because it cannot be backfilled. Each line says what changed twice:
  `reasons` in prose, and `rules` in fixed names (`downmix`, `languages`,
  `sdh`) so aggregating never means parsing English that will be reworded.
  Each also carries a `config_id`, the digest of the settings it was judged
  under; startup and every sweep record the full settings beside theirs, so
  a rewrite years old still resolves to the rules that ordered it.
- Webhook secrets are stored only as SHA-256 digests in
  `/config/webhook-secrets`, verified and re-provisioned at startup, so a
  wiped `/config` heals itself and a copied one leaks nothing. Delete a
  digest to lock that caller out.
- An authenticated caller can POST a webhook-shaped body to `/webhook`
  naming any path the container can reach, the mounts are the boundary. A
  path that doesn't exist here is logged and dropped, usually the *arr and
  trackstarr spelling the library differently.
- An applying sweep downgrades itself to report-only when an enabled *arr
  can't be listed: with original languages unknown, a foreign film's own
  track would read as junk to drop. `fix` refuses the same way unless
  `--original` supplies the language.
- A file the download client still hard-links is left alone and re-checked
  every `HARDLINK_RECHECK` seconds, so it is rewritten minutes after seeding
  ends. Rewriting one is safe for the seed, which keeps the old inode, but it
  breaks the link and the file then occupies disk twice until the torrent
  goes. `SKIP_HARDLINKS=false` rewrites on import instead and takes that
  cost, which is worth it only if you don't seed. The set of files waiting
  this way is kept in `parked.json` and restored at startup: nothing ever
  re-fires an import, so a restart mid-seed would otherwise leave them for a
  sweep, and `SWEEP_AT` is unset by default.
- Configure Plex or Jellyfin below and each rewrite nudges the server, so
  track lists stay correct even on network mounts its own watcher can't see.
- A file that changes mid-rewrite (an upgrade landing) is deferred, the
  result is discarded and the next webhook or sweep retries.

## Commands

```sh
trackstarr serve              # webhook listener plus the scheduled sweep
trackstarr sweep              # walk the library and report to /config/pending.tsv
trackstarr sweep --apply      # ... and rewrite what it finds
trackstarr fix FILE...        # plan and rewrite specific files now
trackstarr plan FILE...       # explain the decision, print the ffmpeg command
trackstarr secret NAME        # mint NAME's webhook secret and print it once
```

`plan` is the one to reach for when a file did something surprising, it
prints the language decision and the exact ffmpeg command without running
it. `fix` is the same decision applied on the spot, wherever the file
lives. Both take `--original` when the file isn't in a *arr library.

`secret` prints its output once and only once, since only the digest is
kept, and refuses to mint again for an existing caller unless you pass
`--rotate`, so a second run can't quietly lock out a working client.

```sh
$ trackstarr plan --original eng "Tears of Steel (2012).mkv"

Tears of Steel (2012).mkv
  original language : eng
  keeping languages : eng
  downmix layouts   : 2.0 (320k), 5.1 (640k)
  - add 2.0 downmix from stream 3 (6ch eng)
  ffmpeg -i ... -map 0:0 -map 0:3 -map 0:1 -map 0:2 -map 0:3 ...
```

## Configuration

Every variable below can also live in `/config/settings.json` as one JSON
object of the same names (`{"DROP_COMMENTARY": true, "SWEEP_AT": "0 4 * * *"}`),
the file a future settings UI will write. The environment wins where both
name a setting, `STATE_DIR` is environment-only since it says where the file
is, and a key nothing reads refuses startup as the typo it usually is.

| Variable                            | Default                             |                                                                                           |
| ----------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------- |
| `MEDIA_DIRS`                        | `/data/media/movies:/data/media/tv` | colon-separated; where the sweep walks                                                    |
| `WORK_DIR`                          | `/data/trackstarr-work`             | any filesystem; a cross-filesystem one costs a copy per rewrite                           |
| `STATE_DIR`                         | `/config`                           | pending.tsv, the sweep cache, the event history, the parked set, webhook secrets and `locks/` live here |
| `RADARR_URL` / `RADARR_API_KEY`     | (unset)                             | omit to disable; the key also takes `_FILE` / `FILE__`                                    |
| `SONARR_URL` / `SONARR_API_KEY`     | (unset)                             | omit to disable; the key also takes `_FILE` / `FILE__`                                    |
| `PLEX_URL` / `PLEX_TOKEN`           | (unset)                             | refresh after rewrites; omit to disable; token also takes `_FILE` / `FILE__`              |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | (unset)                             | same, and the same API fits Emby                                                          |
| `ALWAYS_KEEP_LANGS`                 | `eng`                               | comma-separated; codes or names (`en`, `eng`, `English`) all work                         |
| `DISABLED_RULES`                    | (unset)                             | any of `languages,downmix,cover_art,order,sdh`; dashes read as underscores                |
| `DROP_COMMENTARY`                   | `false`                             | remove commentary tracks instead of protecting them                                       |
| `DOWNMIX_LAYOUTS`                   | `2.0,5.1`                           | layouts guaranteed to exist; each needs an `AUDIO_BITRATE_` below                         |
| `SKIP_HARDLINKS`                    | `true`                              | leave files the download client still links alone, until it lets go                       |
| `HARDLINK_RECHECK`                  | `900`                               | seconds between re-checks; 0 leaves them to the sweep                                     |
| `ALLOWED_EXTS`                      | `.mkv,.mp4,.m4v`                    | containers that will be rewritten                                                         |
| `AUDIO_CODEC`                       | `aac`                               | downmix encoder; `libfdk_aac` if your ffmpeg carries it                                   |
| `AUDIO_BITRATE_2_0` / `_5_1`        | `320k` / `640k`                     | one per layout, dots as underscores; `AUDIO_BITRATE_7_1` for a 7.1                        |
| `REGENERATE_DOWNMIXES`              | (unset)                             | `generated` rebuilds this tool's tracks on settings change; `all` also upgrades weak ones |
| `REMUX_TO_MKV`                      | `false`                             | convert mp4/m4v to mkv, where every feature works                                         |
| `COMMENTARY_PATTERN`                | see `config.py`                     | regex; likewise `SDH_PATTERN`, `FORCED_PATTERN`, `JUNK_TITLE_PATTERN`                     |
| `LISTEN_ADDR` / `LISTEN_PORT`       | `0.0.0.0` / `5120`                  |                                                                                           |
| `WEBHOOK_URL`                       | `http://trackstarr:5120`            | how the *arrs reach the listener                                                          |
| `SWEEP_AT`                          | (unset)                             | cron schedule, local time (`0 4 * * *`); empty disables                                   |
| `SWEEP_APPLY`                       | `false`                             | the sweep reports until this is true                                                      |
| `DRY_RUN`                           | `false`                             | plan and record everywhere, rewrite nothing; overrides `SWEEP_APPLY` and `--apply`        |
| `MAX_CONCURRENT_REWRITES`           | `1`                                 | rewrites at once, across webhooks, sweeps and processes                                   |
| `FFMPEG_TIMEOUT` / `PROBE_TIMEOUT`  | `7200` / `180`                      | seconds                                                                                   |
| `LOG_LEVEL`                         | `INFO`                              |                                                                                           |

`MAX_CONCURRENT_REWRITES` is worth raising if a backfill is going to take
days. ffmpeg's audio encoders are single-threaded, so one rewrite is usually
one busy core and some idle disk, and the default of 1 assumes spinning
disks, where parallel rewrites fight over the heads. Measure rather than
guess, time a sweep at 1 and at 3, if the wall time barely moves storage is
the bottleneck. The budget is shared, webhook imports arriving mid-sweep
queue against the same limit, and a `docker exec trackstarr sweep --apply`
beside a running `serve` competes for the same slots.

Start with `SWEEP_APPLY=false` and read `/config/pending.tsv` before letting
it loose on an existing library. It only gates the sweep, webhook imports
are rewritten as they land, that is the tool's job. To observe everything
without touching anything set `DRY_RUN=true`, plans still run against the
live *arrs and record would-fix events, but nothing is rewritten, not even
by `sweep --apply`.

## Development

```bash
uv sync                 # the dev group in pyproject, at the versions CI uses
cp .env.example .env    # local WORK_DIR/STATE_DIR etc, loaded at startup
uv run pytest           # needs ffmpeg 8.1+ on PATH; refuses to start without it
uv run pytest --cov     # what CI measures; fails under the floor in pyproject
uv run ruff check
uv run ruff format
uv run mypy             # src only, settings in pyproject
```

`.env` is gitignored and read once at startup; anything already in the
environment wins, so it never masks the compose file a deploy uses. The image
ships no `.env` and sets the same names directly.

`uv.lock` is committed so lint and tests mean the same thing here as on CI;
after editing dependencies in `pyproject.toml`, run `uv lock` and commit the
result, or CI's `--locked` will reject it. The package itself has no runtime
dependencies, so `pip install -e .` is still all an install needs.

The rules live in `planner.py` and are pure functions of ffprobe output and
a `Policy` snapshot (`policy.py`), so `tests/test_planner.py` covers them
with hand-built stream dicts and no media at all. Deciding stops there:
`command.py` turns a plan into an ffmpeg argument list and `executor.py`
runs it, which is what lets `plan` print the exact command without going
near a rewrite. `tests/test_integration.py`
generates real files with ffmpeg, and the suite refuses to start without one
new enough to round-trip per-stream MP4 titles, rather than skipping a third
of itself where nobody would notice.

## Licence

MIT. Trackstarr is an independent project, not affiliated with or endorsed
by the Radarr, Sonarr, Plex, Jellyfin or Emby teams.
