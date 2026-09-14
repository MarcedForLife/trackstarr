// A panel hung off the button that opened it. Four controls ask this way (the
// sweep's pair, the two row menus, Stop all), so the clamping and the focus
// handoff live here rather than in each.

import { tick } from 'svelte';
import { overlay } from '$lib/overlay';

// Kept clear of the window edges, and off the trigger.
const MARGIN = 12;
const GAP = 8;

type Options = {
	/** Which trigger edge the panel lines up with. A control at the right of its
	 * row wants `right`, or the clamp shoves the panel away from it. */
	edge?: 'left' | 'right';
	/** Run as it closes, for a caller keeping its own record of what is up. */
	onclose?: () => void;
};

export type Popover = {
	readonly open: boolean;
	readonly left: number;
	readonly top: number;
	/** Whether this panel is the one up, for a caller with several. */
	holds: (candidate: HTMLElement | undefined) => boolean;
	raise: (trigger: HTMLElement, panel: HTMLElement) => Promise<void>;
	toggle: (trigger: HTMLElement, panel: HTMLElement) => Promise<void>;
	lower: () => void;
	/** Measure again after the panel's contents change size. */
	place: () => void;
	/** Whether a press landed on neither the panel nor its trigger. */
	outside: (target: Node) => boolean;
};

export function popover({ edge = 'right', onclose }: Options = {}): Popover {
	let open = $state(false);
	let left = $state(0);
	let top = $state(0);
	let trigger = $state<HTMLElement | null>(null);
	let panel = $state<HTMLElement | null>(null);

	// Unnamed, so it takes no history entry. A question two taps wide should not
	// cost a back press, and Escape still answers it before the page under it.
	const held = overlay({
		close: () => {
			open = false;
			panel?.hidePopover();
			trigger?.focus();
			onclose?.();
		}
	});

	function place() {
		if (!trigger || !panel) return;
		const rect = trigger.getBoundingClientRect();
		const from = edge === 'right' ? rect.right - panel.offsetWidth : rect.left;
		left = Math.max(MARGIN, Math.min(from, innerWidth - panel.offsetWidth - MARGIN));
		top = Math.max(MARGIN, Math.min(rect.bottom + GAP, innerHeight - panel.offsetHeight - MARGIN));
	}

	async function raise(nextTrigger: HTMLElement, nextPanel: HTMLElement) {
		// Swapping panels within one overlay, so the old one goes without a close
		// the new one would undo in the same breath.
		if (panel && panel !== nextPanel) panel.hidePopover();
		trigger = nextTrigger;
		panel = nextPanel;
		open = true;
		held.raise();
		await tick();
		nextPanel.showPopover();
		// Shown first, since a hidden popover measures zero and its size decides
		// where it goes.
		place();
		nextPanel.querySelector('button')?.focus();
	}

	async function toggle(nextTrigger: HTMLElement, nextPanel: HTMLElement) {
		if (open && panel === nextPanel) held.lower();
		else await raise(nextTrigger, nextPanel);
	}

	return {
		get open() {
			return open;
		},
		get left() {
			return left;
		},
		get top() {
			return top;
		},
		holds: (candidate) => open && !!candidate && panel === candidate,
		raise,
		toggle,
		lower: () => void held.lower(),
		place,
		outside: (target) => !panel?.contains(target) && !trigger?.contains(target)
	};
}
