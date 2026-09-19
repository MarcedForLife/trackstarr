// The connections API: which services the settings name, and a live check. The
// check is a POST because it may carry unsaved values.

import { request } from '$lib/api';

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
	kind?: ServiceName;
	// How the pages name it: the label setting, or the kind and ID.
	label: string;
	// A source calls us on import; a library is called by us afterwards. Only a
	// source has a webhook and only a library a path map.
	group: 'source' | 'library';
	// What its setting names start with: RADARR for the first Radarr, RADARR_4K
	// for a named one.
	prefix: string;
	// The setting names each field edits.
	url: string;
	key: string;
	map?: string;
	// Where a browser reaches the service, for the "Open in" links.
	publicUrl?: string;
	// The display name. Only a source has one.
	labelName?: string;
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
		prefix: 'RADARR',
		url: 'RADARR_URL',
		key: 'RADARR_API_KEY',
		publicUrl: 'RADARR_PUBLIC_URL',
		labelName: 'RADARR_LABEL',
		keyLabel: 'API key',
		lead: 'Calls the webhook on each film import, supplies original languages, and is where films open from the library.',
		placeholder: 'http://radarr:7878'
	},
	{
		name: 'sonarr',
		label: 'Sonarr',
		group: 'source',
		prefix: 'SONARR',
		url: 'SONARR_URL',
		key: 'SONARR_API_KEY',
		publicUrl: 'SONARR_PUBLIC_URL',
		labelName: 'SONARR_LABEL',
		keyLabel: 'API key',
		lead: 'Calls the same webhook for episodes. A season import arrives as one call with many files. Series open here from the library.',
		placeholder: 'http://sonarr:8989'
	},
	{
		name: 'plex',
		label: 'Plex',
		group: 'library',
		prefix: 'PLEX',
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
		prefix: 'JELLYFIN',
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

// The settings a named instance is made of, after its prefix.
export const INSTANCE_SUFFIXES = ['URL', 'API_KEY', 'PUBLIC_URL', 'LABEL'];

// One of them, with the prefix's two halves captured. PUBLIC is skipped, since
// RADARR_PUBLIC_URL is the first Radarr's own setting.
const INSTANCE_SETTING =
	/^(RADARR|SONARR)_((?!PUBLIC_)[A-Z0-9]+)_(?:URL|API_KEY|PUBLIC_URL|LABEL)$/;

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

/** What a source is called with no label set: its kind, and for a named
 * instance the ID its keys carry. The service spells it the same way. */
export function fallbackLabel(service: Service): string {
	const kind = SERVICES.find((entry) => entry.name === (service.kind ?? service.name))!.label;
	return service.kind ? `${kind} ${service.name.split('-')[1]}` : kind;
}

/** Every source the settings name: the two fixed ones, then each named
 * instance with the same fields under its own keys. Labels are read off
 * `values`, so a rename shows before it is saved. */
export function sources(values: Record<string, unknown>): Service[] {
	const fixed = SERVICES.filter((service) => service.group === 'source');
	const prefixes = new Set(
		Object.keys(values).flatMap((name) => {
			const match = name.match(INSTANCE_SETTING);
			return match ? [`${match[1]}_${match[2]}`] : [];
		})
	);
	const named = [...prefixes].sort().map((prefix): Service => {
		const [kind, id] = prefix.toLowerCase().split('_');
		const base = fixed.find((service) => service.name === kind)!;
		return {
			...base,
			name: `${kind}-${id}`,
			kind: kind as ServiceName,
			prefix,
			url: `${prefix}_URL`,
			key: `${prefix}_API_KEY`,
			publicUrl: `${prefix}_PUBLIC_URL`,
			labelName: `${prefix}_LABEL`
		};
	});
	return [...fixed, ...named].map((service) => ({
		...service,
		label: String(values[service.labelName!] ?? '').trim() || fallbackLabel(service)
	}));
}

/** The ID a new instance's keys carry, from its name: the letters and digits,
 * uppercased, or the next free number when it has none. A number is added
 * when the result is taken. PUBLIC is skipped, as above. */
export function instanceId(text: string, taken: Set<string>): string {
	const stem = text.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
	const free = (candidate: string) => candidate !== 'PUBLIC' && !taken.has(candidate);
	if (stem && free(stem)) return stem;
	for (let count = 2; ; count++) {
		if (free(`${stem}${count}`)) return `${stem}${count}`;
	}
}

/** What is wrong with a label, or nothing. The service refuses the same. */
export function labelProblem(text: string): string {
	return /^[A-Za-z0-9 _-]{0,40}$/.test(text)
		? ''
		: 'Use letters, numbers, spaces, hyphens and underscores, up to 40 characters.';
}
