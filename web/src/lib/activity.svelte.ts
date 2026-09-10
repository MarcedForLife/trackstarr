// One reading of what the service is doing, shared by every reader on a page.
// The overview has two, the transport bar and the re-check behind a title's
// sheet, and one stream message wakes both: the look is made here once and
// handed to each. What a run leaving the snapshot means is the reader's own
// business, so they stay two classes.

import { onDestroy } from 'svelte';
import { poll, type Poller } from '$lib/poll';
import { getActivity, type Activity } from '$lib/runs';
import { told } from '$lib/stream';

// The least time between two looks however many stream messages arrive, which
// holds a sweep's progress to a rate a person can read.
const GAP_MS = 2000;

// What the poll waits with nobody watching, which is only the moment between
// construction and the first reader.
const IDLE_MS = 15000;

/** A snapshot landing, for a reader that works in differences. */
export type Landed = {
	/** What the service says now. */
	now: Activity;
	/** What it said last. */
	before: Activity;
	/** Whether a look failed in between, so a run missing from `now` may have
	 * been cut off rather than finished. */
	missed: boolean;
};

export type Reader = {
	/** How long this reader would wait for the next look. The soonest wins. */
	pace: () => number;
	/** A snapshot landed. */
	saw: (landed: Landed) => void | Promise<void>;
};

// The reading the page on screen is making, for the chrome around it. The nav's
// pause warning is on every page and outlives all of them, so it cannot own one
// of these, but it can read the page's rather than ask again. See $lib/paused.
let onPage = $state<Snapshot | null>(null);

/** What the page is reading, or nothing on a page that reads nothing. */
export const pageSnapshot = {
	get current(): Snapshot | null {
		return onPage;
	}
};

function readHere(snapshot: Snapshot | null) {
	onPage = snapshot;
}

export class Snapshot {
	/** The last reading, seeded from the page's loader. */
	current = $state<Activity>()!;
	/** A lost connection, kept apart from a refusal: the next look clears it, and
	 * what is on screen stays up under it. */
	offline = $state('');

	// Plain: nothing draws the set, and it is read only as the poll re-arms and
	// as a snapshot lands.
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	#readers = new Set<Reader>();
	// Whether a look has failed since the last one that landed.
	#missed = false;
	// Looks can cross: a button's and the poll's can be in the air at once, and
	// the older one landing last would put back what the newer just said.
	#sent = 0;
	#landed = 0;
	#runs: Poller;

	constructor(seed: Activity) {
		this.current = seed;
		this.#runs = poll({
			ask: () => this.look(),
			pace: () => this.#pace(),
			gap: GAP_MS,
			kinds: ['runs', 'progress']
		});
		readHere(this);
		onDestroy(() => {
			this.#runs.stop();
			// Only if the next page has not already put its own there: which of the
			// two runs first is the router's business, not this class's.
			if (onPage === this) readHere(null);
		});
	}

	/** Read every landing until the returned stop is called. */
	watch(reader: Reader): () => void {
		this.#readers.add(reader);
		// The reader has just taken `current` as its seed, and the poll was armed
		// before it had a pace to ask for.
		this.#runs.mark();
		return () => {
			this.#readers.delete(reader);
		};
	}

	/** Ask the service, no sooner than the gap after the last look. */
	prod() {
		this.#runs.prod();
	}

	/** Somebody else just read this: pace the next look from now. */
	mark() {
		this.#runs.mark();
	}

	/** Look now and wait for the answer, for a press that cannot finish drawing
	 * itself without it. */
	async look() {
		const sent = ++this.#sent;
		let fresh: Activity;
		try {
			fresh = await getActivity();
		} catch {
			// Not while a newer look is out or already in: it covers the same
			// window, so nothing here was missed and the service is plainly there.
			if (sent < this.#sent) return;
			this.#missed = true;
			this.offline = 'Could not reach the service.';
			return;
		}
		this.#land(sent, fresh);
	}

	/** A control answered with a snapshot of its own: this moment's, so it lands
	 * like a look and saves one. */
	take(activity: Activity) {
		this.#land(++this.#sent, activity);
	}

	#land(sent: number, activity: Activity) {
		// An older answer has nothing to add once a newer one has landed.
		if (sent < this.#landed) return;
		this.#landed = sent;
		const landed: Landed = { now: activity, before: this.current, missed: this.#missed };
		this.current = activity;
		this.offline = '';
		this.#missed = false;
		for (const reader of [...this.#readers]) {
			try {
				// Not awaited: the next look must not wait on what a reader makes of
				// this one, such as an ended run's summary read out of the history.
				void Promise.resolve(reader.saw(landed)).catch(() => {});
			} catch {
				/* a reader that throws is a bug of its own, not the poll's business */
			}
		}
	}

	// Read as the poll re-arms, not watched: a reactive read would rebuild the
	// timer on every answer.
	#pace(): number {
		const paces = [...this.#readers].map((reader) => reader.pace());
		return paces.length ? Math.min(...paces) : told(IDLE_MS);
	}
}
