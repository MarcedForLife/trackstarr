import { describe, expect, test } from 'vitest';
import {
	ABOVE,
	BELOW,
	channelsOf,
	codecNotes,
	formatLang,
	formatRow,
	langCode,
	langLabel,
	parseLang,
	parseRow,
	placeRow,
	rateBps,
	rateNotches,
	sameRate,
	shareNote,
	shareProblem
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

const STOCK: Record<string, string[]> = { '2.0': ['aac', '320k'], '5.1': ['ac3', '640k'] };

describe('parseRow', () => {
	test('reads a bare name as a downmix at its stock spec', () => {
		expect(parseRow('5.1', STOCK)).toEqual({
			name: '5.1',
			action: 'downmix',
			codec: 'ac3',
			bitrate: '640k'
		});
	});

	test('reads two fields as an action, which encodes nothing', () => {
		expect(parseRow('7.1:remove', STOCK)).toEqual({
			name: '7.1',
			action: 'remove',
			codec: '',
			bitrate: ''
		});
	});

	test('reads three fields as a downmix at that encoder and rate', () => {
		expect(parseRow('5.1:eac3:448k', STOCK)).toEqual({
			name: '5.1',
			action: 'downmix',
			codec: 'eac3',
			bitrate: '448k'
		});
	});

	test('leaves a size the stock table has no opinion about unspecified', () => {
		expect(parseRow('4.0', STOCK)).toEqual({
			name: '4.0',
			action: 'downmix',
			codec: '',
			bitrate: ''
		});
	});
});

describe('formatRow', () => {
	test('writes a downmix out in full, so the stock table cannot move it later', () => {
		expect(formatRow(parseRow('5.1', STOCK))).toBe('5.1:ac3:640k');
	});

	test('writes an action without an encoder it would never read', () => {
		expect(formatRow(parseRow('7.1:remove', STOCK))).toBe('7.1:remove');
	});

	test('round-trips a spelled-out downmix', () => {
		expect(formatRow(parseRow('2.0:libopus:192k', STOCK))).toBe('2.0:libopus:192k');
	});
});

describe('placeRow', () => {
	const entries = ['2.0:aac:320k', '5.1:ac3:640k', '7.1:remove'];

	test('rewrites in place while the row stays on its side of the boundary', () => {
		expect(placeRow(entries, 0, '2.0:aac:320k', 'downmix', 'keep', 2)).toEqual([
			'2.0:aac:320k',
			'5.1:ac3:640k',
			'7.1:remove'
		]);
	});

	test('sends a row joining the removals to the bottom', () => {
		expect(placeRow(entries, 0, '2.0:remove', 'downmix', 'remove', 2)).toEqual([
			'5.1:ac3:640k',
			'7.1:remove',
			'2.0:remove'
		]);
	});

	test('lands a row leaving the removals at the end of the block above', () => {
		expect(placeRow(entries, 2, '7.1:aac:768k', 'remove', 'downmix', 2)).toEqual([
			'2.0:aac:320k',
			'5.1:ac3:640k',
			'7.1:aac:768k'
		]);
	});
});

describe('language rows', () => {
	test('reads a bare name as a downmix, and an action beside it', () => {
		expect(parseLang('eng')).toEqual({ name: 'eng', action: 'downmix' });
		expect(parseLang('original')).toEqual({ name: 'original', action: 'downmix' });
		expect(parseLang('fre:keep')).toEqual({ name: 'fre', action: 'keep' });
	});

	test('writes a downmix bare, since there is no spec to pin', () => {
		expect(formatLang({ name: 'eng', action: 'downmix' })).toBe('eng');
		expect(formatLang({ name: 'fre', action: 'keep' })).toBe('fre:keep');
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

describe('rateNotches', () => {
	const stereo = ['128k', '192k', '256k', '320k', '384k'];

	test('offers the size its own rates when the row holds one of them', () => {
		expect(rateNotches(stereo, '320k')).toEqual(stereo);
		// Spelled differently, still the same notch.
		expect(rateNotches(stereo, '320000')).toEqual(stereo);
	});

	test('folds an unlisted rate in at its place, so it stays selectable', () => {
		expect(rateNotches(stereo, '224k')).toEqual(['128k', '192k', '224k', '256k', '320k', '384k']);
	});

	test('leaves a rate it cannot read at the front rather than dropping it', () => {
		expect(rateNotches(stereo, '0.2M')).toEqual(['0.2M', ...stereo]);
	});

	test('has nothing to fold in for an empty rate', () => {
		expect(rateNotches(stereo, '')).toEqual(stereo);
	});
});

describe('sameRate', () => {
	test('compares what two rates mean, not how they are written', () => {
		expect(sameRate('640k', '640000')).toBe(true);
		expect(sameRate('1m', '1000k')).toBe(true);
		expect(sameRate('640k', '448k')).toBe(false);
	});

	// Both read as 0 bps, which would otherwise make every junk rate equal.
	test('two rates it cannot read are the same only written the same', () => {
		expect(sameRate('0.2M', '0.2M')).toBe(true);
		expect(sameRate('0.2M', 'nonsense')).toBe(false);
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

describe('shareProblem', () => {
	test('takes a whole number inside the band', () => {
		expect(shareProblem('80', BELOW)).toBe('');
		expect(shareProblem(' 10 ', BELOW)).toBe('');
		expect(shareProblem('90', BELOW)).toBe('');
	});

	test('names what is wrong while the field is still open', () => {
		expect(shareProblem('', BELOW)).toBe('That has to be a whole number.');
		expect(shareProblem('79.5', BELOW)).toBe('That has to be a whole number.');
		expect(shareProblem('9', BELOW)).toBe('That has to be between 10 and 90.');
		expect(shareProblem('-20', BELOW)).toBe('That has to be between 10 and 90.');
		expect(shareProblem('100', BELOW)).toBe('That has to be between 10 and 90.');
	});

	test('takes 0 for the share that switches off, and says so in its band', () => {
		expect(shareProblem('0', ABOVE)).toBe('');
		expect(shareProblem('110', ABOVE)).toBe('');
		expect(shareProblem(' 400 ', ABOVE)).toBe('');
		// Under the rate itself a track would be re-encoded to the rate it has.
		expect(shareProblem('100', ABOVE)).toBe('That has to be 0, or between 110 and 400.');
		expect(shareProblem('401', ABOVE)).toBe('That has to be 0, or between 110 and 400.');
		// The other share has no off, so 0 is out of its band like any other.
		expect(shareProblem('0', BELOW)).toBe('That has to be between 10 and 90.');
	});
});

describe('shareNote', () => {
	// The share is what the setting holds; a rate is what a track reports about
	// itself, which is the only one of the two a reader can check.
	test('turns the share into the rate each layout is read against', () => {
		const note = shareNote('80', BELOW, [
			{ layout: '2.0', rate: '320k' },
			{ layout: '5.1', rate: '640k' }
		]);
		expect(note).toBe('Low bitrate means a 2.0 under 256k, a 5.1 under 512k.');
	});

	test('puts the rate on the side its share names', () => {
		const note = shareNote('150', ABOVE, [
			{ layout: '2.0', rate: '192k' },
			{ layout: '5.1', rate: '448k' }
		]);
		expect(note).toBe('High bitrate means a 2.0 over 288k, a 5.1 over 672k.');
	});

	test('leaves out a layout whose rate cannot be read yet', () => {
		const note = shareNote('50', BELOW, [
			{ layout: '2.0', rate: '320k' },
			{ layout: '5.1', rate: '' }
		]);
		expect(note).toBe('Low bitrate means a 2.0 under 160k.');
	});

	test('says what 0 means, since a rate would say nothing', () => {
		expect(shareNote('0', ABOVE, [{ layout: '2.0', rate: '192k' }])).toBe(
			'Off: no track is re-encoded to shrink it.'
		);
	});

	test('says nothing while the share itself is wrong, or with nothing to say it about', () => {
		expect(shareNote('120', BELOW, [{ layout: '2.0', rate: '320k' }])).toBe('');
		expect(shareNote('80', BELOW, [])).toBe('');
		expect(shareNote('90', ABOVE, [{ layout: '2.0', rate: '192k' }])).toBe('');
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
