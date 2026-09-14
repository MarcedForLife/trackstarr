// Moving something that may already be moving. All transforms, so it stays on
// the compositor, and a run in flight is taken over from where it stands
// rather than snapped back to its start.

/** How far down a computed transform moves an element. */
export function offsetOf(transform: string): number {
	if (!transform || transform === 'none') return 0;
	return new DOMMatrix(transform).m42;
}

/** Where a transform has this element now, a running animation included. */
export function offsetNow(element: HTMLElement): number {
	return offsetOf(getComputedStyle(element).transform);
}

/** Start `by` pixels from where the element stands and travel home. */
export function carry(element: HTMLElement, by: number, span: number, easing: string): Animation {
	const from = offsetNow(element) + by;
	const start = `translateY(${from}px)`;
	// An animation's first value lands a frame late, so this frame would paint
	// the move uncompensated. The animation outranks the inline style after it.
	element.style.transform = start;
	// Read back, since the CSSOM rounds what it stores.
	const stopgap = element.style.transform;
	const run = element.animate([{ transform: start }, { transform: 'none' }], {
		duration: span,
		easing
	});
	// The run does not fill, so a stopgap left behind would snap the element
	// back at the end. Left alone if a later carry or a drag rewrote it.
	requestAnimationFrame(() => {
		if (element.style.transform === stopgap) element.style.transform = '';
	});
	return run;
}
