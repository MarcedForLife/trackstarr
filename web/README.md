# Trackstarr web UI

A Svelte 5 / SvelteKit app with Tailwind CSS v4. It builds to static files in
`build/`, served by the Python listener from `WEB_DIR`. SSR is disabled and
`index.html` is the route fallback; the runtime image needs no Node.js.

## Development

From the repository root, run `uv run dev.py` to start the API and UI together.
Vite serves on `LISTEN_PORT` (5120 by default) and proxies `/api`, `/health` and
`/webhook` to the listener one port higher. The launcher installs web dependencies
if `node_modules` is missing. Leave `WEB_DIR` unset for development.

Run these commands from `web/`:

```sh
npm ci                  # install locked dependencies
npm run check           # Svelte and TypeScript checks
npm run lint            # Prettier and ESLint
npm run format          # apply formatting
npm test                # Vitest; no browser required
npm run test:watch      # rerun tests during development
npm run build           # static production bundle
npm run probe           # build and run focus probes in Chromium and Firefox
npm run probe:sweep     # build and measure library interaction frame times
```

Tests live beside the modules they cover and run in Node, without a DOM.
`src/palette.test.ts` checks palette tokens across CSS and the initial HTML.
For browser-only behaviour, see [probes](probes/README.md). For screenshots,
gesture checks and recordings against a running service, see the
[UI review harness](../tools/uireview/README.md).

## Standalone demo

```sh
npm ci
npm run posters:demo    # fetch licensed poster assets
npm run dev:demo        # run locally without the Python service
npm run build:demo      # build a static demo
```

Any username and password sign in. Demo mode substitutes browser implementations
of the API, event stream and poster loading through `$demo` in `vite.config.ts`.
The normal build selects `none.ts` and does not import the demo backend.

The sample library uses open films and public-domain titles with invented media
files. It supports simulated sweeps, rule changes, retagging and rechecks, plus
three weeks of generated event history. Library and processing changes reset on
reload; browser appearance preferences persist.

`src/lib/demo/posters.json` records image sources and licences.
`posters:demo` checks licences and downloads assets to the gitignored
`src/lib/demo/posters/`; the demo credits displayed images. The GitHub Pages
workflow fetches these before building and uses `BASE_PATH` for the repository
subpath.

## Conventions

- **Reactivity:** runes mode is enabled outside `node_modules`. Use `$state`,
  `$derived` and `$props`. Use `let x = $derived(data.x)` for loader values that
  should reset when data reloads. Reserve `svelte-ignore state_referenced_locally`
  for deliberate one-time reads, such as a class constructor's initial state.
- **Shared updates:** create one `Snapshot` per page and pass it to readers such
  as `ServicePanel` and `Recheck`. Readers request their own polling intervals;
  the shortest wins, avoiding duplicate `/api/runs` requests.
- **Styling:** keep design tokens, base styles and shared keyframes in
  `src/routes/layout.css`. Base element styles use `@layer base`; reduced-motion
  overrides remain unlayered. Animate the CSS property being changed: Tailwind v4
  translation utilities use `translate`, not `transform`.
- **Pages and sections:** `Page.svelte` sets the document title and page width.
  Rules and Appearance use collapsible `Section` groups with short summaries of
  their current settings.
- **Routing:** use `resolve` from `$app/paths` for links and redirects, and
  `$lib/nav`'s `routeOf` for comparisons that exclude the deployment base path.
- **Versions:** keep `package.json` aligned with the backend version.
- **Mobile review:** check layouts around 412×915, as well as desktop. Use a real
  phone to assess animation smoothness.

## API contracts

`tests/test_contract.py` writes backend responses to `src/fixtures/*.json`.
`src/fixtures.test.ts` checks those fixtures against the frontend types;
`src/demo.test.ts` checks the demo against the same contracts.

After changing an endpoint, regenerate fixtures from the repository root:

```sh
UPDATE_FIXTURES=1 uv run pytest tests/test_contract.py
```

Then run `npm run check` and `npm test` from `web/` and commit the updated fixtures.
