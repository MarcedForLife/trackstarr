// One owner for the history entries overlays stand on, and for Escape.
//
// Everything put over a page (the title sheet, the picking bar, the nav drawer,
// a popover) should be closed by the back gesture rather than the page. Each
// used to push its own entry and answer every popstate, so one back press could
// close two things and leave a spent entry. Now what is up is a stack, and one
// popstate and one keydown listener answer only its top.

import { onDestroy } from 'svelte';
import { pushState, replaceState } from '$app/navigation';
import { page } from '$app/state';

// The flags in App.PageState, which is what an entry of ours carries.
type Name = keyof App.PageState;

type Held = {
	name?: Name;
	// Whether this one stands on a history entry. Decided as it is raised: the
	// same overlay can come up on a press or on its own account.
	entry: boolean;
	close: () => void;
	escape: () => void;
};

export type Raised = {
	// The flag its entry carries. No name means no entry: a popover inside a
	// sheet should not cost a back press.
	name?: Name;
	// Its entry has gone: put it away, touching no history.
	close: () => void;
	// Escape, where it means something other than lower(): a bar showing a
	// running progress bar has nothing to dismiss.
	onescape?: () => void;
};

export type Overlay = {
	/** Raise it. `entry` is false when it came up on its own account, as an
	 * adopted run's bar does; back must not answer for that, Escape still does. */
	raise: (entry?: boolean) => void;
	/** Lower it, through the entry when it owns the top one. Answers whether an
	 * entry was spent, since the pop restores that entry's scroll position. */
	lower: () => boolean;
};

const stack: Held[] = [];
let listening = false;

// The current entry's state with one flag set or cleared, keeping the others.
function flagged(name: Name, on: boolean): App.PageState {
	const state: App.PageState = { ...page.state };
	if (on) state[name] = true;
	else delete state[name];
	return state;
}

function popped() {
	// The popped entry belongs to the topmost overlay that took one; anything
	// raised over it goes too.
	while (stack.length) {
		const top = stack.pop();
		if (!top) break;
		top.close();
		if (top.entry) break;
	}
	settle();
}

function pressed(event: KeyboardEvent) {
	if (event.key === 'Escape') stack.at(-1)?.escape();
}

function listen() {
	if (listening) return;
	listening = true;
	window.addEventListener('popstate', popped);
	window.addEventListener('keydown', pressed);
}

// Nothing up, nothing listening: the pages answer their own keys, and a
// popstate that is a navigation is not ours.
function settle() {
	if (!listening || stack.length) return;
	listening = false;
	window.removeEventListener('popstate', popped);
	window.removeEventListener('keydown', pressed);
}

export function overlay({ name, close, onescape }: Raised): Overlay {
	const held: Held = {
		name,
		entry: false,
		close,
		escape: () => (onescape ? onescape() : lower())
	};

	// Whether the component that made this one has gone.
	let gone = false;

	function drop() {
		const at = stack.indexOf(held);
		if (at >= 0) stack.splice(at, 1);
		settle();
	}

	function raise(entry = true) {
		// A page left with a fetch in flight is no longer this stack's business.
		if (gone) return;
		const wants = entry && !!name;
		const at = stack.indexOf(held);
		if (at >= 0) {
			// Already up: a second poster tapped into an open sheet must not cost a
			// second back press. Except one that came up on its own account and is
			// now the reader's, which takes the entry it lacked while still on top.
			if (!wants || held.entry || at !== stack.length - 1) return;
		} else {
			stack.push(held);
			listen();
		}
		held.entry = wants;
		if (wants && name) pushState('', flagged(name, true));
	}

	function lower(): boolean {
		const at = stack.indexOf(held);
		if (at < 0) return false;
		// Through the entry, or it would eat the next back press; the pop closes it.
		if (held.entry && at === stack.length - 1) {
			history.back();
			return true;
		}
		drop();
		// Buried under something raised later: its own entry is out of reach.
		if (name && page.state[name]) replaceState('', flagged(name, false));
		close();
		return false;
	}

	// A navigation unmounts the page's overlays, leaving their entries behind
	// where nothing can reach them. Dropped, not closed: there is nothing left to
	// put away. Registered here because onDestroy only works during init.
	onDestroy(() => {
		gone = true;
		drop();
	});

	return { raise, lower };
}
