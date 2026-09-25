import { describe, expect, test } from 'vitest';
import { ruleLabel, videoDetail } from '$lib/format';
import { judge, type Settings } from '$lib/demo/judge';
import type { Track } from '$lib/library';

const tracks: Track[] = [
	{ index: 0, kind: 'video', codec: 'hevc', dv: { profile: 8, compatibility: 1 } },
	{ index: 1, kind: 'audio', codec: 'aac', channels: 2, lang: 'eng' }
];
const settings: Settings = { ALLOWED_EXTS: ['.mkv'], LANGUAGES: ['eng'], AUDIO_LAYOUTS: [] };

describe('Dolby Vision details', () => {
	test('labels supported, unsupported, planned and ordinary tracks', () => {
		expect(videoDetail(tracks[0])).toBe(
			'Dolby Vision profile 8 · compatibility 1 · HDR10 compatible'
		);
		expect(videoDetail({ dv: { profile: 5, unsupported: 'No HDR10 fallback' } })).toBe(
			'Dolby Vision profile 5. No HDR10 fallback'
		);
		expect(videoDetail({ dv: { unsupported: 'Malformed metadata' } })).toBe(
			'Dolby Vision. Malformed metadata'
		);
		expect(videoDetail({ dv_removed: true })).toBe('Dolby Vision removed · HDR10 preserved');
		expect(videoDetail({})).toBe('');
		expect(ruleLabel('dv_strip')).toBe('Remove Dolby Vision');
		expect(ruleLabel('order')).toBe('order');
	});
	test.each(['never', 'alongside', 'always'])('demo respects %s mode', (mode) => {
		const result = judge({ tracks, ext: '.mkv' }, 'eng', { ...settings, RULE_DV_STRIP: mode });
		expect(result.status).toBe(mode === 'always' ? 'pending' : 'conform');
		if (mode === 'always') {
			expect(result.planned[0].dv).toBeUndefined();
			expect(result.planned[0].dv_removed).toBe(true);
			expect(result.why.rules).toContain('dv_strip');
			expect(
				judge({ tracks: result.planned, ext: '.mkv' }, 'eng', { ...settings, RULE_DV_STRIP: mode })
					.status
			).toBe('conform');
		}
	});
	test('alongside participates in another rewrite', () => {
		const result = judge(
			{ tracks: [tracks[0], { ...tracks[1], channels: 6 }], ext: '.mkv' },
			'eng',
			{ ...settings, AUDIO_LAYOUTS: ['2.0'], RULE_DV_STRIP: 'alongside' }
		);
		expect(result.status).toBe('pending');
		expect(result.planned[0].dv_removed).toBe(true);
		expect(result.why.incidental_rules).toContain('dv_strip');
	});
	test('unsupported profiles are information without pending work', () => {
		const result = judge(
			{
				tracks: [{ ...tracks[0], dv: { profile: 5, unsupported: 'No HDR10 fallback' } }, tracks[1]],
				ext: '.mkv'
			},
			'eng',
			{ ...settings, RULE_DV_STRIP: 'always' }
		);
		expect(result.status).toBe('conform');
	});
});
