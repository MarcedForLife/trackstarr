// Cards lean toward the pointer, not only the one under it.
//
// `:hover` cannot do this: on a desktop the grid should respond around the
// cursor, and on a phone the browser cancels the pointer once a touch becomes
// a scroll. Touch events keep arriving through a scroll, so they drive it.
//
// Only cards on screen and near the pointer are written to. Driving every card
// from one inherited custom property cost 1.9ms a frame in style recalc on a
// 400 poster grid.
//
// One piece of geometry drives three things: how far a card leans, the light
// across it, and, while a pointer is held anywhere, which card is raised. The
// raise follows the pointer, not the card the press began on.

// The classes and properties written below. Imported here so both halves of
// the mechanism are read together.
import './poster.css';

// Degrees a card leans with the pointer at its edge, at full strength.
const MAX_TILT = 13;

// Default reach in card widths from a card's centre. The caller's figure is the
// Appearance setting, read every frame.
const REACH = 1.6;

// The eye's distance from a card, in card widths. A multiple, since perspective
// only means anything against the size of what it looks at: a fixed 900px made
// thirteen degrees two pixels of keystone on a phone.
const DEPTH = 3.6;

// How quickly the sheen forgets the last movement.
const DECAY = 0.86;

// What movement contributes to the sheen at its brightest, per pixel per frame.
const SPEED_GAIN = 0.02;

// How much of the gap to its target light a card closes each frame: about a
// third of a second to settle.
const CATCH = 0.14;

// How far past a card's box the pointer may be and still raise it, as a
// fraction of its half-size, so a thumb crossing a gutter does not drop it.
const SPILL = 1.08;

// Pointers held on a poster anywhere on the page. A count, so two fingers on
// two posters are two presses.
let holds = 0;

// One waker per live field, so a press that never moves still raises a card and
// a release still lowers it.
const fields = new Set<() => void>();

/** A pointer held on a poster, from the card that owns the gesture. */
export function pressing(on: boolean) {
	const was = holds > 0;
	holds = Math.max(0, holds + (on ? 1 : -1));
	if (was === holds > 0) return;
	for (const wake of fields) wake();
}

// A card in the field, with its centre in content coordinates (page scroll on
// one axis, container scroll on the other), so a scroll costs no re-measure and
// one field drives both a page-scrolled grid and a self-scrolling row.
type Near = {
	tilt: HTMLElement;
	// The artwork the light is painted on. Separate because the sheen properties
	// are `inherits: false`: written here they invalidate one element.
	art: HTMLElement;
	cx: number;
	cy: number;
	hw: number;
	hh: number;
	// Whether the card is leaning, so a flat card is not rewritten every frame.
	on: boolean;
	// The light it carries now, chasing the light it should have.
	lit: number;
};

// Smooth at both ends, so a card eases into the field.
function falloff(ratio: number) {
	if (ratio >= 1) return 0;
	const near = 1 - ratio;
	return near * near * (3 - 2 * near);
}

function clamp(value: number) {
	return Math.min(1, Math.max(-1, value));
}

// A pointer the caller moves itself. Appearance's preview strip sweeps one so
// the tilt settings can be seen on a phone, which has no hover.
export type Driver = {
	// Viewport coordinates, as a real pointer arrives in.
	aim: (x: number, y: number) => void;
	away: () => void;
};

