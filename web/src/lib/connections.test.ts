import { expect, test, vi } from 'vitest';
import {
	arrField,
	connectionChanges,
	connectionSettings,
	newInstance,
	sources
} from './connections';
import { changes, cloneValues } from './draft';
import { snapshot, DEFAULTS } from './demo/settings';

function setup() {
	const shot = snapshot(
		{
			...DEFAULTS,
			RADARR_4K_NAME: 'UHD shelf',
			RADARR_4K_URL: 'http://4k',
			RADARR_4K_API_KEY: ''
		},
		new Set(['RADARR_4K_API_KEY'])
	);
	const baseline = connectionSettings(shot);
	return { instances: shot.arr_instances, baseline, draft: cloneValues(baseline) };
}

test('structured sources expose field provenance without parsing environment names', () => {
	const { instances, baseline, draft } = setup();
	const cards = sources(instances, draft);
	expect(cards.map((card) => card.name)).toEqual(['radarr', 'sonarr', 'radarr-4k']);
	expect(cards[2].label).toBe('UHD shelf');
	expect(baseline[cards[2].key]).toMatchObject({
		env_name: 'RADARR_4K_API_KEY',
		set: true,
		value: ''
	});
	expect(baseline).not.toHaveProperty('RADARR_4K_URL');
});

test('new identity survives typing and clearing a display name, including Public', () => {
	const { instances, baseline, draft } = setup();
	// Works on plain HTTP LAN installs, where randomUUID may be unavailable.
	const random = vi.spyOn(crypto, 'getRandomValues').mockImplementation((array) => {
		(array as Uint8Array).fill(1);
		return array;
	});
	const fresh = newInstance('radarr');
	random.mockRestore();
	instances.push(fresh);
	for (const [field, entry] of Object.entries(fresh.fields))
		draft[`arr:${fresh.id}:${field}`] = entry.value;
	const id = fresh.id;
	for (const label of ['Public', 'UHD', '']) {
		draft[arrField(id, 'name')] = label;
		const body = connectionChanges(instances, baseline, changes(draft, baseline), new Set());
		expect(body.arr_instances).toEqual([
			{
				id,
				type: 'radarr',
				create: true,
				values: { url: '', api_key: '', public_url: '', name: label }
			}
		]);
		expect(sources(instances, draft).at(-1)!.name).toBe(id);
		expect(sources(instances, draft).at(-1)!.label).toBe(label || 'Radarr');
		expect(draft[arrField(id, 'name')]).toBe(label);
	}
});

test('rename sends only the name, credential clear is explicit, and removal is one operation', () => {
	const { instances, baseline, draft } = setup();
	draft[arrField('radarr-4k', 'name')] = 'Films';
	expect(connectionChanges(instances, baseline, changes(draft, baseline), new Set())).toEqual({
		arr_instances: [{ id: 'radarr-4k', type: 'radarr', values: { name: 'Films' } }]
	});
	expect(
		connectionChanges(instances, baseline, { [arrField('radarr-4k', 'api_key')]: null }, new Set())
	).toEqual({
		arr_instances: [{ id: 'radarr-4k', type: 'radarr', values: { api_key: null } }]
	});
	for (const field of Object.keys(instances[2].fields)) delete draft[`arr:radarr-4k:${field}`];
	draft.WEBHOOK_URL = 'http://callback';
	expect(
		connectionChanges(instances, baseline, changes(draft, baseline), new Set(['radarr-4k']))
	).toEqual({
		WEBHOOK_URL: 'http://callback',
		arr_instances: [{ id: 'radarr-4k', remove: true }]
	});
	// Undo restores fields and cancels the removal without sending credentials.
	Object.assign(draft, cloneValues(baseline));
	expect(connectionChanges(instances, baseline, changes(draft, baseline), new Set())).toEqual({});
});
