// The second hand under readouts the page draws between snapshots: a run's
// elapsed time, a rewrite's progress. Each is a measurement plus how long ago
// it arrived, so a poll need not advance a clock the browser already holds.

// How often the readouts move. A bar stepping once a second under a one-second
// linear transition glides.
const TICK_MS = 1000;

/** The tab's monotonic clock, so a time correction cannot send a bar
 * backwards. */
function now(): number {
	return performance.now();
}

let beat = $state(now());
let readers = 0;
let ticker: ReturnType<typeof setInterval> | undefined;

/** The instant to measure an age from, for something that has just arrived. */
export const mark = now;

/** How long ago that instant was, in seconds. Reactive while something on the
 * page is `ticking()`. */
export function since(mark: number): number {
	return Math.max(0, (beat - mark) / 1000);
}

/** Keep the second hand moving while a live readout is on screen. Call from an
 * `$effect` and return what it returns. One interval per tab, none when idle. */
export function ticking(): () => void {
	readers += 1;
	if (!ticker) {
		// Or the first reader after a quiet spell measures against a stale beat.
		beat = now();
		ticker = setInterval(() => (beat = now()), TICK_MS);
	}
	return () => {
		readers -= 1;
		if (readers) return;
		clearInterval(ticker);
		ticker = undefined;
	};
}
