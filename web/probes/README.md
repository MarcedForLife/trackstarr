# Browser probes

Manual checks against the built UI in Chromium and Firefox, with a stub API backed
by `../src/fixtures`. They cover browser behaviour that Node tests cannot:
focus during transitions, lingering `inert` state, live regions and frame timing.
No running Trackstarr service or account is needed.

Playwright lives in this directory so the app's normal `npm ci` does not install
browser tooling. Probes do not run in CI.

## Run

From `web/`, after installing the app dependencies with `npm ci`:

```sh
npm run probe          # build, install probe dependencies/browsers, run focus checks
npm run probe:sweep    # same preparation, then measure library interactions
```

Once prepared, reuse the current build:

```sh
node probes/processing.mjs    # overview states, failure links and offline progress
ENGINES=firefox node probes/focus.mjs
HEADED=1 node probes/focus.mjs
TITLES=1200 ENGINES=firefox node probes/sweep.mjs
GESTURES=hover HEADED=1 node probes/sweep.mjs
SERVE=1 node probes/focus.mjs   # keep the stub site open on port 5197 for manual review
```

`focus.mjs` reports a pass/fail table. `sweep.mjs` measures frame times for hover,
wheel and drag gestures over a synthetic library (400 titles by default).
`ENGINES` defaults to `chromium,firefox`; `SCALE` controls the sweep probe's device
pixel ratio (default 2).

## Compare a change

Keep a copy of the baseline `build/` before rebuilding the changed UI. Point a
probe at it with `ROOT=/absolute/path/to/baseline-build`, then repeat against the
new bundle using the same engine and settings. A regression check should fail
on the affected baseline and pass after the fix.

Frame timings are diagnostic, not a portable performance score. Compare like
for like and check the result on a real phone. For recordings against the live
service, use the [UI review harness](../../tools/uireview/README.md).
