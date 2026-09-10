// One poll loop for every page: look on a timer, sooner when the stream says
// something changed, never twice at once, never on a hidden tab, and hold a
// burst to a pace a person can read. Four hand-rolled copies had drifted apart.
//
// Made at component init, stopped when the page goes. Nothing is reactive:
// `pace()` and `ready()` are read as the chain re-arms, so a poll does not
// rebuild its timer on every answer.

import { subscribe, type Kind } from '$lib/stream';

type Watched = {
	/** The look. Made only on a visible tab, never twice at once, and must not
	 * throw. */
	ask: () => Promise<void>;
	/** Whether a look is worth making now. A no keeps what the stream owed for
	 * the next pass. */
	ready?: () => boolean;
	/** The stream kinds that prompt a look. */
	kinds?: readonly Kind[];
};

/**
 * `pace` is how long between looks of the poller's own accord, read fresh each
 * time. `gap` is the least time between two looks however many messages arrive,
 * which holds a sweep's message a second to a redrawable rate. A poller with no
 * pace only looks when told, so it needs the gap.
 */
export type Watch = Watched &
	({ pace: () => number; gap?: number } | { pace?: undefined; gap: number });

export type Poller = {
	/** Look again, no sooner than `gap` after the last look. */
	prod: () => void;
	/** Look as soon as any look in flight is done, whatever the gap. */
	now: () => void;
	/** Somebody else just read this: pace the next look from now. */
	mark: () => void;
	/** Give up the timer, the listener and the stream. */
	stop: () => void;
};

export function poll(watch: Watch): Poller {
	const { ask, ready, kinds, pace, gap = 0 } = watch;

	let timer: ReturnType<typeof setTimeout> | undefined;
	let stopped = false;
	let asking = false;
	// A look the stream asked for that has not been made: it landed mid-look, on
	// a hidden tab, or while the page was not ready. Kept, since the poller's own
	// next look is minutes out while the stream is up.
	let owed = false;
	// One that must not wait out the gap either.
	let urgent = false;
	// When the last look was asked for. Starts now, since the page's loader has
	// just read the same thing.
	let asked = Date.now();

	/** How long until the next look, or null for one nothing is waiting on. */
	function due(): number | null {
		if (urgent) return 0;
		const soonest = Math.max(0, asked + gap - Date.now());
		if (owed) return soonest;
		return pace ? Math.max(pace(), soonest) : null;
	}

	// What a pass that made no look waits: a window, not a past deadline it
	// would spin against. What it owed is kept.
	function rest(): number {
		return Math.max(gap, pace?.() ?? 0);
	}

	// Clears before it sets, so only one timer is ever alive.
	function arm(after: number | null = due()) {
		clearTimeout(timer);
		timer = undefined;
		if (stopped || after === null) return;
		timer = setTimeout(tick, after);
	}

	async function tick() {
		// A hidden tab wastes a phone's battery; the wake catches it up.
		if (document.visibilityState !== 'visible' || (ready && !ready())) {
			arm(rest());
			return;
		}
		owed = false;
		urgent = false;
		asked = Date.now();
		asking = true;
		try {
			await ask();
		} finally {
			asking = false;
			arm();
		}
	}

	function prod() {
		owed = true;
		// One landing mid-look is owed; the look in flight re-arms and finds it.
		if (!asking) arm();
	}

	function now() {
		urgent = true;
		prod();
	}

	// Coming back to a tab is when the answer is most likely stale and about to
	// be read.
	const wake = () => {
		if (document.visibilityState === 'visible') now();
	};

	const heard = kinds?.length ? subscribe(kinds, prod) : undefined;
	document.addEventListener('visibilitychange', wake);
	arm();

	return {
		prod,
		now,
		mark: () => {
			asked = Date.now();
			owed = false;
			urgent = false;
			if (!asking) arm();
		},
		stop: () => {
			stopped = true;
			clearTimeout(timer);
			timer = undefined;
			heard?.();
			document.removeEventListener('visibilitychange', wake);
		}
	};
}
