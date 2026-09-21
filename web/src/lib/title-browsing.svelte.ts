import { getLinks, getTitle, type Card, type TitleDetail, type TitleLink } from '$lib/library';
import { fileGroups } from '$lib/variants';
import { poll, type Poller } from '$lib/poll';
import { seasons, type Season } from '$lib/seasons';

export type Opening = Pick<Card, 'id' | 'name'> & Partial<Card>;
export const FILE_PAGE = 12;

/** One opening of one library, shared with work and editor completions. */
export type TitleSession = {
	readonly id: string;
	current: () => boolean;
	reload: () => Promise<void>;
};

export type ReadingState = {
	pages: number;
	renderedGroups: number;
	expandedSeasons: string[] | null;
	selectedPaths: Record<string, string>;
};

function initialReading(): ReadingState {
	return { pages: 1, renderedGroups: FILE_PAGE, expandedSeasons: null, selectedPaths: {} };
}

/** Browsing for one sheet. DOM, overlays and work actions belong to its caller.
 * Every open (including a retry) replaces request ownership. Reading choices
 * survive a retry of the same title, but never another title, a close or
 * disposal. */
export class TitleBrowsing {
	opened = $state<Opening | null>(null);
	detail = $state<TitleDetail | null>(null);
	links = $state<TitleLink[] | null>(null);
	loading = $state(false);
	failure = $state('');
	loadingMore = $state(false);
	moreError = $state('');
	refreshError = $state('');
	reading = $state<ReadingState>(initialReading());
	private readingOf: string | null = null;
	private opening: TitleSession | null = null;
	get session() {
		return this.opening;
	}
	private detailTicket = 0;
	private disposed = false;
	private watcher: Poller | null = null;
	private watch() {
		return poll({
			ask: () => this.reload(),
			ready: () => !!this.opening && !!this.detail && !this.loading,
			gap: 2000,
			kinds: ['library']
		});
	}

	get kind() {
		return this.opened?.kind ?? this.detail?.kind ?? '';
	}

	get grouped() {
		return this.detail ? seasons(this.detail.files) : null;
	}

	get groups() {
		return fileGroups(this.detail?.files ?? [], this.kind);
	}

	async open(card: Opening) {
		if (this.disposed) return;
		const opening: TitleSession = {
			id: card.id,
			current: () => !this.disposed && this.opening === opening,
			reload: async () => {
				if (opening.current()) await this.reload();
			}
		};
		this.opening = opening;
		const ticket = ++this.detailTicket;
		this.opened = card;
		if (this.readingOf !== card.id) this.reading = initialReading();
		this.readingOf = card.id;
		this.detail = null;
		this.links = null;
		this.failure = '';
		this.refreshError = '';
		this.moreError = '';
		this.loadingMore = false;
		this.loading = true;
		// Events owed while shut concern the previous title, or this initial read.
		this.watcher ??= this.watch();
		this.watcher.mark();
		void this.find(opening);
		try {
			const found = await getTitle(card.id, fetch, this.reading.pages);
			if (this.current(opening, ticket)) this.accept(found);
		} catch {
			if (this.current(opening, ticket)) this.failure = 'Could not read that title.';
		} finally {
			if (this.current(opening, ticket)) this.loading = false;
		}
	}

	fill(card: Card) {
		if (this.opening?.id === card.id) this.opened = card;
	}

	/** Invalidate immediately; keep presentation data during the closing slide. */
	close() {
		this.watcher?.stop();
		this.watcher = null;
		this.opening = null;
		this.detailTicket++;
		this.readingOf = null;
	}

	/** Called when the visual close finishes; harmless after a quick reopen. */
	clear() {
		if (this.opening) return;
		this.opened = null;
		this.detail = null;
		this.links = null;
		this.reading = initialReading();
	}

	dispose() {
		if (this.disposed) return;
		this.disposed = true;
		this.close();

		this.clear();
	}

	more() {
		this.reading.renderedGroups = Math.min(
			this.reading.renderedGroups + FILE_PAGE,
			this.groups.length
		);
	}

	isOpen(season: Season, at: number): boolean {
		return this.reading.expandedSeasons?.includes(season.label) ?? at === 0;
	}

	fold(season: Season, at: number) {
		const expanded =
			this.reading.expandedSeasons ?? this.grouped?.slice(0, 1).map((s) => s.label) ?? [];
		this.reading.expandedSeasons = this.isOpen(season, at)
			? expanded.filter((label) => label !== season.label)
			: [...expanded, season.label];
	}

	async loadMore() {
		const opening = this.opening;
		if (!opening || this.loading || this.loadingMore) return;
		const ticket = ++this.detailTicket;
		const pages = this.reading.pages + 1;
		this.loadingMore = true;
		this.moreError = '';
		try {
			const found = await getTitle(opening.id, fetch, pages);
			if (!this.current(opening, ticket)) return;
			this.reading.pages = pages;
			this.accept(found);
		} catch {
			if (this.current(opening, ticket)) this.moreError = 'Could not load more files. Try again.';
		} finally {
			if (this.current(opening, ticket)) this.loadingMore = false;
		}
	}

	/** Replace a cumulative prefix without losing reading choices on failure. */
	async reload() {
		const opening = this.opening;
		if (!opening || this.loading || this.loadingMore) return;
		const ticket = ++this.detailTicket;
		try {
			const found = await getTitle(opening.id, fetch, this.reading.pages);
			if (this.current(opening, ticket)) this.accept(found);
		} catch {
			if (this.current(opening, ticket))
				this.refreshError = 'Could not refresh verdicts. Showing the last update.';
		}
	}

	private current(opening: TitleSession, ticket: number) {
		return opening.current() && this.detailTicket === ticket;
	}

	private accept(found: TitleDetail) {
		this.detail = found;
		this.failure = '';
		this.refreshError = '';
	}

	private async find(opening: TitleSession) {
		let found: TitleLink[];
		try {
			found = await getLinks(opening.id);
		} catch {
			found = [];
		}
		if (opening.current()) this.links = found;
	}
}
