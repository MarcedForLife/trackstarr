# Local demo

Build and start from the repository root:

```sh
docker build --target demo --build-arg WEB_MODE=demo -t trackstarr:demo .
docker compose -f tools/demo/compose.yaml up -d --force-recreate --wait --wait-timeout 60
```

Open http://localhost:5121 and sign in with any username and password. The demo
runs in the browser with simulated data. Reload to reset it, or use
**Debug → Scenarios** to switch sample libraries.

Stop with:

```sh
docker compose -f tools/demo/compose.yaml down
```

See [web development](../../web/README.md#demo) to run the demo without Docker.
