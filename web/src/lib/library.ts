// The library API. Everything here came out of the sweep cache, so a title with
// no verdicts is one nothing has walked yet.

import { request } from '$lib/api';
import type { Sort } from '$lib/order.svelte';
// Re-exported: a file's size is read straight off the card that carries it.
export { size } from '$lib/format';

// One stream as the service summarises it. `flags` are trackstarr's own
// classifications, so the page reads "commentary" as the rules do.
export type Track = {
	index: number;
	kind: 'video' | 'audio' | 'subtitle' | 'attachment' | string;
	codec?: string;
	channels?: number;
	lang?: string;
	title?: string;
	bitrate?: number;
	flags?: string[];
	// Only on a planned track: its input stream. A generated downmix names its
	// source, so two planned entries can share one.
	src?: number;
};

// Why a file is what it is: the lists a "modified" event records, plus the skip.
export type Why = {
	skip?: string;
	// Only on a failed rewrite: what broke. The next sweep tries again.
	failed?: string;
	reasons?: string[];
	incidental?: string[];
	rules?: string[];
	incidental_rules?: string[];
};

// What a rewrite did, kept on the file it left behind. `at` is when, as the
// history spells its stamps. The reasons in prose are the history page's; here
// the tracks it moved are the account. See unify().
export type Modified = {
	at: string;
	bytes_before?: number;
	bytes_after?: number;
	// The file's tracks before the rewrite, the source indices it dropped and
	// the output positions it generated. The file itself is the after. All three
	// are missing where the rewrite moved no track, and on most made before they
	// were kept.
	was?: Track[];
	dropped?: number[];
	added?: number[];
};

// `mixed` and `modified` are a card's words, never a file's. See FILTERS below.
// Closed on purpose: a word off the wire is narrowed by asVerdict() before it
// reaches anything typed with this, so a misspelling here is a build error and
// not a chip nothing ever draws.
export type Verdict =
	| 'failed'
	| 'pending'
	| 'deferred'
	| 'skip'
	| 'unsupported'
	| 'conform'
	| 'modified'
	| 'mixed'
	| 'unchecked'
	| 'missing';

// What a title is: the *arr's own word for what it tracks, or a folder the
// sweep found under a media dir that neither Radarr nor Sonarr claims.
export type Kind = 'movie' | 'series' | 'folder' | string;

export type Card = {
	id: string;
	name: string;
	kind: Kind;
	// The worst verdict any of its files reached.
	state: Verdict;
	year?: number;
	lang?: string;
	// The IMDb score out of ten; missing for anything unrated.
	rating?: number;
	// When the title joined the library, in epoch seconds.
	added?: number;
	files?: number;
	bytes?: number;
	counts?: Record<string, number>;
	// When trackstarr last had one of its files open, in epoch seconds: the
	// later of its newest verdict and its newest rewrite.
	processed?: number;
	// Layouts the rewrites would add, layouts they would rebuild, and how many
	// tracks they would drop: the three changes a poster has room for.
	adds?: string[];
	rebuilds?: string[];
	drops?: number;
	// How many of its files trackstarr has rewritten. A rewritten file passes,
	// so without this a card cannot say it was ever touched.
	modified?: number;
	// Those three as the one number the grid and strip sort on. See _weight in
	// library.py.
	weight?: number;
};

export type Shelf = {
	titles: Card[];
	// False when an *arr could not be listed, so titles are missing.
	complete: boolean;
	// False when the verdicts were reached under rules since changed.
	current: boolean;
	swept: number;
};

export type LibraryFile = {
	path: string;
	name: string;
	status: Verdict;
	bytes: number;
	lang?: string | null;
	// The file now, and what a rewrite would leave. The second is empty for
	// everything but a pending file.
	tracks: Track[];
	planned: Track[];
	why: Why;
	// Absent on a file no rewrite of ours has published.
	modified?: Modified;
};

// A service the title could be opened in. The sheet draws its buttons from this
// and fills the urls in from getLinks; an *arr's link is already here.
export type TitleServer = { server: string; label: string; url?: string };

// The same service once asked, and found to have the title.
export type TitleLink = TitleServer & { url: string };

