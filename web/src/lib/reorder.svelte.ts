// The drag that reorders a settings list, shared by the layout and language
// lists. Rows from `orderable` down are removals, fixed at the bottom and not
// draggable, so the movable rows are always the prefix.
//
// Nothing is reordered until the drag ends; passed rows move by transform. A
// live splice would fight that.

import { tick } from 'svelte';

export type ReorderOptions = {
	/** The element holding the rows, each marked `data-reorder-row`. */
	list: () => HTMLElement;
	/** How many rows from the top may move. */
	orderable: () => number;
	locked: () => boolean;
	/** Commit a move; already bounds-checked. */
	move: (from: number, to: number) => void;
};

export class Reorder {
	/** The row being dragged, and where it would land. */
	from = $state<number | null>(null);
	to = $state(0);
	/** How far the dragged row has travelled, in pixels. */
	offset = $state(0);

	#opts: ReorderOptions;
	#step = 0;
	#start = 0;

	constructor(opts: ReorderOptions) {
		this.#opts = opts;
	}

	// Measured rather than assumed: the row height differs across sm.
	#rowStep(): number {
		const drawn = this.#opts.list().querySelectorAll('[data-reorder-row]');
		if (drawn.length < 2) return 0;
		return drawn[1].getBoundingClientRect().top - drawn[0].getBoundingClientRect().top;
	}

	#commit(from: number, to: number) {
		const limit = this.#opts.orderable();
		if (to < 0 || to >= limit || from >= limit || to === from) return;
		this.#opts.move(from, to);
	}

	grab = (event: PointerEvent, index: number) => {
		if (this.#opts.locked() || index >= this.#opts.orderable()) return;
		// Or the browser scrolls the page and no pointermove arrives.
		event.preventDefault();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		this.#step = this.#rowStep();
		this.#start = event.clientY;
		this.from = index;
		this.to = index;
		this.offset = 0;
	};

	drag = (event: PointerEvent) => {
		if (this.from === null) return;
		this.offset = event.clientY - this.#start;
		const moved = this.#step ? Math.round(this.offset / this.#step) : 0;
		this.to = Math.min(Math.max(this.from + moved, 0), this.#opts.orderable() - 1);
	};

	drop = () => {
		if (this.from === null) return;
		this.#commit(this.from, this.to);
		this.from = null;
		this.offset = 0;
	};

	/** How far a row shifts to make room: those between the dragged row's home
	 * and its landing move one place. */
	shift(index: number): number {
		if (this.from === null || index === this.from) return 0;
		if (this.from < index && index <= this.to) return -this.#step;
		if (this.to <= index && index < this.from) return this.#step;
		return 0;
	}

	keys = async (event: KeyboardEvent, index: number) => {
		const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
		if (!step || this.#opts.locked()) return;
		event.preventDefault();
		// A browser blurs a moved element, so focus goes back or the next arrow
		// press goes nowhere.
		const handle = event.currentTarget as HTMLElement;
		this.#commit(index, index + step);
		await tick();
		handle.focus();
	};
}
