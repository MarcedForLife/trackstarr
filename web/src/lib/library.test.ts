import { describe, expect, test } from 'vitest';
import {
	asVerdict,
	FILTERS,
	judged,
	listing,
	unify,
	VERDICTS,
	type Card,
	type LibraryFile,
	type Track,
	type Verdict
} from '$lib/library';

// Shorthand for the fields the unified list reads.
function track(index: number, kind: string, extra: Partial<Track> = {}): Track {
	return { index, kind, ...extra };
}

function file(over: Partial<LibraryFile> = {}): LibraryFile {
	return {
		path: '/tv/Show/one.mkv',
		name: 'one.mkv',
		status: 'conform',
		bytes: 1,
		seconds: 3600,
		tracks: [],
		planned: [],
		why: {},
		...over
	};
}

describe('unify', () => {
	test('numbers the output and leaves a dropped track without one', () => {
		const before = [track(0, 'video'), track(1, 'audio'), track(2, 'audio')];
		const after = [track(0, 'video', { src: 0 }), track(1, 'audio', { src: 1 })];
		const rows = unify(before, after, { kept: new Set([0, 1]), added: new Set() });

		expect(rows.map((row) => [row.position, row.state])).toEqual([
			[1, 'kept'],
			[2, 'kept'],
			[null, 'dropped']
		]);
		expect(rows[2].track.index).toBe(2);
	});

	test('a dropped track follows the last survivor of its own kind', () => {
		const before = [track(0, 'video'), track(1, 'audio'), track(2, 'audio'), track(3, 'subtitle')];
		const after = [
			track(0, 'video', { src: 0 }),
			track(1, 'audio', { src: 1 }),
			track(2, 'subtitle', { src: 3 })
		];
		const rows = unify(before, after, { kept: new Set([0, 1, 3]), added: new Set() });

		// The dropped audio stands among the audio, not after the subtitles.
		expect(rows.map((row) => [row.track.kind, row.position])).toEqual([
			['video', 1],
			['audio', 2],
			['audio', null],
			['subtitle', 3]
		]);
	});

	test('a kind the output keeps none of comes last', () => {
		const before = [track(0, 'video'), track(1, 'subtitle'), track(2, 'subtitle')];
		const after = [track(0, 'video', { src: 0 })];
		const rows = unify(before, after, { kept: new Set([0]), added: new Set() });

		expect(rows.map((row) => [row.track.index, row.position])).toEqual([
			[0, 1],
			[1, null],
			[2, null]
		]);
	});

	test('a generated track is marked new and still takes a place', () => {
		const before = [track(0, 'audio', { channels: 6 })];
		const after = [
			track(0, 'audio', { src: 0, channels: 6 }),
			track(1, 'audio', { channels: 2, flags: ['generated'] })
		];
		const rows = unify(before, after, { kept: new Set([0]), added: new Set([1]) });

		expect(rows.map((row) => [row.position, row.state])).toEqual([
			[1, 'kept'],
			[2, 'added']
		]);
	});
});