export type TitleDetail = {
	id: string;
	name: string;
	kind: string;
	year?: number;
	lang?: string;
	folder: string;
	current: boolean;
	files: LibraryFile[];
	// Every file under the title, which exceeds `files` past the cap.
	total: number;
	servers: TitleServer[];
};

// What the overview reads: a strip's worth of the grid's cards, plus the tally.
export type Summary = {
	titles: number;
	counts: Record<string, number>;
	// The first few in the strip's order, which only the service can sort.
	head: Card[];
	complete: boolean;
	current: boolean;
	swept: number;
};

/** A tally under the words this build draws. Anything else is folded into the
 * verdict asVerdict() puts it under, so a count cannot go on saying a word its
 * card no longer leads with. The same object back where every word is known,
 * which is every answer until the service learns a new one. */
function counted(counts: Record<string, number>): Record<string, number> {
	const words = Object.keys(counts);
	if (words.every(isVerdict)) return counts;
	const folded: Record<string, number> = {};
	for (const word of words) {
		const verdict = asVerdict(word);
		folded[verdict] = (folded[verdict] ?? 0) + counts[word];
	}
	return folded;
}

/** Narrow the verdicts on fetched cards, in place. The types here promise a
 * Verdict and the service is free to answer with a word this build has never
 * heard of, so every answer carrying cards comes through here. */
export function judged(cards: Card[]): void {
	for (const card of cards) {
		card.state = asVerdict(card.state);
		if (card.counts) card.counts = counted(card.counts);
	}
}

export async function getShelf(fetcher: typeof fetch = fetch): Promise<Shelf> {
	const shelf = await request<Shelf>('/api/library', undefined, fetcher);
	judged(shelf.titles);
	return shelf;
}

export async function getSummary(
	fetcher: typeof fetch = fetch,
	sort: Sort = 'processed'
): Promise<Summary> {
	const summary = await request<Summary>(`/api/library/summary?sort=${sort}`, undefined, fetcher);
	judged(summary.head);
	summary.counts = counted(summary.counts);
	return summary;
}

export async function getTitle(id: string, fetcher: typeof fetch = fetch): Promise<TitleDetail> {
	const title = await request<TitleDetail>(
		`/api/library/title?id=${encodeURIComponent(id)}`,
		undefined,
		fetcher
	);
	for (const file of title.files) file.status = asVerdict(file.status);
	return title;
}

/** Where this title lives in Plex and Jellyfin. Its own request, so a media
 * server that has gone away does not hold up the verdicts. */
export async function getLinks(id: string, fetcher: typeof fetch = fetch): Promise<TitleLink[]> {
	const path = `/api/library/links?id=${encodeURIComponent(id)}`;
	const answer = await request<{ links?: TitleLink[] }>(path, undefined, fetcher);
	return answer.links ?? [];
}

// How many titles the IMDb table scores, and when it was rebuilt, in epoch
// seconds; 0 before the first fetch.
export type Ratings = { scored: number; fetched: number };

export function getRatings(fetcher: typeof fetch = fetch): Promise<Ratings> {
	return request<Ratings>('/api/library/ratings', undefined, fetcher);
}

/** Fetch IMDb's dataset now rather than at the next tick, for somebody who has
 * just switched the scores on. */
export function refreshRatings(): Promise<Ratings & { titles: number }> {
	return request<Ratings & { titles: number }>('/api/library/ratings', {
		method: 'POST',
		body: '{}'
	});
}

/** Drop every stored verdict; how many went. Refused while a sweep runs. */
export function clearVerdicts(): Promise<{ dropped: number }> {
	return request<{ dropped: number }>('/api/library/clear', { method: 'POST', body: '{}' });
}

export type RunMode = 'report' | 'apply';

// Why Process cannot be pressed on a report-only install. See RunButtons.
export const REPORT_ONLY_NOTE = 'This install is set to Report only, so nothing is changed.';

/**
 * Re-probe the chosen titles now, ignoring the cache. `report` plans, `apply`
 * rewrites; REWRITE_MODE latches over both, so read `may_rewrite` first.
 * Refused while a sweep or another re-check runs.
 */
