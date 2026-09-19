# Stripping Dolby Vision

## Why

Some displays have no Dolby Vision support, Samsung's OLED line among them. A
DV file plays on one either as washed-out SDR or not at all, depending on how
the player negotiates. The fix at grab time is to score DV below HDR10+ in the
quality profiles, but that only shapes what arrives next. Files already in the
library stay as they are, and re-downloading them costs a library's worth of
bandwidth to change one metadata layer.

Removing the RPU is a stream copy, the same class of operation as the audio and
subtitle rewrites trackstarr already makes:

```
ffmpeg -i in.mkv -map 0 -c copy -bsf:v dovi_rpu=strip=1 out.mkv
```

## What makes it safe

Only DV profile 8 with `dv_bl_signal_compatibility_id` of 1 may be stripped.
That profile carries a real HDR10 base layer, so removing the enhancement layer
leaves a file that is still HDR10, with its mastering display and content light
level metadata untouched. HDR10+ survives too, which matters because the
profile-8 releases from Apple TV+ and Max usually carry both.

Profile 5 has no HDR10 base. Stripping it leaves the washed-out picture the
whole exercise is meant to avoid, so a profile the rule does not recognise is
left alone rather than guessed at.

## Where it lands

`media.probe` already asks ffprobe for `-show_streams`, and the DOVI
configuration record comes back in the video stream's `side_data_list` with
`dv_profile` and `dv_bl_signal_compatibility_id` on it. Detection therefore
costs no extra probe. The container's ffmpeg carries the `dovi_rpu` filter as
of 8.1.2.

1. `policy.RULES` gains `dv_strip`, defaulting to `never`. It destroys data an
   owner may want, which is the same reason `tag_original` and `regenerate` are
   off until asked for. `Policy.fingerprint` already derives from `rule_modes`,
   so cached verdicts invalidate on their own when the mode changes.
2. `media` gains a `dolby_vision` predicate reading the side data, returning the
   profile and compatibility id, plus a `strippable_dv` test wrapping the
   profile 8 condition.
3. `planner` checks the video stream while bucketing and calls `_record` under
   `dv_strip`, so the change files under its own chip in the sheet like every
   other rule.
4. The flag belongs on the video `OutStream`, not on `Plan`. Cover art is also
   a video stream, and feeding `dovi_rpu` an mjpeg stream fails, so `command`
   renders `-bsf:v:{idx} dovi_rpu=strip=1` against the one stream that carries
   the RPU.

## Mode

`alongside` is tempting, since the ride-along rules exist so that a 20GB remux
is never spent on a cosmetic change. DV is not cosmetic here though, it is the
difference between a file playing and not playing, so the rule needs to be able
to act alone. Offer all three modes and let `never` be the default.

## What it costs

A strip runs at roughly 45x realtime against the media pool, so about two
minutes for a 20GB film and under one for an episode. The library currently
holds around 215 files with DV in the name, so a full pass is a few hours of
I/O and no meaningful CPU.

## Checks

The two files stripped by hand on 2026-09-19, the 2026 film and Dark Matter
S02E04, are the natural fixtures. Both were profile 8 compatibility 1, and the
episode is the one that proves HDR10+ survives. A profile 5 file is needed for
the negative case and the library may not hold one, in which case it has to be
synthesised.

Worth adding `dv_profile` to `track_summary` so a file row can say what it
carries, which makes the rule's decision legible before it runs. A new probe
field shows nothing until a plan is rebuilt.