describe('listing', () => {
	test('a plan is what the file is about to become', () => {
		const shown = listing(
			file({
				tracks: [track(0, 'audio'), track(1, 'audio')],
				planned: [track(0, 'audio', { src: 1 })]
			})
		);

		expect(shown.label).toBe('After the rewrite');
		expect(shown.rows.map((row) => row.position)).toEqual([1, null]);
	});

	test('a rewrite already made is read from what it recorded', () => {
		const shown = listing(
			file({
				tracks: [track(0, 'audio')],
				modified: {
					at: '2026-09-01T00:00:00Z',
					was: [track(0, 'audio'), track(1, 'audio')],
					dropped: [1]
				}
			})
		);

		expect(shown.label).toBe('As rewritten');
		expect(shown.rows.map((row) => row.state)).toEqual(['kept', 'dropped']);
	});

	test('a plan wins over a rewrite a rules change left behind', () => {
		const shown = listing(
			file({
				tracks: [track(0, 'audio')],
				planned: [track(0, 'audio', { src: 0 })],
				modified: {
					at: '2026-09-01T00:00:00Z',
					was: [track(0, 'audio'), track(1, 'audio')],
					dropped: [1]
				}
			})
		);

		expect(shown.label).toBe('After the rewrite');
	});

	test('a file nothing has touched is just its own tracks, numbered', () => {
		const shown = listing(file({ tracks: [track(0, 'video'), track(1, 'audio')] }));

		expect(shown.label).toBe('Tracks');
		expect(shown.rows.map((row) => [row.position, row.state])).toEqual([
			[1, 'kept'],
			[2, 'kept']
		]);
	});

	test('a plan names each row on disk: the kept row by its source, the generated row not at all', () => {
		const planned = listing(
			file({
				tracks: [track(0, 'video'), track(1, 'audio'), track(2, 'audio')],
				planned: [
					track(0, 'video', { src: 0 }),
					track(1, 'audio', { src: 2 }),
					track(2, 'audio', { src: 2, flags: ['generated'] })
				]
			})
		);
		expect(planned.rows.map((row) => [row.state, row.stream])).toEqual([
			['kept', 0],
			['kept', 2],
			['added', null],
			['dropped', 1]
		]);
	});

	test('a rewrite already made: what it dropped is gone, what it generated is in the file', () => {
		const rewritten = listing(
			file({
				tracks: [track(0, 'video'), track(1, 'audio')],
				modified: {
					at: '2026-09-01T00:00:00Z',
					was: [track(0, 'video'), track(1, 'audio'), track(2, 'audio')],
					dropped: [1, 2],
					added: [1]
				}
			})
		);
		expect(rewritten.rows.map((row) => [row.state, row.stream])).toEqual([
			['kept', 0],
			['added', 1],
			['dropped', null],
			['dropped', null]
		]);
	});

	test('a file as it stands is on disk row for row', () => {
		const plain = listing(file({ tracks: [track(0, 'video'), track(3, 'audio')] }));
		expect(plain.rows.map((row) => row.stream)).toEqual([0, 3]);
	});

	test('a rewrite that moved no track numbers the file as it stands', () => {
		const shown = listing(
			file({
				tracks: [track(0, 'audio')],
				// A remux: the record kept no lists, so there is nothing to compare.
				modified: { at: '2026-09-01T00:00:00Z' }
			})
		);

		expect(shown.label).toBe('Tracks');
		expect(shown.rows.map((row) => row.position)).toEqual([1]);
	});
});

describe('asVerdict and judged', () => {
	function card(over: Partial<Card> = {}): Card {
		return { id: 'arr:radarr:1', name: 'Dune', kind: 'movie', state: 'conform', ...over };
	}

	test('takes every word the app draws, deferred among them', () => {
		for (const word of [...VERDICTS, ...FILTERS, 'deferred', 'mixed']) {
			expect(asVerdict(word)).toBe(word);
		}
	});

	test('reads a word this build has never heard of as unchecked', () => {
		expect(asVerdict('quarantined')).toBe('unchecked');
		expect(asVerdict('')).toBe('unchecked');
		// Off Object.prototype, so a lookup by inherited name is not a verdict.
		expect(asVerdict('constructor')).toBe('unchecked');
	});

	test('narrows a fetched card in place, tally and all', () => {
		const shelf = [card({ counts: { quarantined: 3, conform: 1 } })];
		// The cast is the point: no card written in the app can hold this, and one
		// off a newer service can.
		shelf[0].state = 'quarantined' as Verdict;
		judged(shelf);

		expect(shelf[0].state).toBe('unchecked');
		// Folded, so the card's word and its chips cannot disagree.
		expect(shelf[0].counts).toEqual({ unchecked: 3, conform: 1 });
	});

	test('leaves a card whose words are all known alone', () => {
		const counts = { conform: 2 };
		const shelf = [card({ counts })];
		judged(shelf);

		expect(shelf[0].state).toBe('conform');
		expect(shelf[0].counts).toBe(counts);
	});
});
