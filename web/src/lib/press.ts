// A press that answers a finger and a cursor alike. Four gestures come out of
// one pointer: a tap that opens, a hold that raises, a long press that selects,
// and a scroll that belongs to the page. An action rather than markup handlers
// because none of it is drawn; the component only needs the press and the long
// press landing.

// Past this many pixels the gesture was never a tap, and vertically it belongs
// to the scroller.
const SLOP = 10;

// How long a finger must stay put before it counts as a press: long enough to
// sit out a flick, short enough to answer at once.
const HOLD = 140;

// How long before a press means "select" rather than "open". Android's own
// long-press timeout, so the phone's buzz lands as the gesture arms; nothing
// here vibrates on its own for the same reason.
const LONG = 500;

export type PressOptions = {
	/** A tap: pressed and let go without going anywhere. */
	ontap: () => void;
	/** The long press, let go where it was made. */
	onhold?: () => void;
	/** Whether a long press means anything now. Read when the pointer lands. */
	canHold?: () => boolean;
	/** Whether a point is still on the pressed thing. The caller's, since a
	 * poster is measured on the turned card, not the button around it. */
	within: (x: number, y: number) => boolean;
	/** The press made and ended. What it raises is the caller's business. */
	onpress?: (on: boolean) => void;
	/** Whether lifting now would mean the long press. Goes off again if the
	 * pointer wanders away. */
	onarm?: (on: boolean) => void;
};

