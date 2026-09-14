// Grow the space a panel opens into without a layout. The reveal in layout.css
// takes the box's full height in one step, so the panel arrives into a hole
// already cut for it. Instead everything the panel pushed down starts back
// where it stood and travels to its new place on the reveal's own curve. All
// transforms, so it stays on the compositor.
//
// The same two transforms uncover whatever the panel gains later, a file's log
// or its saved plan, so a late answer does not drop the rows below it.

import { carry, offsetOf } from '$lib/carry';

// How far past the fold to keep moving things, so the join between moving and
// still is off screen without a long list paying for every row.
const SPARE = 400;

/** Milliseconds from a computed CSS time, which is seconds unless it says otherwise. */
function ms(time: string): number {
	const value = parseFloat(time);
	return time.trim().endsWith('ms') ? value : value * 1000;
}

/** The boxes after the panel at every level, as far as a screenful past the fold. */
function following(node: HTMLElement): HTMLElement[] {
	const fold = window.innerHeight + SPARE;
	const found: HTMLElement[] = [];

	for (let at: Element | null = node; at && at !== document.body; at = at.parentElement) {
		for (let next = at.nextElementSibling; next; next = next.nextElementSibling) {
			if (!(next instanceof HTMLElement)) continue;
			const box = next.getBoundingClientRect();
			if (!box.height) continue;
			// Where it was laid out, not where a carry in flight is holding it.
			const top = box.top - offsetOf(getComputedStyle(next).transform);
			// Document order runs down the page, so the first thing off the bottom
			// ends the walk.
			if (top > fold) return found;
			found.push(next);
		}
	}
	return found;
}

export function shift(node: HTMLElement) {
	// The clipping window, where the reveal's animation runs, and the panel
	// inside it.
	const opening = node.firstElementChild;
	const panel = opening?.firstElementChild;
	if (!(opening instanceof HTMLElement) || !(panel instanceof HTMLElement)) return;
	// Stated again, since the narrowing above does not reach the closures below.
	const clip: HTMLElement = opening;
	const inner: HTMLElement = panel;

	// Kept, the clip cuts off anything a child draws outside its bounds, focus
	// rings and the palette's selected swatch both being 2px clear. So it goes
	// once nothing is uncovering.
	let opened = false;
	let covering = 0;

	function letGo() {
		node.style.overflow = 'visible';
		clip.style.overflow = 'visible';
	}

	// Reduced motion crushes the duration rather than removing the animation,
	// so this still fires. A child's own animation bubbles here too.
	function ran(event: AnimationEvent) {
		if (event.target !== clip) return;
		opened = true;
		if (!covering) letGo();
	}
	clip.addEventListener('animationend', ran);

	// Read off the element running it, so the two agree every frame.
	const timing = getComputedStyle(clip);
	const span = ms(timing.animationDuration);
	const easing = timing.animationTimingFunction;

	let height = node.getBoundingClientRect().height;

	// Anything that would clamp its scroll while the panel is collapsed for a
	// measurement, and be left there once it is back.
	const scrollers: HTMLElement[] = [];
	for (let at = node.parentElement; at; at = at.parentElement) {
		const spills = getComputedStyle(at).overflowY;
		if (spills === 'auto' || spills === 'scroll') scrollers.push(at);
	}
	if (document.scrollingElement instanceof HTMLElement) scrollers.push(document.scrollingElement);

	/** How far the panel's space has moved each of them, measured with it taking
	 * none: a grid cell beside a taller one never moved, whatever its place in
	 * the document says. */
	function drops(boxes: HTMLElement[]): number[] {
		const held = scrollers.map((element) => element.scrollTop);
		const now = boxes.map((element) => element.getBoundingClientRect().top);
		node.style.height = '0';
		node.style.marginTop = '0';
		node.style.marginBottom = '0';
		const without = boxes.map((element) => element.getBoundingClientRect().top);
		node.style.height = '';
		node.style.marginTop = '';
		node.style.marginBottom = '';
		scrollers.forEach((element, at) => (element.scrollTop = held[at]));
		return now.map((top, at) => top - without[at]);
	}

	// How far each box has been carried for already, so a later gain moves it by
	// the difference rather than by the panel's whole height, and the run doing
	// it, since only the box being taken over may be cancelled.
	const carried = new WeakMap<HTMLElement, number>();
	const moving = new Map<HTMLElement, Animation>();

	/** Everything the panel moved travels back to where it stood, each taking
	 * over from where it is so a second move mid-travel carries on. */
	function follow() {
		const boxes = following(node);
		const drop = drops(boxes);
		boxes.forEach((element, at) => {
			const by = drop[at] - (carried.get(element) ?? 0);
			if (Math.abs(by) < 1) return;
			carried.set(element, drop[at]);
			const was = moving.get(element);
			const run = carry(element, -by, span, easing);
			was?.cancel();
			moving.set(element, run);
		});
	}

	/** Uncover what the panel gained the way the reveal uncovered the rest: the
	 * window clips it and the panel slides up by as much, so only the clip edge
	 * travels. */
	function uncover(by: number) {
		node.style.overflow = 'hidden';
		clip.style.overflow = 'hidden';
		covering++;
		carry(clip, -by, span, easing);
		carry(inner, by, span, easing)
			.finished.catch(() => {})
			.finally(() => {
				covering--;
				if (!covering && opened) letGo();
			});
	}

	// Nothing to move, either from reduced motion or a panel that opened empty.
	if (span < 1 || height < 1)
		return { destroy: () => clip.removeEventListener('animationend', ran) };

	follow();

	const watch = new ResizeObserver(() => {
		const now = node.getBoundingClientRect().height;
		const grew = now - height;
		height = now;
		if (Math.abs(grew) < 1) return;
		if (grew > 0) uncover(grew);
		follow();
	});
	watch.observe(node);

	return {
		destroy() {
			clip.removeEventListener('animationend', ran);
			watch.disconnect();
			// Shut mid-open: the space collapses with the panel.
			for (const run of moving.values()) run.cancel();
		}
	};
}
