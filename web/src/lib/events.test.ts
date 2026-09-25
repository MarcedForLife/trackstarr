import { describe, expect, test } from 'vitest';
import {
	detail,
	details,
	headline,
	measures,
	outcome,
	parts,
	searchable,
	type Event
} from '$lib/events';

function entry(over: Partial<Event> = {}): Event {
	return {
		ts: '2026-09-08T16:00:00+12:00',
		event: 'item_paused',
		version: '1',
		...over
	};
}

test.each(['rules', 'incidental_rules'] as const)(
	'Dolby Vision removal remains visible in events through %s',
	(field) => {
		const removed = entry({
			event: 'modified',
			[field]: ['dv_strip'],
			bytes_before: 100,
			bytes_after: 90
		});
		expect(measures(removed)).toContain('Dolby Vision removed');
		expect(measures({ ...removed, event: 'pending' })).toContain('Remove Dolby Vision');
		for (const event of ['failed', 'deferred', 'skipped']) {
			expect(measures({ ...removed, event })).not.toContain('Dolby Vision removed');
		}
		expect(measures(entry({ event: 'modified' }))).not.toContain('Dolby Vision removed');
	}
);

describe('a verdict headline', () => {
	test('uses the library title and keeps the release filename in the details', () => {
		const path = '/movies/Sintel (2010)/Sintel (2010) Bluray-1080p.mkv';
		const event = entry({ event: 'modified', path });
		expect(headline(event, 'Sintel')).toBe('Modified: Sintel');
		expect(headline(event)).toContain('Bluray-1080p');
		expect(details(event).find((row) => row.label === 'File')?.values).toEqual([path]);
	});
});

describe('the headline of a hold', () => {
	const path = '/data/media/tv/MINDHUNTER (2017) {tvdb-328708}';

	test("says the library's name for the title where the page has the card", () => {
		expect(headline(entry({ path }), 'Mindhunter')).toBe('Paused Mindhunter');
		expect(headline(entry({ event: 'item_resumed', path }), 'Mindhunter')).toBe(
			'Resumed Mindhunter'
		);
	});

	test('falls back to the folder without what the *arr hung off it', () => {
		expect(headline(entry({ path }))).toBe('Paused MINDHUNTER (2017)');
	});

	test('opens on a path, since a title is held by its folder', () => {
		const labels = (line: Event) => details(line).map((row) => row.label);
		expect(labels(entry({ path }))).toContain('Path');
		expect(labels(entry({ event: 'item_resumed', path }))).toContain('Path');
		// A skip is on one file, and says so.
		expect(labels(entry({ event: 'skipped', path: '/data/media/tv/S01E01.mkv' }))).toContain(
			'File'
		);
	});
});

