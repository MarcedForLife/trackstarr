// The connections API: which services the settings name, and a live check. The
// check is a POST because it may carry unsaved values.

import { request } from '$lib/api';

export type ConnectionResult = {
	ok: boolean;
	detail: string;
	// Something to do about it, when there is.
	hint: string;
	// For an *arr: connected, stale, missing, or unknown. Empty for the media
	// servers.
	webhook: string;
};

// Narrowed so a fifth service cannot be added without its icon.
export type ServiceName = 'radarr' | 'sonarr' | 'plex' | 'jellyfin';

// Every mark ServiceIcon can draw. IMDb has a logo and nothing to configure.
export type MarkName = ServiceName | 'imdb';

export type Service = {
	name: ServiceName;
	label: string;
	// A source calls us on import; a library is called by us afterwards. Only a
	// source has a webhook and only a library a path map.
	group: 'source' | 'library';
	// The setting names each field edits.
	url: string;
	key: string;
	map?: string;
	// Where a browser reaches the service, for the "Open in" links.
	publicUrl?: string;
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
		url: 'RADARR_URL',
		key: 'RADARR_API_KEY',
		publicUrl: 'RADARR_PUBLIC_URL',
		keyLabel: 'API key',
		lead: 'Calls the webhook on each film import, supplies original languages, and is where films open from the library.',
		placeholder: 'http://radarr:7878'
	},
	{
		name: 'sonarr',
		label: 'Sonarr',
		group: 'source',
		url: 'SONARR_URL',
		key: 'SONARR_API_KEY',
		publicUrl: 'SONARR_PUBLIC_URL',
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
export const MARKS: MarkName[] = [...SERVICES.map((service) => service.name), 'imdb'];

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
