# Rules and audio

[Back to README](../README.md) · [Configuration reference](configuration.md)

Each `RULE_<NAME>` accepts `always`, `alongside` or `never`:

- `always`: apply whenever needed.
- `alongside`: apply only when another rule already requires a rewrite.
- `never`: disable the rule.

| Rule            | Default     | Action                                                                                                |
| --------------- | ----------- | ----------------------------------------------------------------------------------------------------- |
| `languages`     | `always`    | Remove audio and subtitles in languages absent from `LANGUAGES`. Untagged tracks stay.                |
| `tag_original`  | `never`     | Tag untagged audio with the title's original language when it can be identified safely.               |
| `commentary`    | `never`     | Remove commentary, described audio and isolated scores. These are never downmix sources.              |
| `sdh`           | `alongside` | Remove SDH subtitles when a full subtitle remains in the same language. Keep forced subtitles.        |
| `regenerate`    | `never`     | Rebuild outdated downmixes or replace tracks under the configured bitrate rules. MKV only.            |
| `cover_art`     | `always`    | Remove embedded artwork.                                                                              |
| `release_tags`  | `alongside` | Clear release tags from track and container titles.                                                   |
| `stray_streams` | `alongside` | Remove data and timecode streams.                                                                     |
| `order`         | `always`    | Order video, audio by `AUDIO_LAYOUTS` (then other sizes by channel count), subtitles and attachments. |
| `remux`         | `never`     | Convert MP4/M4V to MKV, converting text subtitles to SRT. Video is copied.                            |

Trackstarr never upmixes or removes every audio track. Forced subtitles still
follow the language filter.

## Audio layouts

`AUDIO_LAYOUTS` controls which audio sizes to create, retain or remove, and their
output order. The default `2.0,5.1` creates missing stereo AAC at 320k and 5.1 AC3
at 640k.

```yaml
AUDIO_LAYOUTS: 2.0:libopus:192k,5.1:eac3:448k,7.1:remove
```

| Entry           | Meaning                                                    |
| --------------- | ---------------------------------------------------------- |
| `5.1`           | Create a missing mix using the defaults for this layout.   |
| `5.1:eac3:448k` | Create a missing mix with this encoder and bitrate.        |
| `7.1:keep`      | Keep this layout without creating it, in the listed order. |
| `7.1:remove`    | Remove this size after creating any replacement downmixes. |

Default encoders and bitrates are `1.0:aac:160k`, `2.0:aac:320k`, `5.1:ac3:640k`,
`6.1:aac:704k` and `7.1:aac:768k`. Other sizes need an explicit encoder and rate.
Each channel count may appear only once, including equivalent counts such as
`4.2` and `5.1`.

Downmixes need a larger source in the same language. Layout settings can trigger
a rewrite without a separate `RULE_` setting. Tracks marked for removal can
supply downmixes before they are removed.

## Languages

`LANGUAGES` sets which languages to keep and downmix.
The default is `original,eng`.

```yaml
LANGUAGES: original,eng,fre:keep
```

| Entry      | Meaning                                                                    |
| ---------- | -------------------------------------------------------------------------- |
| `eng`      | Keep English and create the requested downmix layouts where sources exist. |
| `fre:keep` | Keep French without creating mixes.                                        |
| `original` | Keep and downmix the original language reported by Radarr or Sonarr.       |

Explicit entries override `original`. If Radarr or Sonarr cannot identify the
original language, only the explicit entries apply. `en`, `eng` and `English`
are equivalent.

`RULE_TAG_ORIGINAL` tags audio only when there is one untagged track, no track
already in the original language, and no conflicting language or commentary
label. The language must also be one you keep.

For MKV files, a tag-only change usually avoids a rewrite. Other cases use the
normal rewrite process.

Unlisted languages follow `RULE_LANGUAGES`. Untagged tracks stay. If a requested
layout cannot be created in a downmix language, Trackstarr can use another kept
language, preferring `LANGUAGES` order. This fallback is removed when a requested
language can supply the layout.

## Regeneration

Enable `RULE_REGENERATE` to update existing mixes in MKV files:

- `REGENERATE_SCOPE=generated` rebuilds Trackstarr's own tracks when their codec
  or bitrate settings change.
- `REGENERATE_SCOPE=all` also replaces tracks below `REGENERATE_BELOW_PERCENT`
  of the target bitrate, when a larger surviving source can improve them.
  The default threshold is 80% of the target bitrate.
- `REGENERATE_ABOVE_PERCENT` re-encodes oversized lossy tracks from themselves.
  `0` disables this. `150` means above 150% of the target bitrate. Lossless
  tracks are excluded. This option trades quality for space.