export function runTitles(ids: string[], mode: RunMode): Promise<{ run: string; titles: number }> {
	return request<{ run: string; titles: number }>('/api/library/run', {
		method: 'POST',
		body: JSON.stringify({ ids, mode })
	});
}

export function coverUrl(id: string): string {
	return `/api/library/cover?id=${encodeURIComponent(id)}`;
}

// Which current tracks survive and which planned ones are new, paired by source
// index so the sheet can strike a dropped row.
export type Pairing = {
	kept: Set<number>;
	added: Set<number>;
};

function pair(file: { tracks: Track[]; planned: Track[] }): Pairing {
	// No plan means every track is kept.
	if (!file.planned.length) {
		return { kept: new Set(file.tracks.map((track) => track.index)), added: new Set() };
	}
	const kept = new Set<number>();
	const added = new Set<number>();
	for (const track of file.planned) {
		if (track.flags?.includes('generated')) added.add(track.index);
		else if (track.src !== undefined) kept.add(track.src);
	}
	return { kept, added };
}

/**
 * The same pairing for a rewrite that has already happened, where its record
 * kept the tracks. `kept` indexes what it started from and `added` the file it
 * left, as with a plan.
 *
 * Null where the rewrite moved no track, a remux or a cleared title: nothing
 * was dropped and nothing is new, so the list has only itself to show.
 */
function paired(rewrote: Modified): Pairing | null {
	if (!rewrote.was?.length || !(rewrote.dropped?.length || rewrote.added?.length)) return null;
	const dropped = new Set(rewrote.dropped ?? []);
	return {
		kept: new Set(rewrote.was.map((track) => track.index).filter((index) => !dropped.has(index))),
		added: new Set(rewrote.added ?? [])
	};
}

// One track on its way through a rewrite. `position` is the place it takes in
// the file left behind, counting from one, and null for a track that does not
// survive to have one.
export type Row = {
	track: Track;
	position: number | null;
	state: 'kept' | 'added' | 'dropped';
};

/**
 * Both track lists as one, in the order the rewrite leaves them.
 *
 * Read side by side the two lists were mostly the same list twice, since a
 * rewrite copies far more than it touches. Here every track appears once and
 * its place in the output is the number beside it, so what the rewrite did is
 * the rows that have no number and the rows marked new.
 *
 * A dropped track follows the last survivor of its own kind rather than the
 * position it held, which no longer exists. Where the output has none of that
 * kind left, its tracks come last.
 */
export function unify(before: Track[], after: Track[], mark: Pairing): Row[] {
	const dropped = before.filter((track) => !mark.kept.has(track.index));
	// Where each kind's survivors run out, so its drops can follow them.
	const ends = new Map<string, number>();
	after.forEach((track, at) => ends.set(track.kind, at));

	const rows: Row[] = [];
	after.forEach((track, at) => {
		rows.push({
			track,
			position: at + 1,
			state: mark.added.has(track.index) ? 'added' : 'kept'
		});
		if (ends.get(track.kind) !== at) return;
		for (const missing of dropped) {
			if (missing.kind === track.kind)
				rows.push({ track: missing, position: null, state: 'dropped' });
		}
	});
	for (const missing of dropped) {
		if (!ends.has(missing.kind)) rows.push({ track: missing, position: null, state: 'dropped' });
	}
	return rows;
}

// A file's tracks as one numbered list, and what the numbering is of: what a
// run would leave, what one left, or just the file as it stands. A plan wins
// over a rewrite already made, being the more useful answer, and only a rules
// change leaves a file with both.
export function listing(file: LibraryFile): { label: string; rows: Row[] } {
	if (file.planned.length) {
		return { label: 'After the rewrite', rows: unify(file.tracks, file.planned, pair(file)) };
	}
	const was = file.modified?.was ?? [];
	const moved = file.modified && was.length ? paired(file.modified) : null;
	// The after is the file as it was probed once rewritten, not the plan's word
	// for what it would be.
	if (moved) return { label: 'As rewritten', rows: unify(was, file.tracks, moved) };
	const whole = {
		kept: new Set(file.tracks.map((track) => track.index)),
		added: new Set<number>()
	};
	return { label: 'Tracks', rows: unify(file.tracks, file.tracks, whole) };
}