export function pressGesture(node: HTMLElement, opts: PressOptions) {
	let options = opts;

	// Whether the press has been made.
	let pressed = false;
	// Where the gesture began, and whether it can still become a tap.
	let startX = 0;
	let startY = 0;
	let tappable = false;
	// The pending press, while a finger is down and has not moved.
	let held: ReturnType<typeof setTimeout> | null = null;

	// Where the pointer is now, for the arming timer and the release.
	let atX = 0;
	let atY = 0;
	let arming: ReturnType<typeof setTimeout> | null = null;
	// Whether the long press landed. Lasts the gesture: a pick never reverts to
	// a tap.
	let landed = false;
	// A mouse gesture the release already answered, whose click must be thrown
	// away when it arrives.
	let spent = false;

	// What kind of pointer began the gesture.
	let lastType = 'mouse';

	function disarm() {
		if (arming !== null) {
			clearTimeout(arming);
			arming = null;
		}
		if (landed) options.onarm?.(false);
		landed = false;
	}

	// Make the press.
	function raise() {
		if (pressed) return;
		pressed = true;
		options.onpress?.(true);
	}

	function start(event: PointerEvent) {
		lastType = event.pointerType;
		startX = event.clientX;
		startY = event.clientY;
		atX = event.clientX;
		atY = event.clientY;
		tappable = true;
		// One gesture only: a pick whose click the browser withheld must not
		// swallow the next one's.
		spent = false;
		// Capture the pointer so a cursor dragged off behaves like a thumb
		// sliding off: the release is answered wherever it happens.
		try {
			node.setPointerCapture(event.pointerId);
		} catch {
			// The pointer ended between the press and here.
		}
		// A caller with nothing to raise makes no press: the gesture is a tap or
		// the scroller's.
		if (options.onpress) {
			// A finger waits out HOLD, since it may be the start of a scroll; a
			// mouse button is not ambiguous.
			if (event.pointerType !== 'mouse') {
				held = setTimeout(() => {
					held = null;
					grab();
					raise();
				}, HOLD);
			} else raise();
		}
		// Both pointers on the same timer, not ctrl-click: the select button is
		// at the top of a long page either way.
		if (options.canHold?.() ?? !!options.onhold) {
			arming = setTimeout(() => {
				arming = null;
				// Wandered off while the timer ran.
				if (!options.within(atX, atY)) return;
				landed = true;
				// The tick says the press changed meaning; on a desk it is the
				// whole feedback.
				options.onarm?.(true);
			}, LONG);
		}
	}

	function move(event: PointerEvent) {
		atX = event.clientX;
		atY = event.clientY;
		// The mark follows the pointer off and back on, as the release reads it.
		if (landed) options.onarm?.(options.within(atX, atY));
		if (Math.abs(event.clientX - startX) <= SLOP && Math.abs(event.clientY - startY) <= SLOP) {
			return;
		}
		// Too far to be a tap, or a long drag would open what it ended on.
		tappable = false;
		if (pressed) return;
		// Moved before the press was made: the page is scrolling under it.
		drop();
		disarm();
	}

	function drop() {
		if (held === null) return;
		clearTimeout(held);
		held = null;
	}

	// Once pressed, the gesture is this element's: Android Chrome otherwise takes
	// the touch for a scroll after eight pixels and cancels the pointer.
	// `touch-action` cannot say this, so it takes preventDefault on a live
	// touchmove. Installed only for the life of a press, since a standing
	// non-passive listener stops the compositor scrolling the grid.
	const swallow = (event: TouchEvent) => {
		// Not cancelable means the browser has committed to scrolling.
		if (event.cancelable) event.preventDefault();
	};

	function grab() {
		node.addEventListener('touchmove', swallow, { passive: false });
	}

	function ungrab() {
		node.removeEventListener('touchmove', swallow);
	}

	// The gesture over, however it ended.
	function flatten() {
		drop();
		disarm();
		ungrab();
		tappable = false;
		if (!pressed) return;
		pressed = false;
		options.onpress?.(false);
	}

	// Tidy-up for a gesture that never started or a pointer the browser would
	// not hand over; a captured pointer hears the release wherever it happens.
	function leave(event: PointerEvent) {
		if (node.hasPointerCapture(event.pointerId)) return;
		flatten();
	}

	function release(event: PointerEvent) {
		// A finger acts on the release, not the click that may follow: Chromium
		// swallows the click for half a second after a touch drag, and waiting
		// would cost the double-tap delay. The click that does arrive lands on
		// whatever the tap opened, which is why Sheet ignores one briefly.
		const touch = event.pointerType !== 'mouse';
		const home = options.within(event.clientX, event.clientY);
		// Lifted where held is the long press; lifted elsewhere abandons it. Read
		// before flatten(), which disarms.
		const pick = landed && home;
		const tap = tappable && touch && !landed;
		// A mouse's click is still coming and will tap, which is wrong for a pick
		// (twice) or a cursor carried off (abandoned).
		if (!touch) spent = pick || !home;
		flatten();
		if (pick) options.onhold?.();
		else if (tap) options.ontap();
	}

	// Android Chrome offers the image's menu half a second into a long press and
	// cancels the pointer. Only for a finger: a right-click keeps its menu.
	function menu(event: Event) {
		if (lastType !== 'mouse') event.preventDefault();
	}

	function click(event: MouseEvent) {
		// The click after a mouse gesture the release already answered. Never a
		// keyboard's, whose Enter has detail 0.
		if (spent && event.detail !== 0) {
			spent = false;
			return;
		}
		// A mouse click, or a keyboard's Enter or Space. A finger was answered on
		// release.
		if (event.detail === 0 || lastType === 'mouse') options.ontap();
	}

	node.addEventListener('click', click);
	node.addEventListener('contextmenu', menu);
	node.addEventListener('pointerdown', start);
	node.addEventListener('pointermove', move);
	node.addEventListener('pointerup', release);
	node.addEventListener('pointerleave', leave);
	node.addEventListener('pointercancel', flatten);
	node.addEventListener('lostpointercapture', flatten);

	return {
		update(next: PressOptions) {
			options = next;
		},
		destroy() {
			// Removed mid-press: whatever it raised must be lowered.
			flatten();
			node.removeEventListener('click', click);
			node.removeEventListener('contextmenu', menu);
			node.removeEventListener('pointerdown', start);
			node.removeEventListener('pointermove', move);
			node.removeEventListener('pointerup', release);
			node.removeEventListener('pointerleave', leave);
			node.removeEventListener('pointercancel', flatten);
			node.removeEventListener('lostpointercapture', flatten);
		}
	};
}
