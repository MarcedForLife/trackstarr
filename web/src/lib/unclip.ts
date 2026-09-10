// Let a reveal's clip go once the panel has opened. Kept, the clip cuts off
// anything a child draws outside its bounds: focus rings and the selected
// palette swatch's outline, both 2px clear of their element.

export function unclip(node: HTMLElement) {
	const first = node.firstElementChild;
	if (!(first instanceof HTMLElement)) return;
	const clip: HTMLElement = first;

	function open(event: AnimationEvent) {
		// A child's own animation bubbles here too.
		if (event.target !== clip) return;
		node.style.overflow = 'visible';
		clip.style.overflow = 'visible';
	}

	// Reduced motion crushes the duration rather than removing the animation, so
	// this still fires.
	clip.addEventListener('animationend', open);
	return {
		destroy() {
			clip.removeEventListener('animationend', open);
		}
	};
}