// Everything the app knows how to say about one verdict.
type Words = {
	// The one word a card, a filter and a sheet header share. Most name the
	// state of a file rather than anything done to it: `conform` is "Passed",
	// not "Done".
	label: string;
	// The sentence behind the word, for a `title` attribute. Most say whether
	// the file on disk was touched.
	hint: string;
	// The verdict as a dot, on a card and on a chip.
	pip: string;
	// The same dot over artwork, where the pip is a ring or wants pulling back.
	// See dot().
	onArt?: string;
	// The same colour as a switched-on filter chip. Absent on the verdicts no
	// chip row offers.
	tint?: string;
	// The same colour as a word, for a title's sheet and its files.
	text: string;
};

// Every verdict in one place: adding one is a single entry here, and the maps
// and lookups below follow.
//
// Three hues say what state the file is in: accent is work outstanding, danger
// is trouble, ok is as it should be. So `modified` shares `conform`'s green and
// only `pending` keeps the accent. A state with no colour of its own takes the
// raised surface the rest of the app uses for on.
const VOCABULARY: Record<Verdict, Words> = {
	failed: {
		label: 'Failed',
		hint: 'The rewrite was tried and broke. The file is as it was.',
		pip: 'bg-danger',
		tint: 'border-danger/50 bg-danger/12',
		text: 'text-danger'
	},
	pending: {
		label: 'Pending',
		hint: 'A rewrite would change this file. Nothing has been written yet.',
		pip: 'bg-accent',
		tint: 'border-accent/50 bg-accent/12',
		text: 'text-accent'
	},
	deferred: {
		label: 'Waiting',
		hint: 'Nothing was written: the file changed mid-rewrite, the run stopped, or a download client still hard-links it. The next sweep tries again.',
		pip: 'bg-danger/50',
		text: 'text-danger/70'
	},
	skip: {
		label: 'Skipped',
		hint: 'Not eligible for a rewrite at all.',
		pip: 'bg-faint',
		tint: 'border-line-strong bg-raised',
		text: 'text-faint'
	},
	unsupported: {
		label: 'Unsupported',
		hint: 'A container trackstarr does not rewrite, so the file was never opened. Containers under Rules decides which.',
		// Hollow in the accent: something to attend to that is not ours to do.
		pip: 'border border-accent',
		onArt: 'bg-accent/70',
		// Edge coloured, fill neutral, since a filled one is Pending's.
		tint: 'border-accent/50 bg-raised',
		text: 'text-accent'
	},
	conform: {
		label: 'Passed',
		hint: 'Already meets the rules. Nothing to do.',
		pip: 'bg-ok',
		tint: 'border-ok/50 bg-ok/12',
		text: 'text-ok'
	},
	modified: {
		label: 'Modified',
		// Only the run and history rows ask for a hint, so this is the file's
		// reading. A card says the same thing with the mark after its verdict,
		// and how much of the title in its label; see PosterCard.
		hint: 'Rewritten. The file on disk is the new one.',
		pip: 'bg-ok',
		// Edge coloured, fill neutral, so a filter for what we rewrote is told
		// apart from Passed beside it.
		tint: 'border-ok/50 bg-raised',
		text: 'text-ok'
	},
	mixed: {
		label: 'Mixed',
		hint: 'Nothing outstanding, but the files do not all say the same thing. The dots say which states are in it.',
		// A fallback only: a mixed card draws a dot per state it holds. See PosterCard.
		pip: 'bg-ok/60',
		text: 'text-ok/80'
	},
	unchecked: {
		// Where a word this build does not know lands, since that is what it is.
		// See asVerdict().
		label: 'Unknown',
		hint: 'No verdict yet. The files exist, but no sweep has reached them since they were written.',
		pip: 'bg-line-strong',
		tint: 'border-line-strong bg-raised',
		text: 'text-faint'
	},
	missing: {
		label: 'Missing',
		hint: 'Nothing downloaded. Radarr or Sonarr tracks this title, but there is no file to judge yet.',
		// A ring for a title with no file in it.
		pip: 'border border-faint',
		// Filled for a poster: a 1px ring at 6px over artwork is mostly poster
		// showing through, and two side by side read as one smudge.
		onArt: 'bg-white/40',
		tint: 'border-line-strong bg-raised',
		text: 'text-faint'
	}
};

