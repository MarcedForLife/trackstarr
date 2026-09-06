// Drag a row sideways with a mouse, the way a map drags. A finger already pans
// the row through the browser, and would fight this and lose: the compositor
// scrolls without the main thread. Mouse only.

import { reduced } from '$lib/motion.svelte';

// How far a cursor may move before a click becomes a drag. The cards' tap slop.
const SLOP = 6;

// How much of the gap to the pointer the row closes each frame.
//
// A cursor does not report on the screen's schedule: on a 120Hz panel a third
// of frames arrive with no new position, so a row written straight from the
// last one stalls and jumps. Easing moves it every frame, at the cost of a lag
// that grows with speed (5px reading, 16px flicking). The release goes straight
// to the cursor. Swept at 70Hz on 120Hz, 0.45 stalled on no frames and ran 5px
// behind; 0.55 is past the knee and stalled on a third.
const EASE = 0.45;

// Near enough to have arrived.
const ARRIVED = 0.25;

export function dragScroll(node: HTMLElement) {
	let from = 0;
	let at = 0;
	let dragging = false;
	// Whether the last gesture was a drag, so the click under it is swallowed.
	let dragged = false;
	let pointer = -1;
	// Where the pointer says the row should be, and where it is shown. One write
	// per frame, not per event: every scrollLeft write is a full scroll.
	let want = 0;
	let shown = 0;
	// How far the row can travel, read at the press: a target past the end is
	// one the ease never reaches.
	let span = 0;
	let frame = 0;

	// Where the nearest card starts, in the row's scroll coordinates.
	function nearestCard() {
		const port = node.getBoundingClientRect().left + node.clientLeft;
		let best = node.scrollLeft;
		let closest = Infinity;
		for (const child of node.children) {
			const target = node.scrollLeft + (child.getBoundingClientRect().left - port);
			const away = Math.abs(target - node.scrollLeft);
			if (away < closest) {
				closest = away;
				best = target;
			}
		}
		return Math.min(span, Math.max(0, best));
	}

	function apply() {
		frame = 0;
		const gap = want - shown;
		shown = Math.abs(gap) < ARRIVED ? want : shown + gap * EASE;
		node.scrollLeft = shown;
		// Keep going until the row has caught up.
		if (dragging && shown !== want) frame = requestAnimationFrame(apply);
	}

	function down(event: PointerEvent) {
		if (event.pointerType !== 'mouse' || event.button !== 0) return;
		// Nothing to drag.
		if (node.scrollWidth <= node.clientWidth) return;
		pointer = event.pointerId;
		from = event.clientX;
		at = node.scrollLeft;
		shown = at;
		span = node.scrollWidth - node.clientWidth;
		dragging = false;
		dragged = false;
	}

	function move(event: PointerEvent) {
		if (event.pointerId !== pointer) return;
		const travelled = event.clientX - from;
		if (!dragging) {
			if (Math.abs(travelled) < SLOP) return;
			dragging = true;
			dragged = true;
			// Captured now, not on the press, so an ordinary click keeps its
			// pointer.
			node.setPointerCapture(pointer);
			node.style.cursor = 'grabbing';
			node.style.userSelect = 'none';
		}
		// Clamped here: the ease chases this number.
		want = Math.min(span, Math.max(0, at - travelled));
		if (!frame) frame = requestAnimationFrame(apply);
	}

	function up(event: PointerEvent) {
		if (event.pointerId !== pointer) return;
		pointer = -1;
		if (dragging) {
			// Straight to where the pointer left it.
			if (frame) cancelAnimationFrame(frame);
			frame = 0;
			shown = want;
			node.scrollLeft = want;
			// Then glide onto the nearest card, in place of the coast a finger gets.
			settle();
		}
		dragging = false;
		node.style.cursor = '';
		node.style.userSelect = '';
	}

	function settle() {
		const target = nearestCard();
		if (target === node.scrollLeft) return;
		// A reader who asked for less motion gets the jump.
		if (reduced()) {
			node.scrollLeft = target;
			return;
		}
		node.scrollTo({ left: target, behavior: 'smooth' });
	}

	// The click after a drag lands on whatever poster the cursor stopped over.
	// Capture, so it is swallowed before it reaches the card.
	function click(event: MouseEvent) {
		if (!dragged) return;
		dragged = false;
		event.preventDefault();
		event.stopPropagation();
	}

	node.addEventListener('pointerdown', down);
	node.addEventListener('pointermove', move);
	node.addEventListener('pointerup', up);
	node.addEventListener('pointercancel', up);
	node.addEventListener('click', click, { capture: true });

	return {
		destroy() {
			if (frame) cancelAnimationFrame(frame);
			node.removeEventListener('pointerdown', down);
			node.removeEventListener('pointermove', move);
			node.removeEventListener('pointerup', up);
			node.removeEventListener('pointercancel', up);
			node.removeEventListener('click', click, { capture: true });
		}
	};
}
