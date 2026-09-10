// Whether processing is paused, for the nav's warning: a paused service looks
// broken from any page but the overview. And whether anything is running, for
// the mark in the chrome. One poll shared by the sidebar and the tab bar, which
// can both be mounted mid-resize, and no poll at all on a page already reading
// the service.

import { pageSnapshot } from '$lib/activity.svelte';
import { poll, type Poller } from '$lib/poll';
import { getActivity, type Activity } from '$lib/runs';
import { told } from '$lib/stream';

// A reminder, not a readout: this only has to catch a pause made elsewhere.
const POLL_MS = 15000;

let paused = $state(false);
let busy = $state(false);
let watchers = 0;
let watch: Poller | null = null;

function note(activity: Activity) {
	paused = activity.paused;
	busy = activity.runs.length > 0;
}

async function look() {
	try {
		note(await getActivity());
	} catch {
		/* the nav is not where a connection problem gets reported */
	}
}

export const pause = {
	get current() {
		return paused;
	}
};

/** Whether the service has a run going: a sweep, an import or a re-check. */
export const running = {
	get current() {
		return busy;
	}
};

// Call from an $effect and return the result. The poll runs while anything
// shows the answer.
export function watchPause() {
	watchers += 1;
	if (watchers === 1) {
		// The stream announces a pause; the timer is insurance. No look while the
		// page under the nav is making the same one.
		watch = poll({
			ask: look,
			pace: () => told(POLL_MS),
			ready: () => !pageSnapshot.current,
			kinds: ['runs']
		});
		// The chrome has no loader of its own.
		watch.now();
	}
	// Copied out rather than read through, so the answer stays when the page that
	// was reading it goes and this poll has yet to take over.
	$effect(() => {
		const reading = pageSnapshot.current;
		if (reading) note(reading.current);
	});
	return () => {
		watchers -= 1;
		if (watchers) return;
		watch?.stop();
		watch = null;
	};
}
