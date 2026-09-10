import { describe, expect, test } from 'vitest';
import { detail, details, headline, type Event } from '$lib/events';

function entry(over: Partial<Event> = {}): Event {
	return {
		ts: '2026-09-08T16:00:00+12:00',
		event: 'held',
		version: '1',
		...over
	};
}

describe('the headline of a hold', () => {
	const path = '/data/media/tv/MINDHUNTER (2017) {tvdb-328708}';

	test("says the library's name for the title where the page has the card", () => {
		expect(headline(entry({ path }), 'Mindhunter')).toBe('Held Mindhunter');
		expect(headline(entry({ event: 'lifted', path }), 'Mindhunter')).toBe(
			'Hold lifted on Mindhunter'
		);
	});

	test('falls back to the folder without what the *arr hung off it', () => {
		expect(headline(entry({ path }))).toBe('Held MINDHUNTER (2017)');
	});

	test('opens on a path, since a title is held by its folder', () => {
		const labels = (line: Event) => details(line).map((row) => row.label);
		expect(labels(entry({ path }))).toContain('Path');
		expect(labels(entry({ event: 'lifted', path }))).toContain('Path');
		// A skip is on one file, and says so.
		expect(labels(entry({ event: 'skipped', path: '/data/media/tv/S01E01.mkv' }))).toContain(
			'File'
		);
	});
});

describe('a track edited in place', () => {
	const line = entry({
		event: 'retagged',
		path: '/data/media/tv/Severance/Season 01/Severance - S01E02 - Half Loop.mkv',
		index: 1,
		kind: 'audio',
		changed: { lang: { from: 'und', to: 'jpn' }, commentary: { from: false, to: true } },
		by: 'admin'
	});

	test('is headlined by its title and detailed by its track', () => {
		// The card's name where the page has it, the file's own otherwise.
		expect(headline(line, 'Severance (2022)')).toBe('Retagged Severance (2022)');
		expect(headline(line)).toBe('Retagged Severance');
		expect(detail(line)).toBe('Audio stream 1 · language und to jpn · commentary on');
	});

	test('opens on the file, the track and each tag moved', () => {
		const rows = details(line);
		// Its own rows; the tail every event shares follows.
		expect(rows.slice(0, 4).map((row) => row.label)).toEqual(['File', 'Track', 'Changed', 'By']);
		expect(rows[2].values).toEqual(['language und to jpn', 'commentary on']);
	});

	test('a line recorded without a kind still reads, and SDH keeps its case', () => {
		const line = entry({
			event: 'retagged',
			kind: '',
			changed: { forced: { from: true, to: false }, sdh: { from: false, to: true } }
		});
		expect(detail(line)).toBe('Track stream ? · forced off · SDH on');
	});
});
