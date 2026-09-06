import { describe, expect, test } from 'vitest';
import {
	belowNote,
	belowProblem,
	bitrateName,
	channelsOf,
	codecName,
	codecNotes,
	langCode,
	langLabel,
	rateBps
} from '$lib/rules';
import type { Codec } from '$lib/settings';

const AAC: Codec = {
	name: 'aac',
	max_channels: 8,
	containers: ['.mkv', '.mp4', '.m4v'],
	lossless: false
};
const AC3: Codec = { name: 'ac3', max_channels: 6, containers: ['.mkv', '.mp4'], lossless: false };
const FLAC: Codec = { name: 'flac', max_channels: 8, containers: ['.mkv'], lossless: true };

const LANGUAGES: Record<string, string> = { eng: 'English', fre: 'French', mao: 'Māori' };

describe('setting names', () => {
	test('spells a layout the way the environment does', () => {
		expect(bitrateName('5.1')).toBe('AUDIO_BITRATE_5_1');
		expect(codecName('2.0')).toBe('AUDIO_CODEC_2_0');
	});
});

describe('channelsOf', () => {
	test('reads a layout as the service does', () => {
		expect(channelsOf('5.1')).toBe(6);
		expect(channelsOf('2.0')).toBe(2);
		expect(channelsOf('7.1')).toBe(8);
	});

	// An invented layout is not refused here; it goes to the service, which
	// knows what its ffmpeg will take.
	test('has nothing to say about a name it cannot read', () => {
		expect(channelsOf('stereo')).toBe(0);
		expect(channelsOf('5.1.2')).toBe(0);
	});
});

describe('rateBps', () => {
	test('reads the suffixes the service accepts', () => {
		expect(rateBps('320k')).toBe(320000);
		expect(rateBps('1m')).toBe(1000000);
		expect(rateBps('640000')).toBe(640000);
	});

	test('is not fussed by case or a stray space', () => {
		expect(rateBps(' 320K ')).toBe(320000);
	});

	test('reads an unfinished or invalid rate as nothing', () => {
		expect(rateBps('')).toBe(0);
		expect(rateBps('320kbps')).toBe(0);
		expect(rateBps('320.5k')).toBe(0);
	});
});

describe('belowProblem', () => {
	test('takes a whole number inside the band', () => {
		expect(belowProblem('80')).toBe('');
		expect(belowProblem(' 10 ')).toBe('');
		expect(belowProblem('90')).toBe('');
	});

	test('names what is wrong while the field is still open', () => {
		expect(belowProblem('')).toBe('That has to be a whole number.');
		expect(belowProblem('79.5')).toBe('That has to be a whole number.');
		expect(belowProblem('9')).toBe('That has to be between 10 and 90.');
		expect(belowProblem('-20')).toBe('That has to be between 10 and 90.');
		expect(belowProblem('100')).toBe('That has to be between 10 and 90.');
	});
});

describe('belowNote', () => {
	// The share is what the setting holds; a rate is what a track reports about
	// itself, which is the only one of the two a reader can check.
	test('turns the share into the rate each layout is read against', () => {
		const note = belowNote('80', [
			{ layout: '2.0', rate: '320k' },
			{ layout: '5.1', rate: '640k' }
		]);
		expect(note).toBe('Low bitrate means a 2.0 under 256k, a 5.1 under 512k.');
	});

	test('leaves out a layout whose rate cannot be read yet', () => {
		const note = belowNote('50', [
			{ layout: '2.0', rate: '320k' },
			{ layout: '5.1', rate: '' }
		]);
		expect(note).toBe('Low bitrate means a 2.0 under 160k.');
	});

	test('says nothing while the share itself is wrong, or with nothing to say it about', () => {
		expect(belowNote('120', [{ layout: '2.0', rate: '320k' }])).toBe('');
		expect(belowNote('80', [])).toBe('');
	});
});

describe('codecNotes', () => {
	test('says nothing about an encoder it does not know', () => {
		expect(codecNotes('5.1', undefined, ['.mkv'])).toEqual([]);
	});

	test('says nothing when the choice works', () => {
		expect(codecNotes('5.1', AC3, ['.mkv'])).toEqual([]);
	});

	// The one nobody knows in advance.
	test('names the channel ceiling', () => {
		expect(codecNotes('7.1', AC3, ['.mkv'])).toEqual([
			'ac3 encodes at most 6 channels, so it cannot make a 7.1 track.'
		]);
	});

	test('names every container the encoder cannot be written into', () => {
		expect(codecNotes('5.1', AC3, ['.mkv', '.avi', '.m4v'])).toEqual([
			'ac3 cannot be written into .avi, .m4v, selected under Containers below.'
		]);
	});

	test('says a rate beside a lossless encoder does nothing', () => {
		expect(codecNotes('2.0', FLAC, ['.mkv'])).toEqual([
			'flac is lossless, so the rate beside 2.0 does nothing.'
		]);
	});

	test('says all of what is wrong at once', () => {
		expect(codecNotes('7.1', FLAC, ['.mkv', '.mp4'])).toEqual([
			'flac cannot be written into .mp4, selected under Containers below.',
			'flac is lossless, so the rate beside 7.1 does nothing.'
		]);
		expect(codecNotes('2.0', AAC, ['.mkv'])).toEqual([]);
	});
});

describe('languages', () => {
	test('names a code, and shows an unnamed one as it stands', () => {
		expect(langLabel(LANGUAGES, 'eng')).toBe('English');
		expect(langLabel(LANGUAGES, 'nzs')).toBe('nzs');
	});

	// The tag vocabulary is wider than the names the service offers, so a code
	// it has no name for is still typeable.
	test('takes a name or a code, either case', () => {
		expect(langCode(LANGUAGES, 'English')).toBe('eng');
		expect(langCode(LANGUAGES, ' māori ')).toBe('mao');
		expect(langCode(LANGUAGES, 'ENG')).toBe('eng');
		expect(langCode(LANGUAGES, 'nzs')).toBe('nzs');
		expect(langCode(LANGUAGES, '  ')).toBe('');
	});
});
