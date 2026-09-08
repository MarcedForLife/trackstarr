import { describe, expect, test } from 'vitest';
import type { Card, Verdict } from '$lib/library';
import {
	elsewhere,
	everything,
	headlines,
	holds,
	kindsOn,
	otherKinds,
	sift,
	tally,
	waiting,
	within,
	type View
} from '$lib/shelfview';

// The shelf arrives worst first, since the service sorts it that way, and the
// grid is expected to leave it in that order until an order is asked for.
const SHELF: Card[] = [
	card('a', 'Arrival', 'failed', { added: 30, year: 2016 }),
	card('b', 'Blade Runner', 'pending', { added: 10, year: 1982 }),
	card('c', 'Contact', 'conform', { added: 20, year: 1997 }),
	card('d', 'Dune', 'missing', { added: 40, year: 2021 }),
	card('e', 'Eraserhead', 'unsupported', { added: 50, year: 1977 })
];

function card(id: string, name: string, state: Verdict, rest: Partial<Card> = {}): Card {
	return { id, name, kind: 'movie', state, ...rest };
}

function view(over: Partial<View> = {}): View {
	return { filters: [], hidden: [], kind: '', needle: '', sort: 'worst', flow: 'desc', ...over };
}

// Mostly passed, with one file in a container the rules will never rewrite:
// the title the headline used to call Unsupported outright.
const MOSTLY = card('m', 'Mostly', 'mixed', { counts: { conform: 29, unsupported: 1 } });

describe('within', () => {
	test('finds every state a title holds a file in', () => {
		expect(within(MOSTLY, 'conform')).toBe(true);
		expect(within(MOSTLY, 'unsupported')).toBe(true);
		expect(within(MOSTLY, 'failed')).toBe(false);
	});

	// A wishlist entry has no files to tally, so its word is all it has.
	test('falls back to the headline on a title with no verdicts', () => {
		expect(within(card('d', 'Dune', 'missing'), 'missing')).toBe(true);
		expect(within(card('d', 'Dune', 'missing'), 'conform')).toBe(false);
	});

	// A rewritten file passes, so no verdict marks it and the count has to.
	test('reads Modified off the rewrite count, not the verdicts', () => {
		const ours = card('o', 'Ours', 'conform', { counts: { conform: 3 }, modified: 2 });
		expect(within(ours, 'modified')).toBe(true);
		expect(within(ours, 'conform')).toBe(true);
		expect(within(card('c', 'Contact', 'conform', { counts: { conform: 3 } }), 'modified')).toBe(
			false
		);
	});
});

describe('holds', () => {
	test('shows everything Appearance is not keeping out', () => {
		const showing = view({ hidden: ['missing'] });
		expect(holds(card('c', 'Contact', 'conform'), showing)).toBe(true);
		expect(holds(card('d', 'Dune', 'missing'), showing)).toBe(false);
	});

	// A chip that selects nothing is a trap: a search for a film you have not
	// got is exactly when the hidden state is wanted.
	test('shows a hidden state anyway once its own chip is held', () => {
		const held = view({ filters: ['missing'], hidden: ['missing'] });
		expect(holds(card('d', 'Dune', 'missing'), held)).toBe(true);
		expect(holds(card('c', 'Contact', 'conform'), held)).toBe(false);
	});

	// The point of counting membership: one skipped file no longer takes a title
	// out of Passed.
	test('finds a mixed title under every chip it holds a file for', () => {
		expect(holds(MOSTLY, view({ filters: ['conform'] }))).toBe(true);
		expect(holds(MOSTLY, view({ filters: ['unsupported'] }))).toBe(true);
		expect(holds(MOSTLY, view({ filters: ['skip'] }))).toBe(false);
	});

	// Hiding goes on the headline, or living with Unsupported would swallow the
	// mostly-passed titles that happen to contain one.
	test('keeps a mixed title that Appearance would hide the pure one of', () => {
		expect(holds(MOSTLY, view({ hidden: ['unsupported'] }))).toBe(true);
		expect(holds(card('e', 'Eraserhead', 'unsupported'), view({ hidden: ['unsupported'] }))).toBe(
			false
		);
	});
});

describe('sift', () => {
	test('leaves the shelf in its own order for worst first, and reverses it', () => {
		expect(sift(SHELF, view()).map((title) => title.id)).toEqual(['a', 'b', 'c', 'd', 'e']);
		expect(sift(SHELF, view({ flow: 'asc' })).map((title) => title.id)).toEqual([
			'e',
			'd',
			'c',
			'b',
			'a'
		]);
	});

	test('sorts on the chosen order, either way up', () => {
		const newest = sift(SHELF, view({ sort: 'added', flow: 'desc' }));
		expect(newest.map((title) => title.id)).toEqual(['e', 'd', 'a', 'c', 'b']);
		const oldest = sift(SHELF, view({ sort: 'added', flow: 'asc' }));
		expect(oldest.map((title) => title.id)).toEqual(['b', 'c', 'a', 'd', 'e']);
	});

	// Without the tie-break, an order the sweep has not costed yet would deal
	// the same titles out differently on every reload.
	test('breaks a tie on the name', () => {
		const untimed = [card('z', 'Zodiac', 'conform'), card('m', 'Memento', 'conform')];
		expect(sift(untimed, view({ sort: 'size' })).map((title) => title.id)).toEqual(['m', 'z']);
	});

	test('cuts to the filters and the search together', () => {
		const showing = view({ filters: ['failed', 'conform'], needle: 'c' });
		expect(sift(SHELF, showing).map((title) => title.id)).toEqual(['c']);
	});

	test('cuts to one kind, and to every kind on an empty one', () => {
		const mixed = [...SHELF, card('s', 'Silo', 'conform', { kind: 'series' })];
		expect(sift(mixed, view({ kind: 'series' })).map((title) => title.id)).toEqual(['s']);
		expect(sift(mixed, view({ kind: '' }))).toHaveLength(6);
	});

	test('leaves the shelf itself alone', () => {
		sift(SHELF, view({ sort: 'name', flow: 'asc' }));
		expect(SHELF.map((title) => title.id)).toEqual(['a', 'b', 'c', 'd', 'e']);
	});
});

