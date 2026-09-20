// The connections API: which services the settings name, and a live check. The
// check is a POST because it may carry unsaved values.

import { request } from '$lib/api';
import type {
	ArrInstance,
	ArrType,
	ArrField,
	ArrChange,
	Setting,
	SettingValue,
	SettingsSnapshot,
	SettingsChanges
} from '$lib/settings';

export type ConnectionResult = {
	// Root diagnostics: ready, attention, unknown, or empty for media servers.
	paths?: string;
	ok: boolean;
	detail: string;
	// Something to do about it, when there is.
	hint: string;
	// For an *arr: connected, unreachable, stale, missing, or unknown. Empty for
	// the media servers.
	webhook: string;
	// What the *arr said when it could not call us. Only for unreachable.
	webhook_detail: string;
};

// Narrowed so a fifth service cannot be added without its icon.
export type ServiceName = 'radarr' | 'sonarr' | 'plex' | 'jellyfin';

// Every mark ServiceIcon can draw. IMDb has a logo and nothing to configure.
export type MarkName = ServiceName | 'imdb';

export type Service = {
	name: string;
	type?: ServiceName;
	// How the pages name it: the name setting, or the type and ID.
	label: string;
	// A source calls us on import; a library is called by us afterwards. Only a
	// source has a webhook and only a library a path map.
	group: 'source' | 'library';
	// Sources carry their structured identity; field keys are local editor paths.
	instance?: ArrInstance;
	// The setting names each field edits.
	url: string;
	key: string;
	map?: string;
	// Where a browser reaches the service, for the "Open in" links.
	publicUrl?: string;
	// The display name. Only a source has one.
	nameField?: string;
	keyLabel: string;
	lead: string;
	placeholder: string;
};

// The names match connections.SERVICES in the service.
export const SERVICES: Service[] = [
	{
		name: 'radarr',
		label: 'Radarr',
		group: 'source',
		url: 'arr:radarr:url',
		key: 'arr:radarr:api_key',
		publicUrl: 'arr:radarr:public_url',
		nameField: 'arr:radarr:name',
		keyLabel: 'API key',
		lead: 'Calls the webhook on each film import, supplies original languages, and is where films open from the library.',
		placeholder: 'http://radarr:7878'
	},
	{
		name: 'sonarr',
		label: 'Sonarr',
		group: 'source',
		url: 'arr:sonarr:url',
		key: 'arr:sonarr:api_key',
		publicUrl: 'arr:sonarr:public_url',
		nameField: 'arr:sonarr:name',
		keyLabel: 'API key',
		lead: 'Calls the same webhook for episodes. A season import arrives as one call with many files. Series open here from the library.',
		placeholder: 'http://sonarr:8989'
	},
	{
		name: 'plex',
		label: 'Plex',
		group: 'library',
		url: 'PLEX_URL',
		key: 'PLEX_TOKEN',
		map: 'PLEX_PATH_MAP',
		publicUrl: 'PLEX_PUBLIC_URL',
		keyLabel: 'Token',
		lead: 'Asked to rescan the folder of each rewritten file. Titles open here from the library.',
		placeholder: 'http://plex:32400'
	},
	{
		name: 'jellyfin',
		label: 'Jellyfin',
		group: 'library',
		url: 'JELLYFIN_URL',
		key: 'JELLYFIN_API_KEY',
		map: 'JELLYFIN_PATH_MAP',
		publicUrl: 'JELLYFIN_PUBLIC_URL',
		keyLabel: 'API key',
		lead: 'Rescans the same way, by file rather than folder. Emby speaks this API too.',
		placeholder: 'http://jellyfin:8096'
	}
];

// Built off SERVICES so a fifth connection is drawable when configurable.
export const MARKS: MarkName[] = [
	...SERVICES.map((service) => service.name as ServiceName),
	'imdb'
];

export const ARR_FIELDS: ArrField[] = ['url', 'api_key', 'public_url', 'name'];

export function arrField(id: string, field: ArrField): string {
	return `arr:${id}:${field}`;
}

/** Project structured records into the generic field editor, never storage names. */
export function connectionSettings(snapshot: SettingsSnapshot): Record<string, Setting> {
	const settings = { ...snapshot.settings };
	for (const instance of snapshot.arr_instances) {
		for (const field of ARR_FIELDS) settings[arrField(instance.id, field)] = instance.fields[field];
	}
	return settings;
}

/** Only changed fields travel. Creation and removal are explicit operations. */
export function connectionChanges(
	instances: ArrInstance[],
	baseline: Record<string, Setting>,
	changes: Record<string, SettingValue | null>,
	removed: Set<string>
): SettingsChanges {
	const ordinary = { ...changes };
	const operations: ArrChange[] = [];
	for (const instance of instances) {
		const values: Partial<Record<ArrField, string | null>> = {};
		for (const field of ARR_FIELDS) {
			const key = arrField(instance.id, field);
			if (key in ordinary) {
				values[field] = ordinary[key] as string | null;
				delete ordinary[key];
			}
		}
		if (removed.has(instance.id)) operations.push({ id: instance.id, remove: true });
		else if (Object.keys(values).length)
			operations.push({
				id: instance.id,
				type: instance.type,
				...(!(arrField(instance.id, 'url') in baseline) ? { create: true } : {}),
				values
			});
	}
	return { ...ordinary, ...(operations.length ? { arr_instances: operations } : {}) };
}

/** Allocate identity once, independently of its editable name. */
export function newInstance(type: ArrType): ArrInstance {
	return {
		id: `${type}-${Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join('')}`,
		type,
		fields: Object.fromEntries(
			ARR_FIELDS.map((field) => [field, { value: '', env: false }])
		) as Record<ArrField, Setting>
	};
}

/** Ask whether one connection works. Empty `url` or `key` means the saved
 * value, which is how an untouched password field travels. */
export function testConnection(
	service: string,
	url: string,
	key: string
): Promise<ConnectionResult> {
	return request<ConnectionResult>('/api/connections/test', {
		method: 'POST',
		body: JSON.stringify({ service, url, key })
	});
}

/** Resolve an optional name without storing its fallback in the settings. */
export function instanceName(id: string, name = ''): string {
	const [type, suffix] = id.split('-', 2);
	const word = type[0].toUpperCase() + type.slice(1);
	return name.trim() || (suffix && !/^[0-9a-f]{32}$/.test(suffix) ? `${word} ${suffix}` : word);
}

export function fallbackName(service: Service): string {
	return service.instance
		? instanceName(service.instance.id)
		: SERVICES.find((entry) => entry.name === service.name)!.label;
}

/** Build source cards from explicit records. Names never determine identity. */
export function sources(instances: ArrInstance[], values: Record<string, SettingValue>): Service[] {
	return instances.map((instance) => {
		const base = SERVICES.find((service) => service.name === instance.type)!;
		return {
			...base,
			instance,
			name: instance.id,
			type: instance.type,
			url: arrField(instance.id, 'url'),
			key: arrField(instance.id, 'api_key'),
			publicUrl: arrField(instance.id, 'public_url'),
			nameField: arrField(instance.id, 'name'),
			label: instanceName(instance.id, String(values[arrField(instance.id, 'name')] ?? ''))
		};
	});
}

/** What is wrong with a name, or nothing. The service refuses the same. */
export function nameProblem(text: string): string {
	return /^[A-Za-z0-9 _-]{0,40}$/.test(text)
		? ''
		: 'Use letters, numbers, spaces, hyphens and underscores, up to 40 characters.';
}
