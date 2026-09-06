// A grip that pushes something off the bottom of the screen, shared by the
// title sheet and the selection bar: a downward-only drag, a press that
// dismisses, a springback, and keyboard access. The offset never becomes state;
// the caller writes it onto the element inside this action's frame callback.

// Under this many pixels the gesture is a press on the grip, which dismisses.
const SLOP = 6;

export type DragOptions = {
	/** Past this many pixels the thing goes rather than springing back. The
	 * caller's, as a share of what is dragged. */
	threshold: number;
	/** Whether a gesture may begin; false while one is already leaving. */
	may?: () => boolean;
	/** Where the drag has reached, and whether one is under way. The caller
	 * writes the offset and drops its transition while dragging. */
	onmove: (offset: number, dragging: boolean) => void;
	/** Released past the threshold, pressed, or reached from a keyboard. `at` is
	 * where the finger left it, to carry on from. */
	ondismiss: (at: number) => void;
};

export function dragDismiss(node: HTMLElement, opts: DragOptions) {
	let options = opts;

	let dragging = false;
	let from = 0;
	let dragged = 0;
	let moved = false;
	let frame = 0;

	function grab(event: PointerEvent) {
		if (options.may?.() === false) return;
		from = event.clientY;
		dragged = 0;
		moved = false;
		dragging = true;
		node.setPointerCapture(event.pointerId);
	}

	function drag(event: PointerEvent) {
		if (!dragging) return;
		// Downwards only. An upward drag still counts as movement, so releasing
		// after one springs back rather than dismissing as a press.
		const travel = event.clientY - from;
		if (Math.abs(travel) > SLOP) moved = true;
		dragged = Math.max(0, travel);
		if (!frame) frame = requestAnimationFrame(paint);
	}

	function paint() {
		frame = 0;
		if (dragging) options.onmove(dragged, true);
	}

	function drop() {
		if (!dragging) return;
		dragging = false;
		cancelAnimationFrame(frame);
		frame = 0;
		// A press on the grip dismisses. Decided on the release, since a captured
		// pointer's click is sometimes withheld.
		if (dragged > options.threshold || !moved) {
			// Left where the finger let go, painted now because a flick ends with a
			// move the pending frame has not drawn. Clearing the offset here read as
			// the panel bouncing up before it went: `history.back()` lands a task
			// later.
			options.onmove(dragged, false);
			options.ondismiss(dragged);
			return;
		}
		// Spring back, with the transition on.
		options.onmove(0, false);
	}

	// Enter and Space arrive as a click with no pointer; every other click was
	// answered by the release.
	function pressed(event: MouseEvent) {
		if (event.detail === 0) options.ondismiss(0);
	}

	node.addEventListener('pointerdown', grab);
	node.addEventListener('pointermove', drag);
	node.addEventListener('pointerup', drop);
	node.addEventListener('pointercancel', drop);
	node.addEventListener('click', pressed);

	return {
		update(next: DragOptions) {
			options = next;
		},
		destroy() {
			cancelAnimationFrame(frame);
			node.removeEventListener('pointerdown', grab);
			node.removeEventListener('pointermove', drag);
			node.removeEventListener('pointerup', drop);
			node.removeEventListener('pointercancel', drop);
			node.removeEventListener('click', pressed);
		}
	};
}