// One field of the vocabulary as its own map, for the call sites that index a
// verdict straight into a class. Keyed by every verdict, and the value type
// follows the field: `tint` says it can be missing where `pip` cannot. The two
// assertions hold because the record is keyed by Verdict to begin with.
function column<Value extends string | undefined>(
	pick: (words: Words) => Value
): Record<Verdict, Value> {
	const picked = {} as Record<Verdict, Value>;
	for (const [verdict, words] of Object.entries(VOCABULARY) as [Verdict, Words][]) {
		picked[verdict] = pick(words);
	}
	return picked;
}

export const pip = column((words) => words.pip);
export const tint = column((words) => words.tint);
export const verdictText = column((words) => words.text);

/** Whether a word is one of ours, for the feeds that hold a word we never gave
 * them. Off ``Object.hasOwn``, so "constructor" is not a verdict. */
export function isVerdict(value: string): value is Verdict {
	return Object.hasOwn(VOCABULARY, value);
}

/** A verdict off the wire, or `unchecked` for a word this build has never heard
 * of, which is what it amounts to here: nothing it can draw. */
export function asVerdict(value: string): Verdict {
	return isVerdict(value) ? value : 'unchecked';
}

/** A state's dot as a card draws it: the chip's colour, filled where that one
 * is a ring. */
export function dot(state: Verdict): string {
	return VOCABULARY[state].onArt ?? VOCABULARY[state].pip;
}

// The two lists below each name their own subset of the vocabulary in their own
// order, so neither is derived from it: the order is meaning, and fixtures.test
// holds both to the service's own lists.
//
// Worst first: the order the service sorts the shelf in, and every state a file
// can be in. What hiding reads, and what a title's own files are labelled with.
export const VERDICTS: Verdict[] = [
	'failed',
	'pending',
	'skip',
	'unsupported',
	'conform',
	'unchecked',
	'missing'
];

// What a chip row cuts the grid by. `modified` reads off a card's own
// count, so "what have I rewritten" is a filter and not a badge to go hunting
// for. Hiding reads VERDICTS above, since it goes on the word a card leads with
// and `modified` is never one.
export const MODIFIED: Verdict = 'modified';
export const FILTERS: Verdict[] = [
	'failed',
	'pending',
	'skip',
	'unsupported',
	'conform',
	MODIFIED,
	'unchecked',
	'missing'
];

// The kinds a shelf can hold, in the order a filter offers them. Anything else
// an *arr reports keeps its own place at the end; see kindsOn in $lib/shelfview.
export const KINDS: Kind[] = ['movie', 'series', 'folder'];

/** The plural a filter names a kind by, standing for a set of titles. */
export function kindLabel(kind: Kind): string {
	switch (kind) {
		case 'movie':
			return 'Films';
		case 'series':
			return 'Series';
		case 'folder':
			return 'Folders';
		default:
			return 'Other';
	}
}

/** The same word for one title, as its sheet reads it. */
export function kindName(kind: Kind): string {
	switch (kind) {
		case 'movie':
			return 'Film';
		case 'series':
			return 'Series';
		case 'folder':
			return 'Folder';
		default:
			return 'Title';
	}
}

// The two verdicts an Appearance setting can hide from the grid. `missing` is
// a title with nothing downloaded, so a grid led by them is a wishlist;
// `unsupported` is a standing answer somebody may have decided to live with.
export const MISSING: Verdict = 'missing';
export const UNSUPPORTED: Verdict = 'unsupported';

/** A title's initials, for a tile whose poster the *arrs never had. */
export function initials(name: string): string {
	return name
		.split(/\s+/)
		.slice(0, 2)
		.map((word) => word[0] ?? '')
		.join('')
		.toUpperCase();
}

/** The one-word label a card, a filter and a sheet header share. `missing` is a
 * title with no file; `unchecked` has files no sweep has reached, or a rewrite
 * nothing has looked at since. */
export function verdictLabel(state: Verdict): string {
	return VOCABULARY[state].label;
}

/** The sentence behind the word, for a `title` attribute. */
export function verdictHint(state: Verdict): string {
	return VOCABULARY[state].hint;
}