export function tiltField(
	node: HTMLElement,
	opts: {
		active: () => boolean;
		reach?: () => number;
		strength?: () => number;
		lights?: () => boolean;
		drive?: (driver: Driver) => void;
	}
) {
	const { active, reach: chosen, strength, lights, drive } = opts;
	const cards = new Map<Element, Near>();
	// The pointer in viewport coordinates, and whether there is one to follow.
	let px = 0;
	let py = 0;
	let live = false;
	// The decaying movement term behind the sheen, shared by every card near the
	// pointer: one flick, one flash.
	let speed = 0;
	// The card raised out of the grid, if a pointer is held over one.
	let up: Near | null = null;
	// Whether a finger or a mouse button is down, which is what tells a scroll
	// the reader is driving from a wheel scroll.
	let touching = false;
	let held = false;
	let frame = 0;
	// Whether this press has scrolled anything. Set by the first scroll it
	// causes, cleared when the pointer lifts.
	let dragging = false;

	// Re-measure a card in place, keeping the angle and light it carries.
	function place(near: Near, el: Element) {
		const box = el.getBoundingClientRect();
		near.cx = box.left + box.width / 2 + node.scrollLeft;
		near.cy = box.top + box.height / 2 + window.scrollY;
		near.hw = box.width / 2;
		near.hh = box.height / 2;
	}

	function measure(el: Element): Near | null {
		const tilt = el.querySelector<HTMLElement>('[data-tilt]');
		if (!tilt) return null;
		const near: Near = {
			tilt,
			// The card itself when there is no artwork to light.
			art: tilt.querySelector<HTMLElement>('[data-sheen]') ?? tilt,
			cx: 0,
			cy: 0,
			hw: 0,
			hh: 0,
			on: false,
			lit: 0
		};
		place(near, el);
		return near;
	}

	// Only cards on screen are in the map, so a library of three thousand costs
	// the same as one of thirty. The margin measures a card just before it
	// scrolls into reach.
	const seen = new IntersectionObserver(
		(entries) => {
			// All writes, then all reads. Interleaved, each flattened card
			// invalidated the layout the next rect read had to rebuild.
			for (const entry of entries) {
				if (entry.isIntersecting) continue;
				const going = cards.get(entry.target);
				if (going === up) lower();
				rest(going);
				// Off screen is the one place the transform can go without the
				// card being seen to change.
				forget(going);
				cards.delete(entry.target);
			}
			for (const entry of entries) {
				if (!entry.isIntersecting) continue;
				const near = measure(entry.target);
				if (near) cards.set(entry.target, near);
			}
			request();
		},
		{ rootMargin: '150px' }
	);

	function rest(near: Near | undefined) {
		if (!near?.on) return;
		near.on = false;
		// Removing the class restores the transition, which eases the card home.
		near.tilt.classList.remove('is-near');
		near.tilt.style.setProperty('--rx', '0deg');
		near.tilt.style.setProperty('--ry', '0deg');
		near.art.style.setProperty('--sheen', '0');
		// `is-turning` stays. See forget().
	}

	/**
	 * Move a card's light a step toward its target; whether it has further to go.
	 *
	 * Not a CSS transition on --sheen: a transition on a registered custom
	 * property is re-resolved for every element every frame, which doubled
	 * style recalc on a 120 poster shelf. Exponential, so a light that reverses
	 * mid-fade turns around where it is.
	 */
	function catchUp(near: Near, want: number) {
		const gap = want - near.lit;
		if (Math.abs(gap) < 0.002) {
			if (near.lit === want) return false;
			near.lit = want;
			near.art.style.setProperty('--sheen', `${want}`);
			return false;
		}
		near.lit += gap * CATCH;
		near.art.style.setProperty('--sheen', `${near.lit}`);
		return true;
	}

	// Raise one card and lower the previous one.
	function raise(near: Near | null) {
		if (near === up) return;
		up?.tilt.classList.remove('is-raised');
		near?.tilt.classList.add('is-raised');
		up = near;
	}

	function lower() {
		raise(null);
	}

	/**
	 * Remove the 3D transform, and with it the card's composited layer.
	 *
	 * Only off screen, from the observer: dropping the layer re-rasterises the
	 * cover straight into the page, and at a fractional pixel ratio (2.625 on a
	 * desktop at 150% or most phones) that reads as the picture shifting a pixel
	 * inside a still frame. So the layer stays while the card is on screen, one
	 * per card the pointer has been near, rather than promoting every card.
	 */
	function forget(near: Near | undefined) {
		if (!near) return;
		near.tilt.classList.remove('is-turning');
	}

	function draw() {
		frame = 0;
		const top = window.scrollY;
		// Zero for a grid, which never scrolls sideways.
		const left = node.scrollLeft;
		const spread = chosen?.() ?? REACH;
		// Read every frame so the Appearance slider takes effect as it moves.
		// Zero never gets here; the field is inactive at that stop.
		const gain = strength?.() ?? 1;
		const glossy = lights?.() ?? true;
		speed *= DECAY;
		// The card under a held pointer, chosen across the loop so a pointer on
		// the seam between two tiles raises the nearer.
		let under: Near | null = null;
		let nearest = Infinity;
		// Whether any card's light is still travelling, which keeps frames coming
		// after the pointer stops.
		let fading = false;
		for (const near of cards.values()) {
			if (!live) {
				rest(near);
				fading = catchUp(near, 0) || fading;
				continue;
			}
			const dx = px - (near.cx - left);
			const dy = py - (near.cy - top);
			// Judged by the card's box, not the field: a card at the edge of a
			// narrow spread barely leans but a press on it should still raise it.
			if (holds) {
				const bite = Math.max(Math.abs(dx) / near.hw, Math.abs(dy) / near.hh);
				if (bite <= SPILL && bite < nearest) {
					nearest = bite;
					under = near;
				}
			}
			const reach = near.hw * 2 * spread;
			const pull = falloff(Math.hypot(dx, dy) / reach);
			if (pull <= 0) {
				rest(near);
				fading = catchUp(near, 0) || fading;
				continue;
			}
			if (!near.on) {
				near.on = true;
				// No transition while the pointer drives it, or the card trails
				// the cursor.
				near.tilt.classList.add('is-turning', 'is-near');
				near.tilt.style.setProperty('--depth', `${near.hw * 2 * DEPTH}px`);
			}
			// The pointer's position over the card in half-widths, clamped to its
			// edge, so inside a card the field changes nothing.
			const overX = clamp(dx / near.hw);
			const overY = clamp(dy / near.hh);
			near.tilt.style.setProperty('--ry', `${overX * MAX_TILT * gain * pull}deg`);
			near.tilt.style.setProperty('--rx', `${-overY * MAX_TILT * gain * pull}deg`);
			if (!glossy) {
				fading = catchUp(near, 0) || fading;
				continue;
			}
			// The lean is a compositor matrix; the light rebuilds the artwork's
			// gradients and repaints, six times the cost of everything else. Not
			// written while the row slides, when nobody can follow a highlight
			// anyway; it catches up when the row stops.
			if (dragging) continue;
			// The highlight sits under the pointer and stops at the card's edge.
			near.art.style.setProperty('--mx', `${(1 + overX) * 50}%`);
			near.art.style.setProperty('--my', `${(1 + overY) * 50}%`);
			// The hue follows the lean's direction: cool one way, warm the other.
			near.art.style.setProperty('--hue', `${180 + overX * 110 + overY * 45}`);
			// How far from flat the card is, 0 at rest and 1 at a corner, plus what
			// is left of the movement. From the pivot, or a card the falloff has
			// flattened would go on catching the light.
			const pivot = Math.min(1, Math.hypot(overX, overY) * pull);
			fading = catchUp(near, Math.min(1, (pivot * 0.45 + speed * pull) * gain)) || fading;
		}
		raise(under);
		// Keep going while the flash has anything left to show or any light is
		// still settling.
		if ((live && speed > 0.01) || fading) request();
	}

	function request() {
		if (!frame) frame = requestAnimationFrame(draw);
	}

	// Nothing to lean at. The position is kept for a pointer that comes back.
	function sleep() {
		live = false;
		speed = 0;
		request();
	}

	function aim(x: number, y: number) {
		if (!active()) {
			if (live) sleep();
			return;
		}
		// Only against a position this pointer was at: the first move after a
		// sleep may be from the other side of the page.
		if (live) speed = Math.min(0.55, speed + Math.hypot(x - px, y - py) * SPEED_GAIN);
		px = x;
		py = y;
		live = true;
		request();
	}

	function onPointer(event: PointerEvent) {
		// Touch is followed through its own events, the only stream that survives
		// a scroll; a pen behaves like a mouse.
		if (event.pointerType === 'touch') return;
		held = event.buttons > 0;
		// The release ends the drag; its frame catches the light up.
		if (!held) dragging = false;
		aim(event.clientX, event.clientY);
	}

	function onTouch(event: TouchEvent) {
		touching = true;
		const finger = event.touches[0];
		if (finger) aim(finger.clientX, finger.clientY);
	}

	function away() {
		touching = false;
		dragging = false;
		sleep();
	}

	// A scroll moves the cards past a still pointer. A finger or a held button
	// is driving it, so the cards keep leaning toward the pointer. A wheel is
	// not: following a parked cursor promotes every card the reach sweeps to
	// its own layer and drops it again, half a frame budget in layer assignment,
	// so the field sleeps until the next pointermove.
	function onScroll() {
		if (touching || held) {
			dragging = true;
			request();
			return;
		}
		if (live) sleep();
	}

	// Re-measure every card on screen, dropping any the grid has removed.
	function remeasure() {
		for (const [el, near] of cards) {
			if (el.isConnected) place(near, el);
			else cards.delete(el);
		}
		request();
	}

	function onResize() {
		// Writes first, then reads, as in the observer above.
		lower();
		for (const near of cards.values()) {
			rest(near);
			near.lit = 0;
			forget(near);
		}
		remeasure();
	}

	// The grid changing shape without a window resize: a poster size change or
	// the sidebar folding. Growing taller is not that, and re-measuring on it
	// cost 17% of a long scroll, so only the width and the requested scale
	// count. The scale is read off the inline style, since a tile under
	// `content-visibility` reports its size hint rather than its layout.
	let width = 0;
	let scale = '';
	const reshaped = new ResizeObserver(() => {
		const asked = node.style.getPropertyValue('--scale');
		if (node.clientWidth === width && asked === scale) return;
		width = node.clientWidth;
		scale = asked;
		onResize();
	});
	reshaped.observe(node);

	// The grid's contents changing, which neither observer above sees. Tiles
	// appended below leave every card in place, so they are only watched.
	// Anything removed means a filter or sort rebuilt the list and the survivors
	// moved. A child count could not tell: a page of 36 cut to another 36 read
	// as no change.
	const restocked = new MutationObserver((records) => {
		let cut = false;
		for (const record of records) {
			for (const added of record.addedNodes) if (added instanceof Element) seen.observe(added);
			cut ||= record.removedNodes.length > 0;
		}
		if (cut) relayout();
	});

	// A surviving card slides to its new place, and the mutation lands before
	// the slide's first frame, so rects read where it is leaving from. Right for
	// that frame, stale once the slide ends, so it is measured again then. Only
	// the geometry the second time: the reader may have leaned a card meanwhile.
	function relayout() {
		onResize();
		// The slides are looked for a frame on, not here: Svelte starts them after
		// the mutation this runs from, and what a tile carries now is its finished
		// arrival fade, still filling. Waiting on that settled in the same frame,
		// which left every card measured where it set off from, so the field went
		// on leaning covers the reader had moved on from.
		requestAnimationFrame(() => {
			const sliding = node
				.getAnimations({ subtree: true })
				.filter((slide) => slide.playState !== 'finished');
			if (!sliding.length) return remeasure();
			Promise.allSettled(sliding.map((slide) => slide.finished)).then(remeasure);
		});
	}

	for (const child of node.children) seen.observe(child);
	restocked.observe(node, { childList: true });
	// A row sliding under a finger is the same gesture as the page doing so. A
	// grid never fires this.
	node.addEventListener('scroll', onScroll, { passive: true });
	window.addEventListener('pointermove', onPointer, { passive: true });
	// A press is a position too, or a cursor that pressed without moving would
	// raise whatever the field last saw.
	window.addEventListener('pointerdown', onPointer, { passive: true });
	// The release clears `held`; a cursor that stops moving sends nothing else.
	window.addEventListener('pointerup', onPointer, { passive: true });
	window.addEventListener('touchstart', onTouch, { passive: true });
	window.addEventListener('touchmove', onTouch, { passive: true });
	window.addEventListener('touchend', away, { passive: true });
	window.addEventListener('touchcancel', away, { passive: true });
	window.addEventListener('scroll', onScroll, { passive: true });
	window.addEventListener('resize', onResize, { passive: true });
	// The cursor leaving the window sends no pointermove.
	document.addEventListener('pointerleave', away);
	// A card anywhere on the page saying a press began or ended.
	fields.add(request);

	// Once the listeners are up, so a driver may start at once. A real pointer
	// still wins: both go through `aim`, and the last call before a frame draws.
	drive?.({ aim, away });

	return {
		destroy() {
			seen.disconnect();
			reshaped.disconnect();
			restocked.disconnect();
			cancelAnimationFrame(frame);
			fields.delete(request);
			node.removeEventListener('scroll', onScroll);
			window.removeEventListener('pointermove', onPointer);
			window.removeEventListener('pointerdown', onPointer);
			window.removeEventListener('pointerup', onPointer);
			window.removeEventListener('touchstart', onTouch);
			window.removeEventListener('touchmove', onTouch);
			window.removeEventListener('touchend', away);
			window.removeEventListener('touchcancel', away);
			window.removeEventListener('scroll', onScroll);
			window.removeEventListener('resize', onResize);
			document.removeEventListener('pointerleave', away);
			lower();
			for (const near of cards.values()) {
				rest(near);
				forget(near);
			}
		}
	};
}
