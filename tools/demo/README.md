# Local demo deployment

The local `tsd` command offers to build/update the demo on port 5121 after deploying the main service. Use `tsd --demo` to include it or `tsd --no-demo` to skip it. Unattended runs skip it by default. The demo builds the local working tree, even with `--release`.

To deploy the demo by itself, run from the repository root:

```sh
docker build --target demo --build-arg WEB_MODE=demo -t trackstarr:demo .
docker compose -f tools/demo/compose.yaml up -d --force-recreate --wait --wait-timeout 60
```

Open http://localhost:5121. This separate static site has no media mounts or backend service. Its simulated data lives in each browser tab and resets on reload. Debug tools in the banner deliver a 4K replacement missing stereo from either *arr, or pose a board: the full one the demo opens on, imports only, a rewrite that broke, or everything held with work waiting.
