// Whether anything on the page may move of its own accord: the system's
// reduced-motion setting, and the effects slider for the poster field. One
// query and one listener for the tab; `reduced()` is reactive, so a change
// under an open page takes effect.

import { display } from '$lib/display.svelte';

// Guarded: the tests run in node, with no window.
const asked =
	typeof window !== 'undefined' && typeof window.matchMedia === 'function'
		? window.matchMedia('(prefers-reduced-motion: reduce)')
		: null;

let still = $state(asked?.matches ?? false);

asked?.addEventListener('change', (event) => (still = event.matches));

/** Whether the reader has asked the system for less movement. */
export function reduced(): boolean {
	return still;
}

/** Whether the poster effects may run: the Appearance choice, under the system
 * setting. Asked by the grid, the strip and the preview alike. */
export function moving(): boolean {
	return display.strength > 0 && !still;
}
