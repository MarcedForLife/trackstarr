// Whether anything on the page may move of its own accord: the system's
// reduced-motion setting, and the poster settings for the poster field. One
// query and one listener for the tab; `reduced()` is reactive, so a change
// under an open page takes effect.

import { backOut, cubicOut } from 'svelte/easing';
import type { TransitionConfig } from 'svelte/transition';
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

/** Whether the poster effects may run: a tilt or a finish chosen on
 * Appearance, under the system setting. Asked by the grid, the strip and the
 * preview alike. */
export function moving(): boolean {
	return (display.strength > 0 || display.lights) && !still;
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

// Short, since it is only ever one small thing changing in one place.
const ROW_FADE = 150;

/** Fade for a small element replaced in place. */
export function rowFade(): { duration: number } {
	return { duration: still ? 0 : ROW_FADE };
}

/** `out:` for a state giving way to a sibling in its place. It leaves the flow
 * and fades where it stood. Needs a positioned parent. */
export function swapLeave(node: HTMLElement): TransitionConfig {
	const { offsetTop: top, offsetLeft: left, offsetWidth: width } = node;
	return {
		duration: still ? 0 : ROW_FADE,
		css: (t) =>
			`position: absolute; top: ${top}px; left: ${left}px; width: ${width}px; ` +
			`opacity: ${t}; pointer-events: none`
	};
}

/** `in:` for a list row replaced in place. Waits for the leaving row to fade. */
export function rowArrive(): { duration: number; delay: number } {
	return { duration: still ? 0 : ROW_FADE, delay: still ? 0 : ROW_FADE };
}

// Where each row stood before the update that removed it.
const slots = new WeakMap<HTMLElement, number>();

/** Remember where the rows of every `data-rows` list under `within` stand,
 * before an update moves them. `rows` is what those lists draw. */
export function keepRowSlots(within: () => HTMLElement | undefined, rows: () => unknown): void {
	$effect.pre(() => {
		rows();
		for (const list of within()?.querySelectorAll('[data-rows]') ?? []) {
			for (const row of list.children) {
				if (row instanceof HTMLElement) slots.set(row, row.offsetTop);
			}
		}
	});
}

/** `out:` for a row of such a list. It leaves the flow and fades where it
 * stood, so its replacement arrives in the same frame. Svelte measures a
 * leaving row only once the arrivals are in, which strands it below them.
 *
 * The list has to space its rows with a gap. Under `space-y-*` a leaving row
 * makes the last row in flow no longer last, which gives it a bottom margin
 * that collapses out of the list and pushes everything below it down. */
export function rowLeave(node: HTMLElement): TransitionConfig {
	const slot = slots.get(node);
	if (slot !== undefined) {
		// Svelte has pinned a width of its own, so left and right replace it and
		// the row still spans the list.
		Object.assign(node.style, {
			position: 'absolute',
			top: `${slot}px`,
			left: '0',
			right: '0',
			width: '',
			transform: ''
		});
	}
	return { duration: still ? 0 : ROW_FADE, css: (t) => `opacity: ${t}` };
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

// A digit travels its whole height, on small dim text at the edge of the eye:
// shorter and the turn was missed.
const DIGIT_ROLL = 280;

/** The turn of one digit of a count changing: the old digit leaves the way
 * the number went and the new one follows it in, like a counter wheel. The
 * caller draws the movement, since only it knows the direction. */
export function digitRoll(): { duration: number; easing: (t: number) => number } {
	return { duration: still ? 0 : DIGIT_ROLL, easing: cubicOut };
}

// Long enough to read as counting; short of the Bar's 500ms, so the number
// lands and the bar catches it up rather than the other way round.
const COUNT_THROUGH = 400;

/** The tween for a count that jumped: it counts through the values between,
 * where rolling every digit at once would say nothing about how far it went. */
export function countThrough(): { duration: number; easing: (t: number) => number } {
	return { duration: still ? 0 : COUNT_THROUGH, easing: cubicOut };
}