describe('tally and everything', () => {
	test('counts each verdict', () => {
		expect(tally(SHELF)).toEqual({
			failed: 1,
			pending: 1,
			conform: 1,
			missing: 1,
			unsupported: 1
		});
	});

	// The chip promises what pressing it lands on, so a title in two states is
	// on both and the chips sum past the shelf.
	test('counts a mixed title under each state it holds', () => {
		expect(tally([...SHELF, MOSTLY])).toEqual({
			failed: 1,
			pending: 1,
			conform: 2,
			missing: 1,
			unsupported: 2
		});
	});

	test('counts headlines one to a title', () => {
		expect(headlines([...SHELF, MOSTLY])).toEqual({
			failed: 1,
			pending: 1,
			conform: 1,
			missing: 1,
			unsupported: 1,
			mixed: 1
		});
	});

	test('takes the hidden verdicts off what All stands for', () => {
		expect(everything(SHELF, [])).toBe(5);
		expect(everything(SHELF, ['missing', 'unsupported'])).toBe(3);
	});

	// Counted on the headline, so the mixed title survives both.
	test('keeps a mixed title in All whatever it holds', () => {
		expect(everything([...SHELF, MOSTLY], ['missing', 'unsupported'])).toBe(4);
	});
});

describe('waiting', () => {
	test('says nothing about a shelf with verdicts on it', () => {
		expect(waiting(headlines(SHELF), SHELF.length)).toBeNull();
	});

	test('counts the fresh install as every judgeable title', () => {
		// The four with files, plus Dune, which nobody has downloaded.
		const fresh = SHELF.map((title) => card(title.id, title.name, 'unchecked'));
		fresh[3] = card('d', 'Dune', 'missing');
		expect(waiting(headlines(fresh), fresh.length)).toEqual({ titles: 4, judgeable: 4 });
	});

	test('holds off until the grid is mostly one word', () => {
		const half = [...SHELF.slice(0, 3), card('u', 'Under the Skin', 'unchecked')];
		expect(waiting(headlines(half), half.length)).toBeNull();
		const most = [...half, card('v', 'Videodrome', 'unchecked'), card('w', 'Wings', 'unchecked')];
		expect(waiting(headlines(most), most.length)).toEqual({ titles: 3, judgeable: 6 });
	});
});

describe('kindsOn', () => {
	test('offers only the kinds the shelf holds, in the filter order', () => {
		expect(kindsOn(SHELF)).toEqual(['movie']);
		const mixed = [...SHELF, card('s', 'Silo', 'conform', { kind: 'series' })];
		expect(kindsOn(mixed)).toEqual(['movie', 'series']);
	});

	test('keeps a kind this build has no word for, after the ones it does', () => {
		const odd = [...SHELF, card('l', 'Lidarr thing', 'conform', { kind: 'album' })];
		expect(kindsOn(odd)).toEqual(['movie', 'album']);
	});
});

describe('otherKinds', () => {
	const MIXED = [...SHELF, card('s', 'Silo', 'conform', { kind: 'series' })];

	// Cutting to films is a visible state, but a search answered "no" still
	// reads as "you have not got it".
	test('names the kind a search cut out from under it', () => {
		const showing = view({ kind: 'movie', needle: 'silo' });
		expect(otherKinds(MIXED, showing)).toEqual([{ kind: 'series', count: 1 }]);
	});

	test('says nothing without a search, or with every kind showing', () => {
		expect(otherKinds(MIXED, view({ kind: 'movie' }))).toEqual([]);
		expect(otherKinds(MIXED, view({ needle: 'silo' }))).toEqual([]);
	});

	// Pressing it would land on the same empty grid.
	test('leaves out a title the verdict filters would hold back anyway', () => {
		const showing = view({ kind: 'movie', filters: ['failed'], needle: 'silo' });
		expect(otherKinds(MIXED, showing)).toEqual([]);
	});
});

describe('elsewhere', () => {
	test('names the verdicts holding what the search found, in the read order', () => {
		const showing = view({ hidden: ['missing', 'unsupported'], needle: 'e' });
		expect(elsewhere(SHELF, showing)).toEqual([
			{ state: 'unsupported', count: 1 },
			{ state: 'missing', count: 1 }
		]);
	});

	test('says nothing without a search', () => {
		expect(elsewhere(SHELF, view({ hidden: ['missing'] }))).toEqual([]);
	});
});
