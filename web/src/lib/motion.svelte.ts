// Whether anything on the page may move of its own accord: the system's
// reduced-motion setting, and the effects slider for the poster field. One
// query and one listener for the tab; `reduced()` is reactive, so a change
// under an open page takes effect.

import { backOut, cubicOut } from 'svelte/easing';
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

// Short, since a row travels one row's height and the queue moves under
// somebody watching it.
const ROW_SLIDE = 220;

/** `animate:flip` for a file list: a row that changes place travels there, so
 * the queue advancing reads as movement rather than a redraw. Not under
 * `moving()`, which is the poster effects and nothing to do with a list. */
export function rowSlide(): { duration: number; easing: (t: number) => number } {
	return { duration: still ? 0 : ROW_SLIDE, easing: cubicOut };
}

/** The fade for something small arriving or leaving in place: a row in one
 * of those lists, or a bar's segment giving way to its fill. Shorter than the
 * slide, so a leaving row has gone before the rows under it land in its
 * place. */
export function rowFade(): { duration: number } {
	return { duration: still ? 0 : 150 };
}

// Long enough for the overshoot to read, on a mark twenty pixels wide.
const POP = 200;

/** `transition:scale` for the tick a selection wears: up from half size with
 * a little overshoot, so ticks appearing on every row at once read as the
 * gesture landing rather than a redraw. */
export function tickPop(): { duration: number; easing: (t: number) => number; start: number } {
	return { duration: still ? 0 : POP, easing: backOut, start: 0.5 };
}

/** A whole block of the page arriving or leaving on its own height, which is
 * the only motion that takes the frame round it along. The disclosure panel's
 * `--reveal-span` plus the 20ms a box several times taller wants. */
export function blockSlide(): { duration: number; easing: (t: number) => number } {
	return { duration: still ? 0 : 240, easing: cubicOut };
}
