# trackstarr

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
    volumes:
      - /mnt/content:/data
      - /mnt/config/trackstarr:/config
    environment:
      TZ: Europe/London
      RADARR_URL: http://radarr:7878
      RADARR_API_KEY: ${RADARR_API_KEY}
      SONARR_URL: http://sonarr:8989
      SONARR_API_KEY: ${SONARR_API_KEY}
      SWEEP_AT: "04:00"
```

There is nothing to configure on the Radarr or Sonarr side: at startup
trackstarr registers its own webhook connection with them, firing on import
and upgrade. If they reach the container by some name other than
`trackstarr`, set `WEBHOOK_URL`.

`WORK_DIR` must be on the same filesystem as the media. Rewrites are staged
there and renamed over the original only after their duration and stream
count verify, so an interrupted job leaves the library untouched, but that
rename is only atomic within one filesystem.

A few behaviors worth knowing:

- The sweep remembers its verdicts in `sweep-cache.json`. A file that hasn't
  changed isn't probed again, so after the first night a sweep costs stats,
  not ffprobe runs. Changing any rule setting drops the cache by itself.
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
trackstarr plan FILE...       # explain the decision, print the ffmpeg command
```

`plan` is the one to reach for when a file did something surprising. It prints
the language decision and the exact command that would run, without running
it.

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

| Variable | Default | |
|---|---|---|
| `MEDIA_ROOTS` | `/data/media/movies:/data/media/tv` | colon-separated |
| `WORK_DIR` | `/data/trackstarr-work` | must share a filesystem with the media |
| `STATE_DIR` | `/config` | pending.tsv and the sweep cache live here |
| `RADARR_URL` / `RADARR_API_KEY` | (unset) | omit to disable |
| `SONARR_URL` / `SONARR_API_KEY` | (unset) | omit to disable |
| `PLEX_URL` / `PLEX_TOKEN` | (unset) | refresh after rewrites; omit to disable |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | (unset) | same, and the same API fits Emby |
| `ALWAYS_KEEP_LANGS` | `eng` | ISO 639-2/B, comma-separated |
| `DISABLED_RULES` | (unset) | any of `languages,downmix,cover_art,order,sdh` |
| `DROP_COMMENTARY` | `false` | remove commentary tracks instead of protecting them |
| `DOWNMIX_LAYOUTS` | `2.0,5.1` | layouts guaranteed to exist; own rate as `5.1:640k` |
| `SKIP_HARDLINKS` | `false` | leave files the download client still links alone |
| `HARDLINK_RECHECK` | `900` | seconds between re-checks; 0 leaves them to the sweep |
| `ALLOWED_EXTS` | `.mkv,.mp4,.m4v` | containers that will be rewritten |
| `AUDIO_CODEC` | `aac` | downmix encoder; `libfdk_aac` if your ffmpeg carries it |
| `AUDIO_BITRATE` | `320k` | stereo rate; layouts without their own rate scale it per channel |
| `REGENERATE_DOWNMIXES` | (unset) | `generated` rebuilds this tool's tracks on settings change; `all` also upgrades weak ones |
| `REMUX_TO_MKV` | `false` | convert mp4/m4v to mkv, where every feature works |
| `COMMENTARY_PATTERN` | see `config.py` | regex; likewise `SDH_PATTERN`, `FORCED_PATTERN`, `JUNK_TITLE_PATTERN` |
| `LISTEN_ADDR` / `LISTEN_PORT` | `0.0.0.0` / `8080` | |
| `WEBHOOK_URL` | `http://trackstarr:8080` | how the *arrs reach the listener |
| `SWEEP_AT` | (unset) | `HH:MM` local; empty disables |
| `SWEEP_APPLY` | `false` | the sweep reports until this is true |
| `FFMPEG_TIMEOUT` / `PROBE_TIMEOUT` | `7200` / `180` | seconds |
| `LOG_LEVEL` | `INFO` | |

Start with `SWEEP_APPLY=false` and read `/config/pending.tsv` before letting
it loose on an existing library.

## Development

```bash
pip install -e '.[dev]'
pytest              # unit tests need nothing; integration tests need ffmpeg
ruff check .
ruff format .
```

The rules live in `planner.py` and are pure functions of ffprobe output, so
`tests/test_planner.py` covers them with hand-built stream dicts and no media
at all. `tests/test_integration.py` generates real files with ffmpeg and is
skipped automatically when ffmpeg is missing.

## Licence

MIT.
