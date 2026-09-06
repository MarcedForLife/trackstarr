// Whether processing is paused, for the nav's warning: a paused service looks
// broken from any page but the overview. One poll shared by the sidebar and
// the tab bar, which can both be mounted mid-resize.

import { poll, type Poller } from '$lib/poll';
import { getActivity } from '$lib/runs';
import { told } from '$lib/stream';

// A reminder, not a readout: this only has to catch a pause made elsewhere.
const POLL_MS = 15000;

let paused = $state(false);
let watchers = 0;
let watch: Poller | null = null;

async function look() {
	try {
		paused = (await getActivity()).paused;
	} catch {
		/* the nav is not where a connection problem gets reported */
	}
}

export const pause = {
	get current() {
		return paused;
	}
};

// Call from an $effect and return the result. The poll runs while anything
// shows the answer.
export function watchPause() {
	watchers += 1;
	if (watchers === 1) {
		// The stream announces a pause; the timer is insurance.
		watch = poll({ ask: look, pace: () => told(POLL_MS), kinds: ['runs'] });
		// The chrome has no loader of its own.
		watch.now();
	}
	return () => {
		watchers -= 1;
		if (watchers) return;
		watch?.stop();
		watch = null;
	};
}
