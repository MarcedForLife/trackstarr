# Trackstarr

Keeps a media library's audio and subtitle tracks tidy, driven by Radarr and
Sonarr webhooks. Stream copy only, no video transcoding, no GPU.

Built to replace a Tdarr plugin stack that was doing nothing but track
selection, and to fix the one thing that stack kept getting wrong: a 2.0
commentary track is not a stereo track. Hence the name, it excels at
tracks.

## The rules

1. **Language.** Keep audio and subtitle tracks whose language is English, the
   title's original language, or untagged. Drop the rest. The original
   language comes from Radarr/Sonarr's `originalLanguage` field, so there is
   no TMDB key to configure and nothing to rate-limit.
2. **Downmix.** Guarantee a non-commentary track for each channel layout in
   `DOWNMIX_LAYOUTS`, 2.0 and 5.1 by default, so a 7.1-only file gains both.
   Each missing layout is downmixed from the best surviving bigger track;
   nothing is upmixed. Commentary, isolated scores and audio description
   never count toward a layout, whether they are flagged in the container's
   disposition bits or only named in the track title.
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

Every rule is idempotent. Applying the result and re-planning yields an empty
plan, so the sweep is safe to run as often as you like. Rules you don't want
switch off by name (`DISABLED_RULES=languages,sdh`), and `DROP_COMMENTARY`
removes commentary tracks outright if protecting them isn't what you want.

Generated tracks carry a tag recording the codec and bitrate they were made
with. `REGENERATE_DOWNMIXES=generated` rebuilds any whose settings no longer
match, fresh from their original source, so changing `AUDIO_BITRATE`
propagates without generation loss. `all` goes further and also replaces a
real track reported below half its layout's configured rate, a 128k stereo
beside a lossless 5.1, say. Both are off by default because they queue
rewrites across the library after a settings change, and both only ever
touch Matroska files, the one container that keeps the identifying tag. A
track whose bitrate the container doesn't report is left alone (that means
files without mkvmerge's statistics tags), and an original track is only
ever replaced by a fresh downmix from a bigger track, never re-encoded in
place.

`REMUX_TO_MKV` rewrites MP4 and M4V into Matroska, converting mov_text
subtitles to SRT and stream copying everything else, so a converted library
is one where every feature above works. Opt-in because MP4 direct-plays on
more devices; a household of older clients may prefer the status quo.

Things it deliberately does not do: transcode video, upmix, rewrite a file
just to fix a title, or touch a file whose every audio track would fail the
language test.

