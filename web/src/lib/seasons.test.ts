import { describe, expect, test } from 'vitest';
import type { LibraryFile, Verdict } from '$lib/library';
import { seasons } from '$lib/seasons';

function episode(path: string, status: Verdict = 'conform'): LibraryFile {
	return {
		path,
		name: path.slice(path.lastIndexOf('/') + 1),
		status,
		bytes: 1,
		seconds: 3600,
		tracks: [],
		planned: [],
		why: {}
	};
}

const SHOW = '/data/media/tv/Severance';

describe('seasons', () => {
	test('groups by season, latest first, episodes in order', () => {
		const grouped = seasons([
			episode(`${SHOW}/Season 01/Severance - S01E02 - Half Loop.mkv`),
			episode(`${SHOW}/Season 02/Severance - S02E01 - Hello, Ms. Cobel.mkv`),
			episode(`${SHOW}/Season 01/Severance - S01E01 - Good News.mkv`)
		]);

		expect(grouped?.map((season) => season.label)).toEqual(['Season 2', 'Season 1']);
		expect(grouped?.[1].files.map((file) => file.name)).toEqual([
			'Severance - S01E01 - Good News.mkv',
			'Severance - S01E02 - Half Loop.mkv'
		]);
	});

	test('specials and unnumbered files come last', () => {
		const grouped = seasons([
			episode(`${SHOW}/Specials/Severance - S00E01 - The Lexington Letter.mkv`),
			episode(`${SHOW}/Season 01/Severance - S01E01 - Good News.mkv`),
			episode(`${SHOW}/Extras/trailer.mkv`)
		]);

		expect(grouped?.map((season) => season.label)).toEqual(['Season 1', 'Specials', 'Other']);
	});

	test('a file named some other way takes the season off its folder', () => {
		const grouped = seasons([
			episode(`${SHOW}/Season 03/severance.s03e04.1080p.mkv`),
			episode(`${SHOW}/Season 01/Severance - S01E01 - Good News.mkv`)
		]);

		expect(grouped?.map((season) => season.label)).toEqual(['Season 3', 'Season 1']);
	});

	test('carries the worst verdict in each season', () => {
		const grouped = seasons([
			episode(`${SHOW}/Season 02/Severance - S02E01 - Hello.mkv`, 'pending'),
			episode(`${SHOW}/Season 02/Severance - S02E02 - Goodbye.mkv`, 'failed'),
			episode(`${SHOW}/Season 01/Severance - S01E01 - Good News.mkv`)
		]);

		expect(grouped?.map((season) => season.state)).toEqual(['failed', 'conform']);
	});

	test('says nothing about a film or a single season', () => {
		expect(seasons([episode('/data/media/movies/Dune (2024)/Dune (2024).mkv')])).toBeNull();
		expect(
			seasons([
				episode(`${SHOW}/Season 01/Severance - S01E01 - Good News.mkv`),
				episode(`${SHOW}/Season 01/Severance - S01E02 - Half Loop.mkv`)
			])
		).toBeNull();
	});
});
