import { describe, expect, test } from 'vitest';
import { matches, terms, words } from '$lib/search';

const against = (text: string, query: string) => matches(words(text), terms(query));

describe('flattening text to words', () => {
	test('collapses the punctuation a release name is built from', () => {
		expect(words('The.Expanse.S01E03.1080p.BluRay.x264-GROUP')).toBe(
			'the expanse s01e03 1080p bluray x264 group'
		);
	});

	test('drops accents, so a name can be typed without them', () => {
		expect(words('Amélie')).toBe('amelie');
	});

	test('keeps letters of any script', () => {
		expect(words('千と千尋の神隠し (2001)')).toBe('千と千尋の神隠し 2001');
	});

	test('can go again over its own output', () => {
		expect(words(words('The.Expanse'))).toBe('the expanse');
	});
});

describe('the words typed', () => {
	test('are nothing at all when the box is empty or all punctuation', () => {
		expect(terms('  ')).toEqual([]);
		expect(terms('...')).toEqual([]);
	});

	test('split on the punctuation a path carries', () => {
		expect(terms('/data/media/tv')).toEqual(['data', 'media', 'tv']);
	});
});

describe('matching a line', () => {
	const line = 'rewrote the expanse s01e03 1080p bluray aac 5 1 forced subtitle stripped';

	test('finds a word out of the phrase it sits in', () => {
		expect(against(line, 'expanse')).toBe(true);
		expect(against(line, 'subtitle expanse')).toBe(true);
	});

	test('holds every word typed, not just one of them', () => {
		expect(against(line, 'expanse mindhunter')).toBe(false);
	});

	test('reaches across the dots a release name has instead of spaces', () => {
		expect(against('The.Expanse.S01E03.mkv', 'expanse s01e03')).toBe(true);
	});

	test('forgives a letter missed out of a long word', () => {
		expect(against(line, 'expnse')).toBe(true);
		expect(against(line, 'subttle')).toBe(true);
	});

	test('but holds a short word to its spelling, or it would find everything', () => {
		expect(against(line, 'aac')).toBe(true);
		expect(against(line, 'arc')).toBe(false);
	});

	test('will not spell a word out of letters taken across the line', () => {
		// r, a, i, d are all there in that order, one word apart each time.
		expect(against(line, 'raid')).toBe(false);
		expect(against('bluray aac id', 'raid')).toBe(false);
	});

	test('keeps the letters in the order they were typed', () => {
		expect(against(line, 'esnapxe')).toBe(false);
	});

	test('ignores the case and the accents on both sides', () => {
		expect(against('Rewrote Amélie', 'AMELIE')).toBe(true);
	});

	test('an empty search keeps every line', () => {
		expect(matches(words(line), terms(''))).toBe(true);
	});
});
