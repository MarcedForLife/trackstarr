// A press that spreads from the point touched. A wide row flipping its
// background reads as a flash. Transform and opacity only, so no layout.

import { reduced } from '$lib/motion.svelte';

export function ripple(node: HTMLElement, on = true) {
	// An action cannot be applied conditionally, so the caller says. Read once.
	if (!on) return;

	function start(event: PointerEvent) {
		// Asked on the press: the setting can change under an open page.
		if (reduced()) return;
		// The mark is positioned against the row; a static row would hand it to
		// an ancestor.
		if (getComputedStyle(node).position === 'static') node.style.position = 'relative';
		const box = node.getBoundingClientRect();
		const x = event.clientX - box.left;
		const y = event.clientY - box.top;
		const reach = Math.max(
			Math.hypot(x, y),
			Math.hypot(box.width - x, y),
			Math.hypot(x, box.height - y),
			Math.hypot(box.width - x, box.height - y)
		);

		const mark = document.createElement('span');
		mark.style.cssText = `position:absolute;pointer-events:none;border-radius:9999px;background:currentColor;width:${reach * 2}px;height:${reach * 2}px;left:${x - reach}px;top:${y - reach}px`;
		node.appendChild(mark);

		// Three stops: holding the opacity through the first third keeps the
		// spread visible rather than a rectangle dimming. The promise rejects on
		// a cancel.
		const spread = mark.animate(
			[
				{ transform: 'scale(0)', opacity: 0.18, offset: 0 },
				{ transform: 'scale(0.5)', opacity: 0.18, offset: 0.35 },
				{ transform: 'scale(1)', opacity: 0, offset: 1 }
			],
			{ duration: 520, easing: 'cubic-bezier(0.32, 0.6, 0.3, 1)' }
		);
		const done = () => mark.remove();
		spread.finished.then(done, done);
	}

	node.addEventListener('pointerdown', start);
	return {
		destroy() {
			node.removeEventListener('pointerdown', start);
		}
	};
}
