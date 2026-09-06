import { describe, expect, test } from 'vitest';
import type { Card, Verdict } from '$lib/library';
import { elsewhere, everything, holds, sift, tally, waiting, type View } from '$lib/shelfview';

// The shelf arrives worst first, since the service sorts it that way, and the
// grid is expected to leave it in that order until an order is asked for.
const SHELF: Card[] = [
	card('a', 'Arrival', 'failed', { added: 30, year: 2016 }),
	card('b', 'Blade Runner', 'would-fix', { added: 10, year: 1982 }),
	card('c', 'Contact', 'conform', { added: 20, year: 1997 }),
	card('d', 'Dune', 'missing', { added: 40, year: 2021 }),
	card('e', 'Eraserhead', 'unsupported', { added: 50, year: 1977 })
];

function card(id: string, name: string, state: Verdict, rest: Partial<Card> = {}): Card {
	return { id, name, kind: 'movie', state, ...rest };
}

function view(over: Partial<View> = {}): View {
	return { filters: [], hidden: [], needle: '', sort: 'worst', flow: 'desc', ...over };
}

describe('holds', () => {
	test('shows everything Appearance is not keeping out', () => {
		const showing = view({ hidden: ['missing'] });
		expect(holds('conform', showing)).toBe(true);
		expect(holds('missing', showing)).toBe(false);
	});

	// A chip that selects nothing is a trap: a search for a film you have not
	// got is exactly when the hidden state is wanted.
	test('shows a hidden state anyway once its own chip is held', () => {
		expect(holds('missing', view({ filters: ['missing'], hidden: ['missing'] }))).toBe(true);
		expect(holds('conform', view({ filters: ['missing'], hidden: ['missing'] }))).toBe(false);
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

	test('leaves the shelf itself alone', () => {
		sift(SHELF, view({ sort: 'name', flow: 'asc' }));
		expect(SHELF.map((title) => title.id)).toEqual(['a', 'b', 'c', 'd', 'e']);
	});
});

describe('tally and everything', () => {
	test('counts each verdict', () => {
		expect(tally(SHELF)).toEqual({
			failed: 1,
			'would-fix': 1,
			conform: 1,
			missing: 1,
			unsupported: 1
		});
	});

	test('takes the hidden verdicts off what All stands for', () => {
		const counts = tally(SHELF);
		expect(everything(counts, SHELF.length, [])).toBe(5);
		expect(everything(counts, SHELF.length, ['missing', 'unsupported'])).toBe(3);
	});
});

describe('waiting', () => {
	test('says nothing about a shelf with verdicts on it', () => {
		expect(waiting(tally(SHELF), SHELF.length)).toBeNull();
	});

	test('counts the fresh install as every judgeable title', () => {
		// The four with files, plus Dune, which nobody has downloaded.
		const fresh = SHELF.map((title) => card(title.id, title.name, 'unchecked'));
		fresh[3] = card('d', 'Dune', 'missing');
		expect(waiting(tally(fresh), fresh.length)).toEqual({ titles: 4, judgeable: 4 });
	});

	test('holds off until the grid is mostly one word', () => {
		const half = [...SHELF.slice(0, 3), card('u', 'Under the Skin', 'unchecked')];
		expect(waiting(tally(half), half.length)).toBeNull();
		const most = [...half, card('v', 'Videodrome', 'unchecked'), card('w', 'Wings', 'unchecked')];
		expect(waiting(tally(most), most.length)).toEqual({ titles: 3, judgeable: 6 });
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
