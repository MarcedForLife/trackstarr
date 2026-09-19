# Title-sheet architecture

`TitleSheet.svelte` coordinates browsing, work, editor layout, focus, overlays
and the closing animation. The library page owns its `Runner` and receives
queue-change notifications independently of sheet lifetime.

## Browsing

`title-browsing.svelte.ts` owns detail and link reads, verdict polling, cumulative
pagination, season expansion and selected file paths. Every open or retry
creates a `TitleSession` with an identity, validity predicate and guarded reload
callback. Request tickets also prevent older reads from replacing newer data.

Reading choices survive a retry of the same title. Another title or reopening
after close starts fresh. Closing invalidates ownership and stops polling
immediately, while presentation data remains for the closing animation. `clear`
releases that data only if no newer opening exists. Disposal invalidates reads,
stops polling and clears presentation state.

## Work and editor completion

`title-work.svelte.ts` owns queue and pause reads, work polling, title actions,
and busy/error state. It receives the browsing session and combines that
session's validity with its own request ordering. Mutations invalidate pending
reads so stale answers cannot overwrite action results.

File actions capture paths and queued/active targets before awaiting anything.
Their adapter supplies a validity predicate for row feedback. Accepted
cancellations and page-level queue notifications still finish after the sheet
closes; obsolete responses cannot change a newer opening's state. Standalone
file components retain their own action path.

Editor completion captures both opening and editor target. The edited file
must still belong to the accepted cumulative file prefix before completion can
refresh the sheet. Standalone file callers invalidate reads and editor sessions
on destruction.

## Regression coverage

Controller tests cover request ordering, target capture, failures, retry and
polling disposal. `web/probes/copy-regressions/` separates browser scenarios for
connections, selection, reading, delayed reads, pauses, actions, editors and
mapping. Request barriers bound waits and release delayed routes during cleanup.
The probes exercise reopening, stale completions, file removal and unmounting.
