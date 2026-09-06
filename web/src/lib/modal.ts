// What an overlay does to the page under it: takes the keyboard and hands it
// back, and makes the rest of the page inert while up. An `aria-modal` panel
// with a tabbable page behind it says one thing and shows another.

import { tick } from 'svelte';

// The element that takes the keyboard, or how to find it once drawn.
type Into = HTMLElement | null | (() => HTMLElement | null);

function focused(): HTMLElement | null {
	return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

// Whether focus can be handed back here: the control may be gone, disabled, or
// inside something another overlay made inert.
function reachable(element: HTMLElement | null): element is HTMLElement {
	if (!element?.isConnected) return false;
	if ((element as HTMLButtonElement).disabled) return false;
	return !element.closest('[inert]');
}

// How many frames an overlay gets to become focusable.
const FRAMES = 10;

/** Focus the overlay as soon as it will take it. focus() ignores a hidden
 * element, and the drawer is hidden for its transition's first frames. */
function reach(into: Into, live: () => boolean, left = FRAMES) {
	requestAnimationFrame(() => {
		if (!live()) return;
		const target = typeof into === 'function' ? into() : into;
		if (!target) return;
		target.focus();
		if (document.activeElement !== target && left > 1) reach(into, live, left - 1);
	});
}

/**
 * Focus goes into the overlay as it comes up and back where it came from as it
 * goes. `back` is for a caller whose control is blurred before the overlay is
 * drawn: a confirm disables the button it came from, and the browser blurs a
 * disabled button.
 */
export function keyboard(into: Into, back: HTMLElement | null = focused()): () => void {
	let live = true;
	reach(into, () => live);
	return () => {
		live = false;
		// Wait out the update that removed the overlay; it also re-enables the
		// control underneath.
		tick().then(() => {
			if (reachable(back)) back.focus();
		});
	};
}

// A control a keyboard can land on, and a region that says things out loud.
const FOCUSABLE = 'a[href], button, input, select, textarea, iframe, [tabindex], [contenteditable]';
const SPEAKING = '[aria-live], [role="status"], [role="alert"], [role="log"]';

// Whether making this element inert is worth it. A live region with nothing to
// press is not somewhere a keyboard can go, and inert would silence it:
// SvelteKit's route announcer and the save bar both speak from one.
function worth(element: HTMLElement): boolean {
	if (element.localName === 'script' || element.localName === 'style') return false;
	return !element.matches(SPEAKING) || !!element.querySelector(FOCUSABLE);
}

/**
 * Make the page behind an overlay inert and stop the body scrolling. Everything
 * not among `kept` or an ancestor of one goes inert; several because an overlay
 * and its scrim are not always siblings. Elements already inert are left alone,
 * so unwinding cannot hand back a reach something else still denies.
 */
export function behind(...kept: HTMLElement[]): () => void {
	const spared = new Set<Element>();
	for (const node of kept) {
		for (let step: Element | null = node; step; step = step.parentElement) spared.add(step);
	}

	const marked: HTMLElement[] = [];
	for (const node of kept) {
		for (
			let step: Element | null = node;
			step && step !== document.body;
			step = step.parentElement
		) {
			for (const sibling of Array.from(step.parentElement?.children ?? [])) {
				if (spared.has(sibling) || !(sibling instanceof HTMLElement)) continue;
				if (sibling.inert || !worth(sibling)) continue;
				sibling.inert = true;
				marked.push(sibling);
			}
		}
	}

	const scroll = document.body.style.overflow;
	document.body.style.overflow = 'hidden';

	return () => {
		for (const element of marked) element.inert = false;
		document.body.style.overflow = scroll;
	};
}
