import { expect, test } from 'vitest';
import { copyOptions, fileGroups } from './copies';
import type { LibraryFile } from './library';
import { instanceId, sources } from './connections';
import grouping from '../fixtures/copy-groups.json';

const file = (name: string, folder = '/tv') => ({ name, path: `${folder}/${name}` }) as LibraryFile;

test('copy grouping agrees with the backend pagination contract', () => {
	for (const { kind, path, key } of grouping) {
		const entry = { path, name: path.split('/').at(-1)! } as LibraryFile;
		expect(fileGroups([entry], kind)[0].key, path).toBe(key);
	}
	for (const kind of ['movie', 'series', 'folder']) {
		const cases = grouping.filter((entry) => entry.kind === kind);
		const files = cases.map(({ path }) => ({ path, name: path.split('/').at(-1)! }) as LibraryFile);
		expect(fileGroups(files, kind).map((group) => group.files.map((file) => file.path))).toEqual(
			[...new Set(cases.map((entry) => entry.key))].map((key) =>
				cases.filter((entry) => entry.key === key).map((entry) => entry.path)
			)
		);
	}
});

test('duplicates share an episode row while single copies retain one row', () => {
	const first = file('Show S01E01 [1080p].mkv');
	const copy = file('Show S01E01 [2160p].mkv');
	const next = file('Show S01E02.mkv');
	const groups = fileGroups([first, copy, next], 'series');
	expect(groups.map((group) => group.files.length)).toEqual([2, 1]);
	expect(groups[0].files).toEqual([first, copy]);
	expect(
		fileGroups([file('剧S01E01.1080p.mkv'), file('剧S01E01.2160p.mkv')], 'series')
	).toHaveLength(1);
});

test('multi-episode releases and unnumbered files never merge with an episode', () => {
	const files = [
		'Show S01E01.mkv',
		'Show S01E01-E02.mkv',
		'Show S01E01E02.mkv',
		'Extra.mkv',
		'Trailer.mkv'
	].map((name) => file(name));
	expect(fileGroups(files, 'series')).toHaveLength(files.length);
	expect(fileGroups(files, 'folder')).toHaveLength(files.length);
});

test('film files share a selector without losing same-named paths', () => {
	const files = [file('film.mkv', '/movies'), file('film.mkv', '/movies/alternate')];
	expect(fileGroups(files, 'movie')[0].files).toEqual(files);
});

test('named sources are distinct from the default public address', () => {
	const found = sources({
		RADARR_PUBLIC_URL: '',
		RADARR_4K_URL: '',
		RADARR_4K_API_KEY: '',
		RADARR_4K_LABEL: ' UHD shelf ',
		SONARR_REMOTE_PUBLIC_URL: '',
		SONARR_LABEL: 'Main'
	});
	expect(found.map((service) => service.name)).toEqual([
		'radarr',
		'sonarr',
		'radarr-4k',
		'sonarr-remote'
	]);
	expect(found.map((service) => service.label)).toEqual([
		'Radarr',
		'Main',
		'UHD shelf',
		'Sonarr remote'
	]);
	expect(found[2]).toMatchObject({
		kind: 'radarr',
		prefix: 'RADARR_4K',
		key: 'RADARR_4K_API_KEY',
		labelName: 'RADARR_4K_LABEL'
	});
});

test('an instance id is the name’s letters and digits, or a free number', () => {
	expect(instanceId('UHD shelf', new Set())).toBe('UHDSHELF');
	expect(instanceId('4k', new Set())).toBe('4K');
	expect(instanceId('4k', new Set(['4K']))).toBe('4K2');
	expect(instanceId('', new Set())).toBe('2');
	expect(instanceId('', new Set(['2', '3']))).toBe('4');
	expect(instanceId('public', new Set())).toBe('PUBLIC2');
});

test('same-quality release labels stay compact and identical basenames retain paths', () => {
	const original = {
		...file('Film (2020) Bluray-1080p.mkv'),
		bytes: 1024,
		status: 'pending' as const
	};
	const alternate = {
		...original,
		name: 'Film (2020) Bluray-1080p [1080p alternate].mkv',
		path: '/movies/alternate.mkv'
	};
	const choices = copyOptions([original, alternate]);
	expect(choices[0].label).toBe('1080p · 1.0 KB · Pending');
	expect(choices[1].label).toBe('1080p · alternate · 1.0 KB · Pending');
	const twin = { ...original, path: '/other/film.mkv' };
	expect(copyOptions([original, twin]).every((option) => option.label.includes(option.value))).toBe(
		true
	);
});

test('files from two sources lead with the source, one source stays quiet', () => {
	const held = { ...file('Film Bluray-1080p.mkv'), bytes: 1024, status: 'pending' as const };
	const remote = {
		...held,
		name: 'Film Remux-2160p.mkv',
		path: '/movies4k/Film Remux-2160p.mkv',
		source: 'Radarr 4k'
	};
	expect(copyOptions([held, remote]).map(({ label }) => label)).toEqual([
		'1080p · 1.0 KB · Pending',
		'Radarr 4k · 2160p · 1.0 KB · Pending'
	]);
	const twin = { ...held, source: 'Radarr' };
	expect(
		copyOptions([
			{ ...held, source: 'Radarr' },
			{ ...twin, path: '/other/film.mkv' }
		])[0].label
	).toBe('1080p · 1.0 KB · Pending · /tv/Film Bluray-1080p.mkv');
});
