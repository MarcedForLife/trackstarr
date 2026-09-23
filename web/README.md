# Web UI

Svelte 5, SvelteKit and Tailwind CSS. The Python service serves the static `build/` output through
`WEB_DIR`.

## Development

Run `uv run dev.py` from the repository root to start the API and UI at http://localhost:5120 (or
`LISTEN_PORT`). It installs missing web dependencies and proxies API requests to Python. Leave
`WEB_DIR` unset.

From `web/`:

```sh
npm ci
npm run check       # Svelte and TypeScript
npm run lint        # Prettier and ESLint
npm test            # unit and contract tests
npm run build       # production bundle
```

Use `npm run format` to format code and `npm run test:watch` for watch mode.

## Demo

The demo runs in the browser without a Python service. From `web/`:

```sh
npm run posters:demo    # download credited poster assets
npm run dev:demo       # local demo
npm run build:demo     # static demo bundle
```

If prompted, sign in with any username and password. **Debug / Scenarios** switches between sample
libraries, including multiple variants, a large series and connection failures. Reload to reset the
demo. Appearance preferences persist.

`src/lib/demo/posters.json` records artwork sources and licences. Downloads are gitignored. See
[local demo deployment](../tools/demo/README.md) for Docker or `.github/workflows/demo.yml` for
GitHub Pages.

## API contracts

`/api/settings` returns Radarr and Sonarr connections in `arr_instances`, with an `id`, `type`,
label and `fields` containing values and environment overrides. Credentials are blank, with `set`
indicating a configured credential.

A settings POST accepts ordinary settings and `arr_instances` operations.

- `{id, values}` edits a connection.
- `{id, type, create: true, values}` adds one.
- `{id, remove: true}` removes one.

Blank or omitted credentials keep the saved value. `null` clears it. Requests succeed or fail as a
whole. Renaming keeps the connection ID. The API also accepts the flat setting names used in
environment variables and `settings.json`.

After changing an endpoint, regenerate fixtures from the repository root.

```sh
UPDATE_FIXTURES=1 uv run pytest tests/test_contract.py
```

Run `npm run check` and `npm test` from `web/`, then commit `src/fixtures/`. Tests check fixtures
and demo responses against the frontend contracts.
