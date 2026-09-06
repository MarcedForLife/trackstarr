# Probes

Checks that only exist in a browser: a `focus()` refused during a transition,
an `inert` left behind, a live region that never announces, a dropped frame.
These drive the built bundle in Chromium and Firefox behind a stub service
answering with the fixtures in `../src/fixtures`.

Not in CI: they need browsers, and a Gecko answer still wants a real phone.
Playwright lives in this directory's own `package.json` so `npm ci` in `web/`
never installs it.

```bash
npm run probe          # build, then focus.mjs: the pass/fail table, both engines
npm run probe:sweep    # build, then sweep.mjs: frame times over the shelf
ENGINES=firefox node probes/focus.mjs    # one engine, on the last build
SERVE=1 node probes/focus.mjs            # serve it on :5197 for a phone
```

For a "before": `git stash push -- src`, `npm run build`, copy `build/` aside,
`git stash pop`, build again, then run a probe with `ROOT=<the copy>`. A probe
the old bundle also passes is not testing the change. Judge numbers in Zen or
Firefox and on a phone; desktop Chromium does not transfer.
