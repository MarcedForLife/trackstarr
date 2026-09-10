// Grow the space a panel opens into without a layout. The reveal in layout.css
// takes the box's full height in one step, so the panel arrives into a hole
// already cut for it. Instead everything after the panel starts a panel-height
// above its laid-out position and travels down on the reveal's own curve. All
// transforms, so it stays on the compositor.

// How far past the fold to keep moving things, so the join between moving and
// still is off screen without a long list paying for every row.
const SPARE = 400;

/** Milliseconds from a computed CSS time, which is seconds unless it says otherwise. */
function ms(time: string): number {
	const value = parseFloat(time);
	return time.trim().endsWith('ms') ? value : value * 1000;
}

/** Everything the panel pushed down: the elements after it at every level. */
function under(node: HTMLElement): HTMLElement[] {
	// A sibling starting above the panel's foot is beside it, in a flex row.
	const foot = node.getBoundingClientRect().bottom - 1;
	const fold = window.innerHeight + SPARE;
	const moving: HTMLElement[] = [];

	for (let at: Element | null = node; at && at !== document.body; at = at.parentElement) {
		for (let next = at.nextElementSibling; next; next = next.nextElementSibling) {
			if (!(next instanceof HTMLElement)) continue;
			const box = next.getBoundingClientRect();
			// Document order runs down the page, so the first thing off the bottom
			// ends the walk.
			if (box.top > fold) return moving;
			if (box.top < foot || !box.height) continue;
			// Out of the flow, so the panel did not move it: the fixed tab bar.
			const placed = getComputedStyle(next).position;
			if (placed !== 'fixed' && placed !== 'absolute') moving.push(next);
		}
	}
	return moving;
}

export function shift(node: HTMLElement) {
	// The clipping window, where the reveal's animation runs.
	const clip = node.firstElementChild;
	if (!clip) return;

	// Read off the element running it, so the two agree every frame.
	const timing = getComputedStyle(clip);
	const span = ms(timing.animationDuration);
	// Reduced motion crushes the duration to nothing.
	if (span < 1) return;

	// The border box, not the margin box: the margin belongs to the open state
	// and appears with it.
	const height = node.getBoundingClientRect().height;
	if (height < 1) return;

	const moving = under(node).map((element) =>
		element.animate([{ transform: `translateY(${-height}px)` }, { transform: 'none' }], {
			duration: span,
			easing: timing.animationTimingFunction
		})
	);

	return {
		destroy() {
			// Shut mid-open: the space collapses with the panel.
			for (const run of moving) run.cancel();
		}
	};
}
