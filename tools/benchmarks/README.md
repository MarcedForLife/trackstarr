# Title-detail benchmark

Run from the repository root:

```sh
.venv/bin/python tools/benchmarks/title_detail.py
.venv/bin/python tools/benchmarks/title_detail.py --background 5000
```

The default dataset has 50,000 background files and two related libraries, each
with 1,000 episodes and three copies per episode: 56,000 files total. The selected
title returns 600, 1,200 and 3,000 files at cumulative depths 1, 2 and 5. Entries
include video, audio and subtitle tracks, plans and reasons. `--episodes`,
`--copies`, `--background` and `--repeats` vary the dataset and sampling.

The script runs production title grouping, sorting, rollups, file projection and
JSON serialization. It supplies in-memory cache, catalogue and rewrite-history
responses. It never reads installation settings, contacts arr servers or processes
media. Results exclude cold cache parsing, disk access, network transfer,
compression and browser rendering. This is a warm computation benchmark, not an
end-to-end latency claim. Run on an otherwise idle machine for comparisons; no
hardware-dependent timing threshold belongs in CI.

## Browser and HTTP measurements

`web/probes/title-detail-benchmark.mjs` drives the normal built UI against a real
test backend. It opens a title and presses **Load more files** through cumulative
page depths, recording HTTP duration, time to first byte, compressed/decoded body
sizes and click-to-settlement timing. It performs one unreported warm-up followed
by five measured passes. It only reads library data and creates/deletes its own
login session; it does not seed data or process media.

Put configuration in a private, ignored JSON file (mode 0600):

```json
{
  "url": "http://127.0.0.1:15120",
  "username": "admin",
  "password": "YOUR_TEST_PASSWORD",
  "titleId": "arr:sonarr:1",
  "cardLabel": "^Severance.*Pending",
  "repeats": 5,
  "pages": 5
}
```

`cardLabel` is a regular expression matching exactly one library-card button.
The title must contain enough groups to reach the requested depth. With probe
dependencies and Chromium installed, run from `web/`:

```sh
node probes/title-detail-benchmark.mjs ../dev/multi-arr-acceptance/benchmark.json
```
