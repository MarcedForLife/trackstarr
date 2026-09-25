import { describe, expect, test } from 'vitest';
import {
	bytesFor,
	dated,
	describe as line,
	named,
	placeWord,
	titled,
	wholeUnits
} from '$lib/format';

describe('named', () => {
	test('an episode is said apart from the series it truncates with', () => {
		expect(
			named(
				'/tv/House of the Dragon/Season 2/House of the Dragon (2022) - S02E05 - Regent [WEBRip-2160p][HDR10][AAC 2.0][h265]-HODL..mkv'
			)
		).toEqual({ name: 'House of the Dragon', episode: 'S02E05', detail: '' });
	});

	test('two rewrites of one series differ where the line cannot cut', () => {
		const of = (episode: string) =>
			named(`The Gentlemen (2024) - ${episode} - TBA [WEBDL-1080p][EAC3 5.1][x264]-EniaHD.mkv`);
		expect(of('S02E04').name).toBe(of('S02E08').name);
		expect(of('S02E04').episode).not.toBe(of('S02E08').episode);
	});

	test('a double counts as one episode', () => {
		expect(named('Lost (2004) - S01E01-E02 - Pilot [Bluray-1080p].mkv').episode).toBe('S01E01-E02');
	});

	// A film has nothing but its year to be told apart by, so it keeps it,
	// beside the title rather than in it.
	test('a film hands its year and release words back apart', () => {
		expect(named('/films/Dune (2021)/Dune (2021) [Bluray-2160p][DTS 5.1][x265]-GRP.mkv')).toEqual({
			name: 'Dune',
			episode: '',
			detail: '(2021)'
		});
		// A release that names its quality outside the tags keeps that too.
		expect(named('/films/Coffee Run (2020) Bluray-1080p.mkv').detail).toBe('(2020) Bluray-1080p');
	});

	// A title that opens on a bracket is why the tags are found by ' [' rather
	// than by '['.
	test('a bracket in the title is not a tag', () => {
		expect(named('[REC] (2007) [Bluray-1080p][DTS 5.1][x264]-GRP.mkv').name).toBe('[REC]');
	});

	test('a title of its own shape survives', () => {
		expect(named('9-1-1 (2018) - S01E01 - Pilot [WEBDL-1080p].mkv')).toEqual({
			name: '9-1-1',
			episode: 'S01E01',
			detail: ''
		});
		expect(named('Doctor Who (2005) - S01E01 [HDTV-720p].mkv')).toEqual({
			name: 'Doctor Who',
			episode: 'S01E01',
			detail: ''
		});
	});

	test('an untagged name loses only its extension', () => {
		expect(named('/tv/some.release.name-GRP.mkv')).toEqual({
			name: 'some.release.name-GRP',
			episode: '',
			detail: ''
		});
	});

	// A hold is placed on a title, so what arrives is the folder the *arr made.
	test("a title's own folder drops the provider's id and keeps the year", () => {
		expect(named('/data/media/tv/MINDHUNTER (2017) {tvdb-328708}')).toEqual({
			name: 'MINDHUNTER',
			episode: '',
			detail: '(2017)'
		});
	});

	test('a dot in a folder is not an extension', () => {
		expect(named('/data/media/tv/Mr. Robot (2015) {tvdb-289590}').name).toBe('Mr. Robot');
	});

	test('a name with nothing to drop is handed back whole', () => {
		expect(named('/data/readme').name).toBe('readme');
		expect(named(undefined).name).toBe('');
	});
});

describe('titled', () => {
	test('says both halves where there is room for both', () => {
		expect(
			titled('House of the Dragon (2022) - S02E05 - Regent [WEBRip-2160p][h265]-HODL..mkv')
		).toBe('House of the Dragon S02E05');
	});

	test('two files of one series do not read as one name twice', () => {
		const of = (episode: string) =>
			titled(`The Gentlemen (2024) - ${episode} - TBA [WEBDL-1080p][x264]-EniaHD.mkv`);
		expect(of('S02E04')).not.toBe(of('S02E08'));
	});

	test('a film has one half and gets one', () => {
		expect(titled('Dune (2021) [Bluray-2160p][x265]-GRP.mkv')).toBe('Dune (2021)');
	});
});

describe('a track on one line', () => {
	test('says the language on the kinds that carry one, und included', () => {
		expect(line({ kind: 'audio', codec: 'eac3', channels: 6, lang: 'eng' })).toBe(
			'EAC3 · 5.1 · eng'
		);
		expect(line({ kind: 'audio', codec: 'flac', channels: 2 })).toBe('FLAC · 2.0 · und');
		expect(line({ kind: 'subtitle', codec: 'subrip' })).toBe('SUBRIP · und');
	});

	// Nothing tags a video stream, so `und` there is a fix nobody can make.
	test('leaves a video stream without one', () => {
		expect(line({ kind: 'video', codec: 'h264' })).toBe('H264');
	});
});

describe('wholeUnits', () => {
	test('a count that is a whole number of one unit is said in it', () => {
		expect(wholeUnits(60)).toBe('1 minute');
		expect(wholeUnits(900)).toBe('15 minutes');
		expect(wholeUnits(7200)).toBe('2 hours');
		expect(wholeUnits(86400)).toBe('1 day');
	});

	test('the largest unit that divides it wins', () => {
		expect(wholeUnits(90000)).toBe('25 hours');
		expect(wholeUnits(172800)).toBe('2 days');
	});

	test('anything that would need rounding is said not at all', () => {
		expect(wholeUnits(45)).toBe('');
		expect(wholeUnits(90)).toBe('');
		expect(wholeUnits(7201)).toBe('');
	});

	test('nothing, a fraction or a negative is not a duration', () => {
		expect(wholeUnits(0)).toBe('');
		expect(wholeUnits(-60)).toBe('');
		expect(wholeUnits(60.5)).toBe('');
		expect(wholeUnits(NaN)).toBe('');
	});
});

describe('bytesFor', () => {
	test('a rate over a running time is what the track takes', () => {
		// 640k over an hour: 640000 / 8 * 3600.
		expect(bytesFor(640_000, 3600)).toBe(288_000_000);
	});

	test('either half missing says nothing rather than nothing much', () => {
		expect(bytesFor(undefined, 3600)).toBe(0);
		expect(bytesFor(640_000, 0)).toBe(0);
	});
});

describe('dated', () => {
	test('takes the year off the release words', () => {
		expect(dated('(2010) Bluray-1080p')).toEqual({ year: '2010', words: 'Bluray-1080p' });
		expect(dated('Bluray-1080p')).toEqual({ year: '', words: 'Bluray-1080p' });
		expect(dated('')).toEqual({ year: '', words: '' });
	});
});

test('a queue place reads as next, then by its order', () => {
	expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 103].map(placeWord)).toEqual([
		'Next',
		'2nd',
		'3rd',
		'4th',
		'11th',
		'12th',
		'13th',
		'21st',
		'22nd',
		'103rd'
	]);
});