[muxarr](https://github.com/KirovAir/muxarr) covers similar ground with a web
UI and per-directory profiles. It only ever stream copies, so it cannot create
a stereo or 5.1 track where none exists. Pick it if you want a GUI, pick
trackstarr if you want the downmixes and a container that compose alone can
configure.

## Running it

```yaml
services:
  trackstarr:
    image: ghcr.io/marcedforlife/trackstarr:latest
    container_name: trackstarr
    restart: unless-stopped
    user: "1000:1000"
    ports:
      - "5120:5120"
    volumes:
      - /mnt/content:/data
      - /mnt/config/trackstarr:/config
    environment:
      TZ: Europe/London
      RADARR_URL: http://radarr:7878
      RADARR_API_KEY: ${RADARR_API_KEY}
      SONARR_URL: http://sonarr:8989
      SONARR_API_KEY: ${SONARR_API_KEY}
      SWEEP_AT: "0 4 * * *"
```

There is nothing to configure on the Radarr or Sonarr side: at startup
trackstarr registers its own webhook connection with them, firing on import
and upgrade. Each connection carries its own generated secret, which the
listener requires on every call; the custom-headers field it rides in needs
Sonarr v4 / Radarr 4.3 or newer. If they reach the container by some name
other than `trackstarr`, set `WEBHOOK_URL`.

The port mapping is optional. Radarr and Sonarr reach the listener over the
compose network, so publishing it is only for your benefit: `GET /health`
(or `/ping`) answers `healthy`, which is what the container's healthcheck
probes and the only thing an unauthenticated caller can reach. Every POST
needs the per-connection secret. 5120 is the two layouts the downmix rule
guarantees, and picked mainly because 8080 is already qBittorrent's.

Every API key and token can be read from a file instead of the environment,
which keeps it out of the compose file and out of `docker inspect` — the two
places these actually escape from. Both conventions work, `RADARR_API_KEY_FILE`
and `FILE__RADARR_API_KEY`, so whichever one the rest of your stack uses is
the one to use here:

```yaml
services:
  trackstarr:
    environment:
      RADARR_URL: http://radarr:7878
      RADARR_API_KEY_FILE: /run/secrets/radarr_api_key
    secrets:
      - radarr_api_key

secrets:
  radarr_api_key:
    file: ./secrets/radarr_api_key
```

That block needs no swarm; plain `docker compose` bind-mounts the file to
`/run/secrets/<name>`. A plain read-only volume does the same job with less
ceremony. Naming the same credential both ways is refused at startup rather
than resolved by precedence, so there is never a question of which one is
live. This is hygiene, not a boundary: the file is still plaintext, and
anything running as this container's user can read it.

A rewrite is staged in `WORK_DIR` as a hidden `.partial` file and published
over the original only once its duration and stream count verify, so an
interrupted job leaves the library untouched.

`WORK_DIR` can be on any filesystem, including a different drive from the
media. Publishing renames when it can and copies when it can't: `rename` is
atomic within a filesystem and fails with `EXDEV` across one, so trackstarr
tries it, and on `EXDEV` copies the finished file onto the target's own
filesystem under a hidden name and renames *that* into place. Either way
readers see the old file or the new one, never a partial write, and multi-
drive layouts — mergerfs, unRAID, SnapRAID, or just movies and TV on
separate mounts — need no configuration.

The cost of a cross-filesystem `WORK_DIR` is that every rewrite is written
twice, once by ffmpeg and once by the copy. Startup says so when it detects
it. Whether that matters depends on where the bottleneck is: these rewrites
are usually limited by single-threaded audio encoding rather than disk, in
which case the extra copy disappears into the noise.

A few behaviors worth knowing:

- The sweep remembers its verdicts in `sweep-cache.json`. A file that hasn't
  changed isn't probed again, so after the first night a sweep costs stats,
  not ffprobe runs. Changing any rule setting drops the cache by itself.
- Every rewrite, rewrite failure and sweep appends a JSON line to
  `events.jsonl`, recording what changed, sizes before and after, how long
  it took, and the version and settings responsible. Sweep summaries carry
  the library's total size and a run id their rewrites share, so one
  night's work groups together and growth can be plotted. Nothing consumes
  it yet, it is the history a stats view will aggregate, kept from day one
  because it cannot be backfilled. Readers take any `events*.jsonl` sibling
  too, so if it ever grows unwieldy, move a chunk to `events-2026.jsonl`
  and history stays whole.
- Webhook secrets are kept as SHA-256 digests, one file per caller in
  `/config/webhook-secrets`. Nothing stores the secret itself, so a copy of
  the config volume carries nothing that can be replayed, and a lost secret
  is rotated rather than looked up. The *arrs never need one read back: what
  their connection holds is checked against the digest at every startup and
  replaced when it doesn't verify, so a wiped `/config` re-provisions itself.
  Delete a file and that caller alone is locked out, immediately.
- An authenticated caller can queue any path the container can reach, so
  the mounts are the boundary: mount only what the tool may touch. A path
  that doesn't exist here is logged and dropped, the usual sign the *arr
  and trackstarr spell the library differently.
- With `SKIP_HARDLINKS` set, a file the download client still hard-links is
  left alone. Webhook imports wait in memory and are re-checked every
  `HARDLINK_RECHECK` seconds; the sweep picks up anything a restart forgets.
- Configure Plex or Jellyfin below and each rewrite nudges the server, so
  track lists stay correct even on network mounts its own watcher can't see.
- A file that changes mid-rewrite (an upgrade landing) is deferred, not
  failed. The result is discarded and the next webhook or sweep retries.

## Commands

```
trackstarr serve              # webhook listener plus the scheduled sweep
trackstarr sweep              # walk the library and report to /config/pending.tsv
trackstarr sweep --apply      # ... and rewrite what it finds
trackstarr fix FILE...        # plan and rewrite specific files now
trackstarr plan FILE...       # explain the decision, print the ffmpeg command
trackstarr secret NAME        # mint NAME's webhook secret and print it once
```

`plan` is the one to reach for when a file did something surprising. It prints
the language decision and the exact command that would run, without running
it. `fix` is the same decision applied on the spot, wherever the file lives.
Both take `--original` when the file isn't in a *arr library.

`secret` prints its output once and only once, since only the digest is kept.
Running it again for a caller that already has one is refused unless you pass
`--rotate`, which replaces the old secret immediately — so a second run can't
quietly lock out a client that was working.

```
$ trackstarr plan --original eng "Star Wars (1977).mkv"

Star Wars (1977).mkv
  original language : eng
  keeping languages : eng
  downmix layouts   : 2.0 (320k), 5.1 (960k)
  - add 2.0 downmix from stream 3 (6ch eng)
  ffmpeg -i ... -map 0:0 -map 0:3 -map 0:1 -map 0:2 -map 0:3 ...
```

## Configuration

| Variable                            | Default                             |                                                                                           |
| ----------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------- |
| `MEDIA_DIRS`                        | `/data/media/movies:/data/media/tv` | colon-separated; where the sweep walks                                                    |
| `WORK_DIR`                          | `/data/trackstarr-work`             | any filesystem; a cross-filesystem one costs a copy per rewrite                           |
| `STATE_DIR`                         | `/config`                           | pending.tsv, the sweep cache, the event history and webhook secrets live here             |
| `RADARR_URL` / `RADARR_API_KEY`     | (unset)                             | omit to disable; the key also takes `_FILE` / `FILE__`                                    |
| `SONARR_URL` / `SONARR_API_KEY`     | (unset)                             | omit to disable; the key also takes `_FILE` / `FILE__`                                    |
| `PLEX_URL` / `PLEX_TOKEN`           | (unset)                             | refresh after rewrites; omit to disable; token also takes `_FILE` / `FILE__`              |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | (unset)                             | same, and the same API fits Emby                                                          |
| `ALWAYS_KEEP_LANGS`                 | `eng`                               | comma-separated; codes or names (`en`, `eng`, `English`) all work                         |
| `DISABLED_RULES`                    | (unset)                             | any of `languages,downmix,cover_art,order,sdh`                                            |
| `DROP_COMMENTARY`                   | `false`                             | remove commentary tracks instead of protecting them                                       |
| `DOWNMIX_LAYOUTS`                   | `2.0,5.1`                           | layouts guaranteed to exist; own rate as `5.1:640k`                                       |
| `SKIP_HARDLINKS`                    | `false`                             | leave files the download client still links alone                                         |
| `HARDLINK_RECHECK`                  | `900`                               | seconds between re-checks; 0 leaves them to the sweep                                     |
| `ALLOWED_EXTS`                      | `.mkv,.mp4,.m4v`                    | containers that will be rewritten                                                         |
| `AUDIO_CODEC`                       | `aac`                               | downmix encoder; `libfdk_aac` if your ffmpeg carries it                                   |
| `AUDIO_BITRATE`                     | `320k`                              | stereo rate; layouts without their own rate scale it per channel                          |
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
days. A rewrite is a stream copy plus a few audio encodes, and ffmpeg's
audio encoders are single-threaded, so one rewrite is usually one busy core
and some idle disk. The default is 1 because the safe assumption is spinning
disks, where parallel rewrites fight over the heads.

Measure rather than guess. Time a sweep at 1 and at 3; if the wall time
barely moves, your storage is the bottleneck and the extra workers only cost
memory. If it drops close to linearly, you were leaving cores idle. The
budget is shared, so webhook imports arriving mid-sweep queue against the
same limit rather than doubling the load, and it holds across processes too:
a `docker exec trackstarr sweep --apply` beside a running `serve` competes
for the same slots.

Start with `SWEEP_APPLY=false` and read `/config/pending.tsv` before letting
it loose on an existing library. Note that `SWEEP_APPLY` only gates the
sweep: files arriving through a webhook are rewritten as they land, which is
the tool's job for new imports. To observe everything without touching
anything, set `DRY_RUN=true`: webhooks and sweeps still plan against the
live *arrs, and each webhook import records a would-fix event saying what
would have happened, but nothing is rewritten, not even by `sweep --apply`.

## Development

```bash
pip install -e '.[dev]'
pytest              # unit tests need nothing; integration tests need ffmpeg
ruff check .
ruff format .
```

The rules live in `planner.py` and are pure functions of ffprobe output and
a `Policy` snapshot (`policy.py`), so `tests/test_planner.py` covers them
with hand-built stream dicts and no media at all. `tests/test_integration.py` generates real files with ffmpeg and is
skipped automatically when ffmpeg is missing.

## Licence

MIT.
