# trackstarr web

A SvelteKit app built to static files, served by the Python listener from
`WEB_DIR`. No Node in the runtime image and no SSR: `ssr = false` in
`src/routes/+layout.ts`, with `index.html` standing in for every route.

## Working on it

Start both halves with `uv run dev.py` from the repo root. Vite takes the
listener's port and proxies the API to it one port along, so the browser sees
one origin as in the image. Leave `WEB_DIR` unset in `.env`, or the listener
serves a stale build over the dev server.

```bash
npm run check     # svelte-check, what CI runs
npm run lint      # prettier --check and eslint
npm run format    # prettier --write
npm run test      # vitest over the modules that are answers rather than markup
npm run build     # static bundle into build/
npm run probe     # the bundle in two browsers; see probes/README.md
```

Tests sit beside what they test, in the node environment: no DOM, no
components. `src/palette.test.ts` parses `layout.css` and `app.html` and holds
the copies of the palette tokens to each other.

## Conventions

`src/routes/layout.css` carries every design token, the base element rules and
the shared keyframes. Element rules sit in `@layer base` so a utility overrules
them; only reduced-motion is unlayered, since its `!important` is the point.
Tailwind v4 moves elements with `translate`, so a hand-written
`transition: transform` animates nothing.

Runes are forced on for everything outside `node_modules` (see
`vite.config.ts`), so `$state`/`$derived`/`$props` throughout and no legacy
reactive statements.

A page seeds state from the loader in one of two ways. A value the page later
reassigns is `let x = $derived(data.x)`, so a loader re-run re-seeds it. A
value a class consumes once at construction (`new SettingsDraft(...)`,
`new Snapshot(...)`, the rows of `DirList`) is read under
`svelte-ignore state_referenced_locally`, since the read really is once.

A page that watches what the service is doing builds one `Snapshot` and hands
it to every reader (`ServicePanel`, `new Recheck(...)`), so one stream message
costs one `/api/runs` request. Each reader keeps its own state and asks the
snapshot for its own pace; the soonest wins.

Page titles come from `Page.svelte`, which sets `{title} · Trackstarr` and
carries the `wide` prop the settings pages leave off. `version` in
`package.json` tracks the backend's.

Rules and Appearance fold: each group is a `<Section heading="..." note="...">`
with the first one `open`. The `note` is what the group answers while shut,
such as `3 of 5 on` or `System · Brass`, not a row count.

`src/fixtures/*.json` are the service's own answers, written by
`tests/test_contract.py` and checked by `src/fixtures.test.ts` against the
types in `$lib`, so a key renamed on one side fails the other. After changing
an endpoint, run `UPDATE_FIXTURES=1 uv run pytest tests/test_contract.py` from
the repo root, then `npm run check`.

The UI is used on a phone most of the time. Check anything laid out for a wide
screen at about 412×915 before calling it done.
