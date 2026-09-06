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

// Why a file is what it is: the lists a "fixed" event records, plus the skip.
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
// the two track lists are the account.
export type Fixed = {
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

export type Verdict =
	'would-fix' | 'conform' | 'skip' | 'unsupported' | 'failed' | 'unchecked' | 'missing' | string;

export type Card = {
	id: string;
	name: string;
	kind: 'movie' | 'series' | 'folder' | string;
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
	fixed?: number;
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
	// everything but a would-fix.
	tracks: Track[];
	planned: Track[];
	why: Why;
	// Absent on a file no rewrite of ours has published.
	fixed?: Fixed;
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

export function getShelf(fetcher: typeof fetch = fetch): Promise<Shelf> {
	return request<Shelf>('/api/library', undefined, fetcher);
}

export function getSummary(
	fetcher: typeof fetch = fetch,
	sort: Sort = 'processed'
): Promise<Summary> {
	return request<Summary>(`/api/library/summary?sort=${sort}`, undefined, fetcher);
}

export function getTitle(id: string, fetcher: typeof fetch = fetch): Promise<TitleDetail> {
	return request<TitleDetail>(
		`/api/library/title?id=${encodeURIComponent(id)}`,
		undefined,
		fetcher
	);
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

// Channel counts as layouts, so a track reads "5.1" like DOWNMIX_LAYOUTS.
const LAYOUTS: Record<number, string> = { 1: '1.0', 2: '2.0', 3: '2.1', 6: '5.1', 8: '7.1' };

export function layout(channels: number | undefined): string {
	if (!channels) return '';
	return LAYOUTS[channels] ?? `${channels}ch`;
}

export function rate(bitrate: number | undefined): string {
	if (!bitrate) return '';
	if (bitrate >= 1_000_000) return `${(bitrate / 1_000_000).toFixed(1)} Mbps`;
	// A text subtitle runs at tens of bits a second, which rounded to "0k".
	if (bitrate < 1000) return `${bitrate} bps`;
	return `${Math.round(bitrate / 1000)}k`;
}

// A track on one line: codec, layout, language. The same shape for current and
// planned tracks so the two columns compare.
export function describe(track: Track): string {
	const parts = [track.codec?.toUpperCase()];
	if (track.kind === 'audio') parts.push(layout(track.channels));
	parts.push(track.lang ?? 'und');
	return parts.filter(Boolean).join(' · ');
}

// Which current tracks survive and which planned ones are new, paired by source
// index so the sheet can strike a dropped row.
export type Pairing = {
	kept: Set<number>;
	added: Set<number>;
};

export function pair(file: { tracks: Track[]; planned: Track[] }): Pairing {
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
 * Null where the rewrite moved no track, a remux or a cleared title: the two
 * columns would be the same list twice, which says less than the one.
 */
export function paired(fixed: Fixed): Pairing | null {
	if (!fixed.was?.length || !(fixed.dropped?.length || fixed.added?.length)) return null;
	const dropped = new Set(fixed.dropped ?? []);
	return {
		kept: new Set(fixed.was.map((track) => track.index).filter((index) => !dropped.has(index))),
		added: new Set(fixed.added ?? [])
	};
}

// Each verdict's colour as a dot. Three hues say what state the file is in:
// accent is work outstanding, danger is trouble, ok is as it should be. So
// `fixed` shares `conform`'s green and only `would-fix` keeps the accent.
export const pip: Record<string, string> = {
	fixed: 'bg-ok',
	'would-fix': 'bg-accent',
	failed: 'bg-danger',
	deferred: 'bg-danger/50',
	conform: 'bg-ok',
	skip: 'bg-faint',
	// Hollow in the accent: something to attend to that is not ours to do.
	unsupported: 'border border-accent',
	unchecked: 'bg-line-strong',
	// A ring for a title with no file in it.
	missing: 'border border-faint'
};

// The same colours as a switched-on filter chip. The states with no colour of
// their own take the raised surface the rest of the app uses for on.
export const tint: Record<string, string> = {
	'would-fix': 'border-accent/50 bg-accent/12',
	conform: 'border-ok/50 bg-ok/12',
	skip: 'border-line-strong bg-raised',
	// Edge coloured, fill neutral, since a filled one is Pending's.
	unsupported: 'border-accent/50 bg-raised',
	failed: 'border-danger/50 bg-danger/12',
	unchecked: 'border-line-strong bg-raised',
	missing: 'border-line-strong bg-raised'
};

// The same colours as a word, for a title's sheet and its files.
export const verdictText: Record<string, string> = {
	fixed: 'text-ok',
	'would-fix': 'text-accent',
	failed: 'text-danger',
	deferred: 'text-danger/70',
	conform: 'text-ok',
	skip: 'text-faint',
	unsupported: 'text-accent',
	unchecked: 'text-faint',
	missing: 'text-faint'
};

// Worst first: the order the service sorts the shelf in.
export const VERDICTS: Verdict[] = [
	'failed',
	'would-fix',
	'skip',
	'unsupported',
	'conform',
	'unchecked',
	'missing'
];

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

// The one-word label a card, a filter and a sheet header share. Most name the
// state of a file rather than anything done to it: `conform` is "Passed", not
// "Done". `missing` is a title with no file; `unchecked` has files no sweep has
// reached, or a rewrite nothing has looked at since.
export function verdictLabel(state: Verdict): string {
	switch (state) {
		case 'fixed':
			return 'Fixed';
		case 'would-fix':
			return 'Pending';
		case 'failed':
			return 'Failed';
		case 'deferred':
			return 'Waiting';
		case 'conform':
			return 'Passed';
		case 'skip':
			return 'Skipped';
		case 'unsupported':
			return 'Unsupported';
		case 'missing':
			return 'Missing';
		default:
			return 'Unknown';
	}
}

// The sentence behind the word, for a `title` attribute. Most say whether the
// file on disk was touched.
export function verdictHint(state: Verdict): string {
	switch (state) {
		case 'fixed':
			return 'Rewritten. The file on disk is the new one.';
		case 'would-fix':
			return 'A rewrite would change this file. Nothing has been written yet.';
		case 'failed':
			return 'The rewrite was tried and broke. The file is as it was.';
		case 'deferred':
			return 'Nothing was written: the file changed mid-rewrite, the run stopped, or a download client still hard-links it. The next sweep tries again.';
		case 'conform':
			return 'Already meets the rules. Nothing to do.';
		case 'skip':
			return 'Not eligible for a rewrite at all.';
		case 'unsupported':
			return 'A container trackstarr does not rewrite, so the file was never opened. Containers under Rules decides which.';
		case 'missing':
			return 'Nothing downloaded. Radarr or Sonarr tracks this title, but there is no file to judge yet.';
		default:
			return 'No verdict yet. The files exist, but no sweep has reached them since they were written.';
	}
}
