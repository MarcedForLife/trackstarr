import { describe, expect, test } from 'vitest';
import { changes, cloneValues } from '$lib/draft';
import type { Setting, SettingValue } from '$lib/settings';

function snapshot(
	values: Record<string, SettingValue>,
	env: string[] = []
): Record<string, Setting> {
	return Object.fromEntries(
		Object.entries(values).map(([name, value]) => [name, { value, env: env.includes(name) }])
	);
}

describe('cloneValues', () => {
	test('copies the arrays, so editing a draft cannot reach the baseline', () => {
		const baseline = snapshot({ LAYOUTS: ['eac3', 'aac'] });
		const draft = cloneValues(baseline);
		(draft.LAYOUTS as string[]).push('ac3');
		expect(baseline.LAYOUTS.value).toEqual(['eac3', 'aac']);
	});
});

describe('changes', () => {
	test('sends the names that differ and nothing else', () => {
		const baseline = snapshot({ ZONE: 'Pacific/Auckland', CRON: '0 4 * * *' });
		expect(changes({ ZONE: 'UTC', CRON: '0 4 * * *' }, baseline)).toEqual({ ZONE: 'UTC' });
	});

	test('reads an unordered array as a set, so a reorder alone is no change', () => {
		const baseline = snapshot({ EXTS: ['mkv', 'mp4'] });
		expect(changes({ EXTS: ['mp4', 'mkv'] }, baseline)).toEqual({});
		expect(changes({ EXTS: ['mkv'] }, baseline)).toEqual({ EXTS: ['mkv'] });
	});

	test('reads an ordered array in its order, so a drag is a change', () => {
		const baseline = snapshot({ LAYOUTS: ['eac3', 'aac'] });
		const ordered = new Set(['LAYOUTS']);
		expect(changes({ LAYOUTS: ['aac', 'eac3'] }, baseline, ordered)).toEqual({
			LAYOUTS: ['aac', 'eac3']
		});
		expect(changes({ LAYOUTS: ['eac3', 'aac'] }, baseline, ordered)).toEqual({});
	});

	// The service refuses an env-pinned name, so offering one is a save that
	// can only fail.
	test('skips a name the environment pins, changed or dropped', () => {
		const baseline = snapshot({ ZONE: 'UTC', CRON: '0 4 * * *' }, ['ZONE', 'CRON']);
		expect(changes({ ZONE: 'Pacific/Auckland' }, baseline)).toEqual({});
	});

	test('sends a null for a name the draft dropped', () => {
		const baseline = snapshot({ 'BITRATE.eac3': '640k', ZONE: 'UTC' });
		expect(changes({ ZONE: 'UTC' }, baseline)).toEqual({ 'BITRATE.eac3': null });
	});

	test('sends a name the baseline never had', () => {
		expect(changes({ 'BITRATE.aac': '256k' }, snapshot({}))).toEqual({ 'BITRATE.aac': '256k' });
	});
});
