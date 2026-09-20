# Web UI

Svelte 5, SvelteKit and Tailwind CSS. Builds to static files in `build/`, served
by the Python service through `WEB_DIR`.

## Development

Run `uv run dev.py` from the repository root to start the API and UI together.
Open http://localhost:5120 (or `LISTEN_PORT`). Leave `WEB_DIR` unset for development.
The launcher installs missing web dependencies and proxies API requests to the
Python service.

From `web/`:

```sh
npm ci
npm run check       # Svelte and TypeScript
npm run lint        # Prettier and ESLint
npm test            # unit and contract tests
npm run build       # production bundle
```

Use `npm run format` to apply formatting and `npm run test:watch` while developing.

## Demo

The demo runs in the browser without a Python service. From `web/`:

```sh
npm run posters:demo    # download credited poster assets
npm run dev:demo       # local demo
npm run build:demo     # static demo bundle
```

Sign in with any username and password. **Debug → Scenarios** offers sample
libraries, including multiple variants, a large series and connection failures.
Reload to reset the demo. Appearance preferences are saved.

Poster sources and licences are recorded in `src/lib/demo/posters.json`.
Downloaded images are gitignored. See [local demo deployment](../tools/demo/README.md)
for Docker, or `.github/workflows/demo.yml` for GitHub Pages.

## API contracts

`/api/settings` returns Radarr and Sonarr connections in `arr_instances`. Each
record includes an `id`, `type`, label and `fields` with values and environment
overrides. Credential values are blank, with `set` indicating whether one is
configured. Use these records to display connections.

A settings POST can include ordinary settings and `arr_instances` operations:

- `{id, values}` edits a connection.
- `{id, type, create: true, values}` adds one.
- `{id, remove: true}` removes one.

Blank or omitted credentials keep the saved value. `null` clears it. Requests
succeed or fail as a whole. Renaming a connection keeps its ID. Environment
variables and `settings.json` use flat setting names, which the API also accepts.

After changing an endpoint, regenerate fixtures from the repository root:

```sh
UPDATE_FIXTURES=1 uv run pytest tests/test_contract.py
```

Then run `npm run check` and `npm test` from `web/` and commit the updated
`src/fixtures/` files. Tests check both the fixtures and demo against the frontend
contracts.
