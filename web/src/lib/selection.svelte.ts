// Which posters are ticked, and the bar over the grid that acts on them.
// $lib/recheck is what the service allows; this is what the reader has picked.
//
// Picking is an overlay in everything but pixels: it changes what a tap means
// and stands a bar over the grid, so it holds a history entry and the back
// gesture ends it. The bar also shows an adopted run, which was nobody's press
// and gets no entry. The grid and filters decide what `all()` reaches, through
// `showing`.

import { SvelteSet } from 'svelte/reactivity';
import type { Card } from '$lib/library';
import { overlay, type Overlay } from '$lib/overlay';
import { pageTop, scrollPageTo } from '$lib/scroller';
import type { Recheck } from '$lib/recheck.svelte';

type Options = {
	/** The Recheck, on demand: the two reference each other, so the page makes
	 * this first and nothing here reads it until a press or a poll asks. */
	recheck: () => Recheck;
	/** Everything the filter and search leave on screen, which is what "select
	 * all" means. Not only the rows laid out so far. */
	showing: () => Card[];
	/** Start the bar leaving; how many milliseconds that takes, or zero. */
	fade: () => number;
	/** A fade abandoned, because the reader asked for the bar again inside it. */
	stay: () => void;
	/** Take the bar away, the way its own grip does. */
	dismiss: () => void;
};

export class Selection {
	/** Whether a tap selects rather than opens. Entered from the Select button
	 * or by holding a poster. */
	picking = $state(false);
	/** The ticked ids. A SvelteSet, so one tap does not rebuild the whole
	 * selection on a 500 poster shelf. */
	readonly picked = new SvelteSet<string>();

	#options: Options;
	#picker: Overlay;
	// A dismissal still fading, so a second does not clear the selection under
	// the first.
	#emptying: ReturnType<typeof setTimeout> | null = null;
	// The scroll position to restore after spending the entry, which would
	// otherwise put the reader back where picking began.
	#putBack: number | null = null;

	constructor(options: Options) {
		this.#options = options;
		this.#picker = overlay({
			name: 'picking',
			close: () => this.#shutBar(),
			// The grip's job for a keyboard. Not while a run is going, when the bar
			// is its readout.
			onescape: () => {
				const recheck = this.#recheck;
				if ((this.picking || recheck.summary) && !recheck.running) this.#options.dismiss();
			}
		});
	}

	get #recheck(): Recheck {
		return this.#options.recheck();
	}

	/** Whether the bar is up: a selection, a run, or a receipt. A getter, since a
	 * `$derived` field would be built before the constructor has the options. */
	get barUp(): boolean {
		const recheck = this.#recheck;
		return this.picking || !!recheck.running || !!recheck.summary;
	}

	/** The ids as the bar's Run press wants them. */
	get ids(): string[] {
		return [...this.picked];
	}

	toggle(card: Card) {
		if (this.picked.has(card.id)) this.picked.delete(card.id);
		else this.picked.add(card.id);
		this.#recheck.refusal = '';
	}

	clear() {
		this.picked.clear();
		this.#recheck.refusal = '';
	}

	all() {
		this.picked.clear();
		for (const card of this.#options.showing()) this.picked.add(card.id);
		this.#recheck.refusal = '';
	}

	/** Into picking, from the button at the top of the page. */
	enter() {
		// A bar still fading from the last dismissal would clear this selection
		// and stay invisible.
		if (this.#emptying !== null) {
			clearTimeout(this.#emptying);
			this.#emptying = null;
			this.#options.stay();
		}
		this.picking = true;
		this.#picker.raise();
		// The bar says why a run cannot start now.
		this.#recheck.prod();
	}

	/** The bar was put away by a press on it or its grip. */
	leave() {
		// Spend the entry, or the next back press would raise the bar again over
		// a cleared selection.
		this.#putBack = pageTop();
		if (!this.#picker.lower()) this.#putBack = null;
	}

	/** A poster held and released in place: picking turns on with it ticked,
	 * without a scroll up to Select. */
	holdPick(card: Card) {
		if (!this.picking) this.enter();
		this.picked.add(card.id);
		this.#recheck.refusal = '';
	}

	/** A run ended and left a receipt for the bar. Raised without an entry, since
	 * an adopted run was nobody's press. */
	showReceipt() {
		this.#picker.raise(false);
	}

	// The bar's entry has gone, or there was none. The grip and buttons fade the
	// bar before spending the entry; a back gesture arrives with it at full
	// opacity, so this starts the fade.
	#shutBar() {
		if (this.#emptying !== null) clearTimeout(this.#emptying);
		// Zero when the fade has already run, or a run keeps the bar up.
		const fading = this.#options.fade();
		if (fading) this.#emptying = setTimeout(() => this.#drop(), fading);
		else this.#drop();
		if (this.#putBack === null) return;
		const at = this.#putBack;
		this.#putBack = null;
		// After SvelteKit's popstate listener restores the entry's scroll, which
		// this would otherwise lose to; still before the paint.
		requestAnimationFrame(() => scrollPageTo(at));
	}

	// Let go of what the bar is drawing. Split from shutting it because the bar
	// must keep its contents while it fades.
	#drop() {
		this.#emptying = null;
		this.picking = false;
		this.clear();
		this.#recheck.summary = null;
	}
}