describe('a file taken off its run', () => {
	const path = '/data/media/movies/Arrival (2016)/Arrival (2016) Bluray-1080p.mkv';
	const cancelled = entry({
		event: 'skipped',
		path,
		by: 'admin',
		where: 'active',
		detail: 'stopped 43% into the rewrite, nothing written',
		seconds: 41.7,
		reasons: ['add 2.0 downmix'],
		rules: ['downmix'],
		adds: ['2.0']
	});

	test('is named for the button that did it', () => {
		expect(outcome(cancelled).word).toBe('Cancelled');
		expect(outcome(entry({ event: 'skipped', path, where: 'waiting' })).word).toBe('Skipped');
		// Older lines, from before the two were told apart.
		expect(outcome(entry({ event: 'skipped', path })).word).toBe('Skipped');
		expect(headline(cancelled, 'Arrival')).toBe('Cancelled: Arrival');
	});

	test("says where the file was, in the service's words", () => {
		expect(detail(cancelled)).toBe('stopped 43% into the rewrite, nothing written');
		expect(detail(entry({ event: 'skipped', path }))).toBe('skipped for this run');
	});

	test('opens to the plan the rewrite had, and who stopped it', () => {
		const rows = Object.fromEntries(details(cancelled).map((row) => [row.label, row.values]));
		expect(rows.Cancelled).toEqual(['stopped 43% into the rewrite, nothing written']);
		expect(rows.Reasons).toEqual(['add 2.0 downmix']);
		expect(rows.Added).toEqual(['2.0']);
		expect(rows.By).toEqual(['admin']);
		expect(rows.Problem).toBeUndefined();
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
		expect(headline(line, 'Severance (2022)')).toBe('Edited tags: Severance (2022)');
		expect(headline(line)).toBe('Edited tags: Severance');
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

describe('a line as a search runs over it', () => {
	const line = entry({
		event: 'modified',
		path: '/data/media/tv/The Expanse/Season 01/The.Expanse.S01E03.1080p.mkv',
		reasons: ['stripped a forced subtitle'],
		rules: ['drop_commentary'],
		run: 'run-8f21',
		title: 'tv:the-expanse'
	});

	test('holds the headline, the detail and everything the panel opens on', () => {
		const text = searchable(line, 'The Expanse');
		// The verdict's own word, what it was about, and the rule that fired.
		expect(text).toContain('modified the expanse');
		expect(text).toContain('stripped a forced subtitle');
		expect(text).toContain('s01e03 1080p mkv');
		expect(text).toContain('drop commentary');
		expect(text).toContain('run 8f21');
	});

	test("holds the event's own name, which no headline says", () => {
		expect(searchable(entry({ event: 'sweep', files: 412 }))).toContain('sweep');
	});

	test('is rebuilt when the card for its title lands after the first look', () => {
		const late = entry({ event: 'item_paused', path: '/data/media/tv/MINDHUNTER (2017)' });
		expect(searchable(late)).toContain('mindhunter 2017');
		expect(searchable(late, 'Mindhunter')).toContain('paused mindhunter');
	});
});

test('older item pause events keep their title wording', () => {
	expect(headline(entry({ event: 'held' }), 'Dune')).toBe('Paused Dune');
	expect(headline(entry({ event: 'lifted' }), 'Dune')).toBe('Resumed Dune');
});

describe('run summaries match the available actions', () => {
	test.each([
		[true, 'Plan'],
		[false, 'Process'],
		[undefined, 'Sweep']
	])('names the mode %s and distinguishes stopped runs', (dry_run, action) => {
		const run = entry({ event: 'sweep', dry_run, files: 1 });
		expect(headline(run)).toBe(`${action} complete · 1 file`);
		expect(headline({ ...run, stopped: 2 })).toBe(`${action} stopped after 1 file`);
	});
});

test.each(['paused', 'resumed', 'held', 'lifted', 'item_paused', 'item_resumed', 'skipped'])(
	'%s keeps the actor in expanded details only',
	(event) => {
		const line = entry({ event, by: 'operator' });
		expect(detail(line)).not.toContain('operator');
		expect(details(line).find((row) => row.label === 'By')?.values).toEqual(['operator']);
	}
);

test('a file re-check receipt counts files rather than zero titles', () => {
	const run = entry({ event: 'recheck', files: 1, counts: { conform: 1 } });
	expect(headline(run)).toBe('Re-checked 1 file');
	expect(details(run).some((row) => row.label === 'Titles')).toBe(false);
	expect(headline({ ...run, titles: 1 })).toBe('Re-checked 1 title');
});

describe('a row leads with the title', () => {
	const path = '/movies/Sintel (2010)/Sintel (2010) Bluray-1080p.mkv';

	test('a rewrite keeps its year by the title and its release words under it', () => {
		const line = parts(entry({ event: 'modified', path, seconds: 60 }), 'Sintel');
		expect(line).toMatchObject({ title: 'Sintel', aside: '2010', file: true });
		expect(line.meta).toEqual(['Bluray-1080p', '1m']);
		expect(line.status?.word).toBe('Modified');
	});

	test('a hold says how long on its status line', () => {
		const line = parts(entry({ event: 'item_paused', path, seconds: 3600 }));
		expect(line.title).toBe('Sintel');
		expect(line.status?.word).toBe('Paused');
		expect(line.reason).toBe('for 1h');
	});

	test('a delivery of one file is about that file', () => {
		const one = entry({ event: 'webhook', arr_label: 'Radarr', files: 1, paths: [path] });
		expect(parts(one).title).toBe('Sintel');
		expect(parts(one).status?.word).toBe('Queued by Radarr');
		const two = { ...one, files: 2, paths: [path, path.replace('Sintel', 'Spring')] };
		expect(parts(two)).toMatchObject({ title: 'Radarr queued 2 files', file: false });
	});

	test('a run moves its file count under the headline and counts past two verdicts', () => {
		const run = entry({
			event: 'sweep',
			dry_run: true,
			files: 30,
			seconds: 120,
			counts: { pending: 8, conform: 20, skip: 1, unsupported: 1 }
		});
		const line = parts(run);
		expect(line.title).toBe('Plan complete');
		expect(line.meta).toEqual(['30 files', '2m', 'dry run']);
		expect(line.counts.map((part) => part.text)).toEqual(['8 pending', '20 passed', '2 other']);
	});
});
