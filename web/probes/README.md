# Browser probes

Manual checks against the built UI in Chromium and Firefox, with a stub API backed
by `../src/fixtures`. They cover browser behaviour that Node tests cannot:
focus during transitions, lingering `inert` state, live regions and frame timing.
No running Trackstarr service or account is needed.

Playwright lives in this directory so the app's normal `npm ci` does not install
browser tooling. CI runs the normal and demo copy probes; the other probes are
manual checks.

## Run

From `web/`, after installing the app dependencies with `npm ci`:

```sh
npm run probe          # build, install probe dependencies/browsers, run focus checks
npm run probe:sweep    # same preparation, then measure library interactions
```

Once prepared, reuse the current build:

```sh
node probes/poster-touch.mjs # sheen resumes after scrolling pauses without lifting the finger
node probes/strip-drag.mjs    # a finger dragging the overview strip keeps the lean steady
node probes/disclosure.mjs    # synchronized row expansion, late content, reversal and reduced motion
node probes/processing.mjs    # overview states, failure links and offline progress
node probes/replan-scroll.mjs # mobile and desktop scroll during queue updates and library re-sorting
node probes/file-account.mjs  # legacy plan explanations and unchecked files
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
for like and check the result on a real phone.

Run `node probes/queue.mjs` after building to check the full queue in Chromium and Firefox: search across 125 files, paging, selection across pages, plans left behind by a rule change, reorder and undo, bulk pause/skip, read-only access, connection recovery, mobile layout, and focus/back behaviour.

## Multiple-variant demo

Run `npm run build:demo && node probes/copies.mjs` from `web/`. This uses the
optional **Multiple variants** scenario and checks single-variant simplicity, one
title per film or series with a folder per instance, film and episode selectors
that lead with the instance holding a variant, the poster's variants count,
connection add/remove with Undo and Discard, mobile overflow and runtime errors
in both engines. It waits for the sheet to open before checking overflow and saves phone and
desktop screenshots under `/tmp/trackstarr-copies-{engine}-{width}.png`.

Run `npm run build && node probes/copies-regressions.mjs` for the normal build's
shared-folder warnings, hidden-copy attention counts, load-more failure/retry,
selection/page-depth persistence during reordered verdict refreshes, replacement
selection after a file disappears and returns, connection-health badges, and
all-root diagnostic display. It also checks failed-refresh feedback and retry,
selection after collapsing seasons, and loading more files that introduces season
sections. A refresh keeps loaded pages, rendered depth, open and closed seasons
and file selections; closing clears that state, and a failed open offers a
retry. Additional cases
move whole episode groups across page boundaries with an editor open, retry a
failed refresh on an empty title, reject late connection-test results after edits,
and retest sources after saving the shared callback. Pause cases cover failure and
retry, and late successful or failed lookups arriving after a newer pause. Mapping
cases cover draft invalidation, saved-mapping feedback, and automatic retesting
after a Plex path-map save. It uses deterministic API responses in Chromium and Firefox at 390 and 1280 px,
and saves `/tmp/trackstarr-copy-review-*.png` screenshots.

CI runs both copy probes after their respective normal and demo builds, in
Chromium and Firefox at both viewport sizes. Episode grouping also has a shared
contract in `src/fixtures/copy-groups.json`, checked by Python and TypeScript.

The copy regression probe also delays pause responses across closing/reopening
titles, including the same title. It checks original-file cancellation, isolation
of newer busy state, and suppression of stale failures for both title-wide and
per-file pauses.

It also holds watch-link responses across reopening the same title, covering
late success and failure, and verifies that an obsolete queue response cannot
trigger a verdict refresh after an action has fetched newer queue state.

For real-backend timing and transferred payload measurements, see
[`title-detail-benchmark.mjs`](title-detail-benchmark.mjs) and the configuration,
methodology and sample results in [the benchmark guide](../../tools/benchmarks/README.md).

## Focused copy regressions

`copies-regressions.mjs` remains the normal-build CI entry point. Its scenarios
live in `copy-regressions/`, with a fresh fixture, browser context and request
history for each scenario and viewport. The runner reports the engine, width
and scenario on failure. Run all scenarios with the existing command, or select
one or more while working on a failure:

```sh
ENGINES=chromium SCENARIOS=reading,editor node probes/copies-regressions.mjs
```

| Scenario                        | Coverage                                                                                                                                    |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `selection`                     | Shared folders, initial/load-more/refresh retry, complete-group paging, reorder, selection fallback, editor removal and season introduction |
| `reading`                       | Per-library page/render depth, season and file selection, failed switch retry, close reset and empty-title recovery                         |
| `connections`                   | Webhook badges, root diagnostics, stale connection tests and callback-save retesting                                                        |
| `pause-identity`                | Held-state display and Resume after primary-source removal and recovery                                                                     |
| `pause`                         | Failed pause retry and a late initial pause lookup                                                                                          |
| `title-actions`, `file-actions` | Original cancellation targets, stale successes/failures and newer busy state across same/different title reopening                          |
| `reads`                         | Same-task quick reopen, stale watch links and obsolete work reads                                                                           |
| `editor`                        | Delayed edit success/failure across same-title reopening                                                                                    |
| `mapping`                       | Draft invalidation, saved path-map feedback and automatic retesting                                                                         |

Delayed-response gates expose `started()`, `release()` and `finished` barriers.
Register browser response waits before releasing a gate; route completion alone
does not mean the UI has consumed the response. Runner cleanup releases pending
gates and drains route handlers before destroying the context, including on a
failed assertion. Scenarios must not rely on state left by an earlier scenario.

`focus.mjs` checks tilt on the inert background posters while a sheet is open,
requires at least one background poster, and still verifies tilt resumes after
closing. The sheet's interactive poster is outside that background assertion.
