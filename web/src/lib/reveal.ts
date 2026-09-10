// Show a long list a screenful at a time. Putting several hundred posters in
// the DOM at once is what made the page slow to appear, not to scroll. The
// sentinel sits after the last row and `rootMargin` fetches the next page
// before the reader reaches the gap.

const AHEAD = '800px';

export function whenNear(node: HTMLElement, grow: () => void) {
	let more = grow;
	const observer = new IntersectionObserver(
		(entries) => {
			if (!entries.some((entry) => entry.isIntersecting)) return;
			more();
			// An observer reports crossings, not states: a page that leaves the
			// sentinel in range is never reported again. Re-observing asks afresh,
			// on the next frame so the render has landed.
			requestAnimationFrame(rearm);
		},
		{ rootMargin: AHEAD }
	);

	// Terminates when the caller's `{#if}` unmounts the sentinel.
	function rearm() {
		observer.unobserve(node);
		observer.observe(node);
	}

	observer.observe(node);
	return {
		// The callback closes over the current length.
		update(next: () => void) {
			more = next;
		},
		destroy() {
			observer.disconnect();
		}
	};
}
