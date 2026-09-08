import { describe, expect, test } from 'vitest';
import { details, headline, type Event } from '$lib/events';

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
