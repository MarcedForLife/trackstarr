import { describe, expect, test } from 'vitest';
import type { Requirement, Sourcing } from '$lib/library';
import { missingAudio, sourcingLines, timing } from '$lib/sourcing';

const NAMES = { eng: 'English', fre: 'French', ger: 'German', spa: 'Spanish' };

const TRACK = {
	path: '/data/media/4k/movies/Dune (2024)/Dune (2024) Remux-2160p.mkv',
	instance_id: 'radarr-4k',
	index: 2,
	source: '4K'
};

function sourcing(...requirements: Requirement[]): Sourcing {
	const open = requirements.some((requirement) => requirement.state !== 'satisfied');
	return { completeness: open ? 'incomplete' : 'complete', requirements };
}

function lines(...requirements: Requirement[]): string[] {
	return sourcingLines({ sourcing: sourcing(...requirements) }, NAMES).map((line) => line.text);
}

describe('timing', () => {
	test('says nothing of a source on the same timeline', () => {
		expect(timing(undefined)).toBe('');
		expect(timing({ scale: '1', offset_us: 0 })).toBe('');
		expect(timing({ scale: '1/1', offset_us: 400 })).toBe('');
	});

	test('names an offset and a speed apart from each other', () => {
		expect(timing({ scale: '25/24', offset_us: 1_250_000 })).toBe('+1.250 s, 25/24 speed');
		expect(timing({ scale: '1', offset_us: -42_000 })).toBe('−0.042 s');
		expect(timing({ scale: '24/25', offset_us: 0 })).toBe('24/25 speed');
	});
});

describe('sourcingLines', () => {
	test('says nothing of a language the file already has', () => {
		expect(lines({ lang: 'eng', state: 'satisfied' })).toEqual([]);
		expect(sourcingLines({}, NAMES)).toEqual([]);
	});

	test('names the language and the source in every state', () => {
		expect(
			lines(
				{ lang: 'eng', state: 'satisfied' },
				{ lang: 'fre', state: 'missing_source' },
				{
					lang: 'ger',
					state: 'analysis_required',
					reason: 'timing not yet measured',
					track: TRACK
				},
				{
					lang: 'spa',
					state: 'ready',
					track: TRACK,
					mapping: { scale: '25/24', offset_us: 1_250_000 },
					measured: true
				}
			)
		).toEqual([
			'French audio missing, no variant has it',
			'German audio from 4K, timing to be measured',
			'Spanish audio from 4K, ready (+1.250 s, 25/24 speed)'
		]);
		expect(
			lines({ lang: 'fre', state: 'ready', track: TRACK, mapping: { scale: '1', offset_us: 0 } })
		).toEqual(['French audio from 4K, ready']);
	});

	test('carries the service reason where a person or a retry is owed', () => {
		expect(
			lines(
				{
					lang: 'fre',
					state: 'needs_review',
					reason: 'timing could not be confirmed: contradicting_region'
				},
				{ lang: 'ger', state: 'needs_review' },
				{ lang: 'spa', state: 'failed', reason: 'measuring timing failed: probe failed' },
				{ lang: 'eng', state: 'unavailable', reason: 'a connection could not be listed' }
			)
		).toEqual([
			'French audio needs review, timing could not be confirmed: contradicting_region',
			'German audio needs review',
			'Spanish audio not imported, measuring timing failed: probe failed',
			'English audio source unknown, a connection could not be listed'
		]);
	});

	test('falls back to the code while the names are read', () => {
		const file = { sourcing: sourcing({ lang: 'fre', state: 'missing_source' }) };
		expect(sourcingLines(file, null)[0].text).toBe('fre audio missing, no variant has it');
	});
});

describe('missingAudio', () => {
	const short = sourcing(
		{ lang: 'eng', state: 'satisfied' },
		{ lang: 'fre', state: 'missing_source' },
		{ lang: 'ger', state: 'needs_review' },
		{ lang: 'spa', state: 'failed' }
	);

	test('lists what a passing file lacks', () => {
		expect(missingAudio({ status: 'conform', sourcing: short }, NAMES)).toBe(
			'French, German and Spanish audio missing'
		);
		const one = sourcing({ lang: 'fre', state: 'missing_source' });
		expect(missingAudio({ status: 'conform', sourcing: one }, NAMES)).toBe('French audio missing');
		const unknown: Sourcing = {
			completeness: 'unknown',
			requirements: [
				{ lang: 'fre', state: 'unavailable' },
				{ lang: 'ger', state: 'missing_source' }
			]
		};
		expect(missingAudio({ status: 'conform', sourcing: unknown }, NAMES)).toBe(
			'French and German audio missing'
		);
	});

	test('says nothing of a complete file or one whose verdict already says more', () => {
		const complete = sourcing({ lang: 'eng', state: 'satisfied' });
		expect(missingAudio({ status: 'conform', sourcing: complete }, NAMES)).toBe('');
		expect(missingAudio({ status: 'conform' }, NAMES)).toBe('');
		expect(missingAudio({ status: 'pending', sourcing: short }, NAMES)).toBe('');
	});
});
