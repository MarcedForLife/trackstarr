# UI review harness

Six scripts for looking at the web UI at phone metrics. Review tools, not a
suite; nothing runs in CI. Only `gestures.mjs` has a right answer to check
against.

Playwright is pinned here rather than in `web/package.json` because the app does
not depend on it and the version is chosen for the CDP screencast.

```sh
npm install --prefix tools/uireview
npx --prefix tools/uireview playwright install chromium   # first run only
```

`filmstrip.mjs` and `device.mjs` also need `ffmpeg` and `ffprobe` on PATH.

## Credentials

The scripts log in when a page asks. Either export the account:

```sh
export UIREVIEW_USER=screenshot UIREVIEW_PASS=...
```

or write `dev/ui-review.env` (gitignored, and the default) as `KEY=value` a line
at a time. Any key holding `USER`, `EMAIL` or `NAME` is read as the username and
any key holding `PASS` as the password. `UIREVIEW_ENV` points somewhere else.

## The scripts

Run them from the repo root.

### serve-build.mjs

Serves `web/build` as the image does, with `/api` proxied to the listener, so a
measurement runs against the real bundle. Build first with
`npm run build --prefix web`.

```sh
node tools/uireview/serve-build.mjs          # :5190, /api to :5120
```

### shot.mjs

Full-page screenshots at phone metrics. Settles layout, wrapping, tap targets,
theme and overflow. Says nothing about smoothness.

```sh
node tools/uireview/shot.mjs /activity /sweep
```

### expand-frames.mjs

Frame gaps for the expanding event row, plus the panel's height and position
per frame, which catches an animation that is not animating. Wants the built
app on :5190.

```sh
node tools/uireview/expand-frames.mjs baseline
CURVE="240ms cubic-bezier(0.22,1,0.36,1)" node tools/uireview/expand-frames.mjs slower
THROTTLE=20 node tools/uireview/expand-frames.mjs composited
```

`CURVE` retimes without a rebuild. `THROTTLE=20` proves whether something is
composited: the main thread drops frames and a compositor animation carries on.

### filmstrip.mjs

Clicks a selector and tiles the screencast frames into one contact sheet.
`page.screenshot()` finishes a running animation first, so it only shows the
end state. Dropping frames and not animating read the same in a number and have
opposite fixes; the sheet tells them apart.

```sh
node tools/uireview/filmstrip.mjs /events 'button[aria-controls^="event-"]'
```

### gestures.mjs

Runs one scenario over the library grid with a mouse at desktop metrics, then
with touch events at phone metrics, and checks a poster answers the same way.
Says ok or FAIL per step. Wants an admin account, since a viewer cannot pick.

```sh
node tools/uireview/gestures.mjs
```

### device.mjs

The same recording on a real phone, which is what settles smoothness. Desktop
Chromium at phone metrics models the main thread and nothing else.

```sh
adb connect <phone-ip>:<port>
adb forward tcp:9222 localabstract:chrome_devtools_remote
BASE=http://<this-box-on-the-lan>:5190 SLOW=2000 \
  node tools/uireview/device.mjs /events 'button[aria-controls^="event-"]'
```

`BASE` has to be this box's LAN address, since the phone cannot reach its
localhost. The screen has to be on with Chrome in the foreground, or the
compositor produces no frames. The screencast delivers about 22 frames a second,
so a 180ms animation gets two or three samples; `SLOW=2000` stretches it until
the sheet has enough to read.

## Environment

| Variable | Default | Used by |
| --- | --- | --- |
| `BASE` | `http://localhost:5180`, `:5190` for `expand-frames` and `gestures`, required for `device` | all but `serve-build` |
| `OUT` | `dev/shots` | `shot`, `filmstrip`, `device` |
| `UIREVIEW_USER` / `UIREVIEW_PASS` | none | all but `serve-build` |
| `UIREVIEW_ENV` | `dev/ui-review.env` | all but `serve-build` |
| `WIDTH` / `HEIGHT` | `412` / `915` | all but `serve-build` |
| `PORT` / `BUILD` / `TRACKSTARR_API_PORT` | `5190` / `web/build` / `5120` | `serve-build` |
| `THROTTLE` | `4` for `expand-frames`, off for `filmstrip` | `expand-frames`, `filmstrip` |
| `NAME` / `WINDOW` / `TILES` / `COLS` / `DEPTH` | script name / `600` or `400` / `16` / `4` / `300` | `filmstrip`, `device` |
| `CDP` | `http://localhost:9222` | `device` |

Throwaway probes belong in the gitignored `dev/uireview/`, not here. These six
are the ones worth keeping.
