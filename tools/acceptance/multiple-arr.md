# Live multiple-arr acceptance

Use two Radarr and two Sonarr instances with distinct library folders and, where
possible, overlapping numeric title IDs. Use disposable media and identify the
named connection that can be temporarily removed. Keep API keys and callback
secrets out of screenshots and reports.

Record the Trackstarr revision, arr versions, stable instance names, container
paths, and start time. Save the original connection settings securely so the
removal test can be reversed. Confirm that every root is visible to Trackstarr
and covered by `MEDIA_DIRS` before processing.

| Step | Action | Evidence required |
| --- | --- | --- |
| Connections | Save all four connections and test each. Inspect the generated webhook in each arr. | Correct instance, API health, successful callback, and all-root diagnostics. |
| Imports | Import disposable media through each instance; include a Sonarr batch. | Every accepted file carries the originating stable instance and correct local path despite colliding numeric IDs. |
| Processing | Process a disposable file that requires a rewrite from each source. | Trackstarr completion and the originating arr's rescan command/history agree; no other arr receives that rescan. |
| Restart | Park a hardlinked disposable file, restart Trackstarr, then make it eligible for processing. | Restored job retains its source and rescans only that instance after completion. |
| Removal | Retain a test callback locally, remove the designated named connection and save, then deliver an import using its old callback. | Old callback is refused and creates no job; default connections continue working. |
| Restore | Restore the removed connection and original test settings, then retest. | Correct callback and healthy diagnostics; no disposable work remains queued or parked. |

For each step, record timestamps, title/file identity, HTTP outcome where
applicable, and redacted log or arr history excerpts. Record failures as failures;
connection-test success alone does not establish import or rescan correctness.

If the installation cannot be accessed, leave these results pending rather than
substituting demo or stub results. The local commands remain:

```sh
.venv/bin/pytest tests/test_multi_arr.py -q
cd web
npm run build
node probes/copies-regressions.mjs
npm run build:demo
node probes/copies.mjs
```
