// The stretch of history a page holds, and every read that changes it: how far
// back was asked, where the next page resumes, what has landed since, and what
// to do when the list no longer joins up with the file.
//
// The window is the service's question: narrowed here, "the last month" would
// mean "of the hundred lines loaded". The kind filter is the page's. Which rows
// are drawn and open is the page's too; a read that replaces the list says so
// through `onfresh`.

import { onDestroy } from 'svelte';
import {
	CUSTOM,
	field,
	getEvents,
	key,
	moment,
	preset,
	type Event,
	type EventPage,
	type Span
} from '$lib/events';
import type { Card } from '$lib/library';
import { poll, type Poller } from '$lib/poll';
import { told } from '$lib/stream';

// How often the page looks for new lines.
const CATCH_UP_MS = 10000;

// How many lines a catch-up asks for: more than anything writes between two.
const CATCH_UP = 25;

// How many lines a read from the top or a page further back asks for.
const PAGE = 100;

type Options = {
	/** The list was replaced, not added to: open rows and scroll depth mean
	 * nothing now. */
	onfresh: () => void;
};

export class History {
	/** The loaded window, oldest page appended to the end. */
	entries = $state<Event[]>([]);
	/** The cards for the titles those lines name, keyed by id, accumulated as
	 * pages arrive. */
	titles = $state<Record<string, Card>>({});
	/** Where the next page resumes; null at the oldest line. */
	cursor = $state<number | null>(null);
	/** A read the buttons should show. */
	busy = $state(false);
	failure = $state('');

	/** How far back to look, by $lib/events' SPANS name. */
	span = $state('all');
	/** The ends of a custom range, in the browser's clock. Seeded the first
	 * time Custom is chosen. */
	from = $state('');
	to = $state('');

	#options: Options;
	// The window the list was read under, which paging back must reuse: a
	// preset recomputed minutes later would drop the line on the boundary. Plain,
	// since only the fetches read it.
	#reading: Span = {};
	#feed: Poller;

	constructor(loaded: EventPage, options: Options) {
		this.#options = options;
		this.entries = loaded.events;
		this.titles = loaded.titles ?? {};
		this.cursor = loaded.next;
		// Quietly, and only while the list is free: a failed catch-up is not worth
		// an error, Load more must not be fought for the list, and a closed window
		// is settled.
		this.#feed = poll({
			ask: async () => {
				try {
					await this.#catchUp();
				} catch {
					/* the lines already on screen are still true */
				}
			},
			pace: () => told(CATCH_UP_MS),
			// A sweep announces a line a second; re-reading at that rate is worse
			// than the wait.
			gap: CATCH_UP_MS,
			ready: () => !this.busy && !this.#settled,
			kinds: ['events']
		});
		onDestroy(() => this.#feed.stop());
	}

	/** Whether the window narrows anything. A range with both ends cleared is
	 * the whole history and must not be called a range. */
	bounded = $derived(
		this.span !== 'all' && (this.span !== CUSTOM || !!moment(this.from) || !!moment(this.to, true))
	);

	// Whether the far end is closed. Nothing new can land in a range that ended,
	// so the catch-up stops looking.
	#settled = $derived(this.span === CUSTOM && !!moment(this.to, true));

	/** Move to a window and read it from the top. */
	look(chosen: string) {
		this.span = chosen;
		// Seeded only when empty, so coming back to Custom finds the last range.
		if (chosen === CUSTOM && !this.from && !this.to) {
			// Read once into the pickers' strings; nothing watches a Date here.
			// eslint-disable-next-line svelte/prefer-svelte-reactivity
			const now = new Date();
			// eslint-disable-next-line svelte/prefer-svelte-reactivity
			this.from = field(new Date(now.getTime() - 7 * 86_400_000));
			this.to = field(now);
		}
		return this.refresh();
	}

	/** Read the window from the top. A changed window is a different list, not
	 * a filter over this one. */
	async refresh() {
		this.busy = true;
		this.failure = '';
		try {
			this.#reading = this.#asked();
			const page = await getEvents(fetch, PAGE, null, this.#reading);
			this.entries = page.events;
			this.titles = page.titles ?? {};
			this.cursor = page.next;
			this.#options.onfresh();
		} catch {
			this.failure = 'Could not reach the service.';
		} finally {
			this.busy = false;
			// The newest lines came with this read; the catch-up can wait.
			this.#feed.mark();
		}
	}

	/** The page of older lines below the one loaded. */
	async more() {
		if (this.cursor === null) return;
		this.busy = true;
		this.failure = '';
		try {
			// The cursor says where to resume; the window the list was read under
			// says where to stop.
			const page = await getEvents(fetch, PAGE, this.cursor, this.#reading);
			this.entries = [...this.entries, ...page.events];
			// Merged: the rows on screen still name their titles.
			this.titles = { ...this.titles, ...page.titles };
			this.cursor = page.next;
		} catch {
			this.failure = 'Could not read any more history.';
		} finally {
			this.busy = false;
		}
	}

	/** The window to ask for, worked out fresh at each fetch. */
	#asked(): Span {
		if (this.span !== CUSTOM) return preset(this.span);
		return { since: moment(this.from), until: moment(this.to, true) };
	}

	/** Put the lines that arrived since the last look on the front. Nothing
	 * loaded moves, so the reader's place and open panels stay put. */
	async #catchUp() {
		// Recomputed: this is the one read that wants a preset to have moved on.
		const now = this.#asked();
		const page = await getEvents(fetch, CATCH_UP, null, now);
		// A lookup for the length of this call.
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const known = new Set(this.entries.map(key));
		if (this.entries.length && !page.events.some((entry) => known.has(key(entry)))) {
			// Nothing we hold is in the newest window, so the list no longer joins
			// up with the file. Start again from the top.
			this.entries = page.events;
			this.titles = page.titles ?? {};
			this.cursor = page.next;
			// The list is this window's now, so paging back is too.
			this.#reading = now;
			this.#options.onfresh();
			return;
		}
		const fresh = page.events.filter((entry) => !known.has(key(entry)));
		if (!fresh.length) return;
		this.entries = [...fresh, ...this.entries];
		// Merged, as an older page is.
		this.titles = { ...this.titles, ...page.titles };
	}
}
