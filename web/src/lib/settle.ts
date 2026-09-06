// A box that animates to its new height instead of jumping. The bar over the
// library grid is pinned to the bottom, so its height is its top edge, and each
// of its states is a different number of lines. Animating height means layout
// every frame, affordable for one box once per state change. Give the box one
// element child holding everything: that is what gets measured, since a box
// being animated cannot say how tall it wants to be.

// The shortest change worth animating. Below this it reads as reflow wobble.
const NUDGE = 2;

// Duration by distance: a line appearing should not take as long as the whole
// panel being replaced.
const MIN = 150;
const MAX = 300;
const PER_PX = 1.4;

// Arriving decelerates, leaving does not; the title sheet's pair. Cubic rather
// than out-quint, which over 40px put 45% of the change in the first frame.
const GROW = 'cubic-bezier(0.33, 1, 0.68, 1)';
const SHRINK = 'cubic-bezier(0.4, 0, 0.2, 1)';

import { reduced } from '$lib/motion.svelte';

export function settle(node: HTMLElement) {
	const inner = node.firstElementChild as HTMLElement | null;
	if (!inner || typeof ResizeObserver === 'undefined') return;

	// Where the box is now. Kept, since by the time a change is observed the
	// layout is already the new one.
	let at = node.offsetHeight;
	let playing: Animation | null = null;

	// The box's padding and border around the child. Per change: padding steps
	// up at sm.
	function frame(): number {
		const style = getComputedStyle(node);
		return (
			parseFloat(style.paddingTop) +
			parseFloat(style.paddingBottom) +
			parseFloat(style.borderTopWidth) +
			parseFloat(style.borderBottomWidth)
		);
	}

	const observer = new ResizeObserver(() => {
		const to = inner.offsetHeight + frame();
		if (Math.abs(to - at) < NUDGE) return;

		// Mid-flight, carry on from where the box is.
		const from = playing ? parseFloat(getComputedStyle(node).height) : at;
		at = to;
		if (reduced()) return;

		playing?.cancel();
		// Only while it moves: a clip left on would cut off the popover below the
		// buttons.
		node.style.overflow = 'hidden';
		const run = node.animate([{ height: `${from}px` }, { height: `${to}px` }], {
			duration: Math.min(MAX, Math.max(MIN, Math.abs(to - from) * PER_PX)),
			easing: to > from ? GROW : SHRINK
		});
		playing = run;
		const done = () => {
			// A cancel is the next change taking over the clip.
			if (playing !== run) return;
			playing = null;
			node.style.overflow = '';
		};
		run.onfinish = done;
		run.oncancel = done;
	});

	observer.observe(inner);
	return {
		destroy() {
			observer.disconnect();
			playing?.cancel();
		}
	};
}
