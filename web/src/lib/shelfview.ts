// What the library grid shows, from the shelf and the controls over it: which
// titles in what order, how many each verdict holds, how many All stands for,
// where a failed search would have landed, and whether the grid is mostly
// unswept. Pure functions, so a test can put a fixture shelf through them.

import { KINDS, MISSING, VERDICTS, type Card, type Kind, type Verdict } from '$lib/library';
import { FLOW, ORDER, type Flow, type Sort } from '$lib/order.svelte';

/** The row of controls over the grid, as the cut below reads it. */
export type View = {
	// Which verdicts the reader is holding, empty being all of them.
	filters: readonly Verdict[];
	// Which verdicts Appearance keeps out of the default view.
	hidden: readonly Verdict[];
	// Which kind the grid is cut to; empty is all of them.
	kind: Kind;
	// What is in the search box, trimmed and lowercased.
	needle: string;
	sort: Sort;
	flow: Flow;
};

/** One hit for a verdict the filters are holding back. */
export type Hit = { state: Verdict; count: number };

/** The same for a kind the type filter is holding back. */
export type KindHit = { kind: Kind; count: number };

/** Whether the kind filter would show this title. */
export function ofKind(card: Card, kind: Kind): boolean {
	return !kind || card.kind === kind;
}

/** The kinds the shelf holds, in the order the filter offers them. An install
 * with only Radarr connected gets one, and no control at all. */
export function kindsOn(titles: readonly Card[]): Kind[] {
	const held = new Set(titles.map((card) => card.kind));
	const known = KINDS.filter((kind) => held.has(kind));
	// Anything an *arr reports that this build has no word for still gets a
	// segment, after the three it does.
	const rest = [...held].filter((kind) => !KINDS.includes(kind)).sort();
	return [...known, ...rest];
}

/** Whether the filters would show a title in this state. With none held the
 * grid is everything less what Appearance hides; a hidden state held by name
 * shows anyway. */
export function holds(state: string, view: Pick<View, 'filters' | 'hidden'>): boolean {
	return view.filters.length ? view.filters.includes(state) : !view.hidden.includes(state);
}

/** The shelf as the filters, the search and the order leave it. */
export function sift(titles: readonly Card[], view: View): Card[] {
	const { needle, sort, flow } = view;
	const kept = titles.filter(
		(card) =>
			ofKind(card, view.kind) &&
			holds(card.state, view) &&
			(!needle || card.name.toLowerCase().includes(needle))
	);
	const order = ORDER[sort];
	// The comparison reversed rather than a second comparator. Worst first has
	// no comparator; it is the order the shelf arrives in.
	const back = flow !== FLOW[sort];
	if (!order) return back ? kept.reverse() : kept;
	// Ties go to the name, or a size the sweep has not costed would reorder on
	// every reload.
	const compare = (a: Card, b: Card) => order(a, b) || a.name.localeCompare(b.name);
	return kept.sort(back ? (a, b) => compare(b, a) : compare);
}

/** How many titles each verdict holds, for the filter counts. */
export function tally(titles: readonly Card[]): Record<string, number> {
	return titles.reduce<Record<string, number>>((counts, card) => {
		counts[card.state] = (counts[card.state] ?? 0) + 1;
		return counts;
	}, {});
}

/** How many titles All stands for: everything less what Appearance hides. */
export function everything(
	counts: Record<string, number>,
	total: number,
	hidden: readonly Verdict[]
): number {
	return hidden.reduce((left, state) => left - (counts[state] ?? 0), total);
}

// Files on disk with no verdict: work not done, unlike `missing`.
const UNJUDGED: Verdict = 'unchecked';

// How much of a judgeable library must be waiting before the grid explains
// itself: where gaps become a wall of one word.
const A_WALL = 0.5;

/** How many titles are waiting on a sweep, out of how many could be judged. */
export type Waiting = { titles: number; judgeable: number };

/** Whether the grid is mostly Unknown, as a fresh install is, and by how much.
 * Null once enough is judged. `missing` is left out of both numbers, since no
 * sweep will ever judge it. */
export function waiting(counts: Record<string, number>, total: number): Waiting | null {
	const titles = counts[UNJUDGED] ?? 0;
	const judgeable = total - (counts[MISSING] ?? 0);
	if (!titles || titles < judgeable * A_WALL) return null;
	return { titles, judgeable };
}

/** Where a search that found nothing would have landed without the verdict
 * filters, so "do I have this" is not answered "no" by a hidden Missing. Only
 * within the kind on screen, or a chip would promise titles the type filter
 * then withholds. */
export function elsewhere(titles: readonly Card[], view: View): Hit[] {
	if (!view.needle) return [];
	const counts: Record<string, number> = {};
	for (const card of titles) {
		if (
			ofKind(card, view.kind) &&
			!holds(card.state, view) &&
			card.name.toLowerCase().includes(view.needle)
		) {
			counts[card.state] = (counts[card.state] ?? 0) + 1;
		}
	}
	return VERDICTS.filter((state) => counts[state]).map((state) => ({
		state,
		count: counts[state]
	}));
}

/** The same for the kinds the type filter is holding back: a grid cut to films
 * should not answer "have I got this series" with no. Counted among titles the
 * verdict filters would show, so pressing one lands on them. */
export function otherKinds(titles: readonly Card[], view: View): KindHit[] {
	if (!view.needle || !view.kind) return [];
	const counts: Record<string, number> = {};
	for (const card of titles) {
		if (
			!ofKind(card, view.kind) &&
			holds(card.state, view) &&
			card.name.toLowerCase().includes(view.needle)
		) {
			counts[card.kind] = (counts[card.kind] ?? 0) + 1;
		}
	}
	return kindsOn(titles)
		.filter((kind) => counts[kind])
		.map((kind) => ({ kind, count: counts[kind] }));
}
