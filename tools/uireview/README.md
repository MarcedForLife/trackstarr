# UI review harness

Six tools for reviewing a running Trackstarr UI: screenshots, animation timings,
frame contact sheets and mouse/touch gesture checks. These run manually, outside
CI. `gestures.mjs` reports pass/fail results; the recording tools need visual review.

For fixture-backed Chromium and Firefox checks, use the
[browser probes](../../web/probes/README.md).

## Setup

Run commands from the repository root:

```sh
npm ci --prefix tools/uireview
npx --prefix tools/uireview playwright install chromium
npm run build --prefix web
node tools/uireview/serve-build.mjs
```

The server exposes `web/build` at `http://localhost:5190` and proxies `/api` to
port 5120. Start the Python listener separately. With `uv run dev.py`, the API
is normally on 5121, so pass `TRACKSTARR_API_PORT=5121` to `serve-build.mjs`.
`filmstrip.mjs` and `device.mjs` also require `ffmpeg` and `ffprobe` on `PATH`.
Playwright is pinned here independently of the app.

In the shell running the review scripts, set the URL and credentials:

```sh
export BASE=http://localhost:5190
export UIREVIEW_USER=screenshot
export UIREVIEW_PASS='your-password'
```

Alternatively, put credentials in the gitignored `dev/ui-review.env` as
`KEY=value` lines. Keys containing `USER`, `EMAIL` or `NAME` supply the username;
keys containing `PASS` supply the password. `UIREVIEW_ENV` selects another file.
Scripts sign in automatically when shown a login page. Use an account that has
completed its first password change; gesture checks need an admin account.

## Tools

| Script              | Purpose                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------ |
| `serve-build.mjs`   | Serve the production bundle with an API proxy.                                                   |
| `shot.mjs`          | Full-page screenshots for layout, wrapping, themes and overflow.                                 |
| `expand-frames.mjs` | Measure event-row animation frame gaps, height and position. Requires at least three event rows. |
| `filmstrip.mjs`     | Click a selector and tile screencast frames into a contact sheet.                                |
| `gestures.mjs`      | Check library poster interactions with desktop mouse and emulated phone touch.                   |
| `device.mjs`        | Record an interaction in Chrome on a real Android phone via ADB/CDP.                             |

```sh
node tools/uireview/shot.mjs /events /settings/sweep
node tools/uireview/expand-frames.mjs baseline
CURVE="240ms cubic-bezier(0.22,1,0.36,1)" node tools/uireview/expand-frames.mjs slower
THROTTLE=20 node tools/uireview/expand-frames.mjs throttled
node tools/uireview/filmstrip.mjs /events 'button[aria-controls^="event-"]'
node tools/uireview/gestures.mjs
```

Screenshots show layout; screencasts show motion. Compare frame timings with the
contact sheet to distinguish a dropped frame from an element that never animates.
CPU throttling helps expose main-thread work, but desktop emulation does not
establish how smooth an animation feels on a phone.

### Real phone recording

Enable wireless debugging and connect the Android device:

```sh
adb connect <phone-ip>:<port>
adb forward tcp:9222 localabstract:chrome_devtools_remote
BASE=http://<computer-lan-ip>:5190 SLOW=2000 \
  node tools/uireview/device.mjs /events 'button[aria-controls^="event-"]'
```

The phone must be able to reach `BASE`. Keep its screen on with Chrome in the
foreground. `SLOW=2000` stretches supported reveal animations for more samples;
use normal speed to judge how they feel.

## Environment reference

| Variable                                 | Default                                                                                      | Used by                              |
| ---------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------ |
| `BASE`                                   | `http://localhost:5180`; port 5190 for `expand-frames` and `gestures`; required for `device` | All clients                          |
| `OUT`                                    | `dev/shots`                                                                                  | `shot`, `filmstrip`, `device`        |
| `UIREVIEW_USER` / `UIREVIEW_PASS`        | Unset                                                                                        | All clients                          |
| `UIREVIEW_ENV`                           | `dev/ui-review.env`                                                                          | All clients                          |
| `WIDTH` / `HEIGHT`                       | `412` / `915`                                                                                | Desktop phone emulation              |
| `PORT` / `BUILD` / `TRACKSTARR_API_PORT` | `5190` / `web/build` / `5120`                                                                | `serve-build`                        |
| `THROTTLE`                               | `4` for `expand-frames`, off for `filmstrip`                                                 | `expand-frames`, `filmstrip`         |
| `RUNS` / `CURVE`                         | `5` / unchanged                                                                              | `expand-frames`                      |
| `NAME`                                   | `filmstrip` or `device`                                                                      | Contact sheet filename               |
| `WINDOW`                                 | `600` ms for `filmstrip`, `400` ms for `device`                                              | Recording duration after click       |
| `TILES` / `COLS` / `DEPTH`               | `16` / `4` / `300`                                                                           | Contact sheet layout and crop height |
| `CDP` / `SLOW`                           | `http://localhost:9222` / unchanged                                                          | `device`                             |

Keep throwaway experiments in the gitignored `dev/uireview/` directory.
