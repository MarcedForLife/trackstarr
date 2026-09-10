// The demo's settings: the service's defaults with connections filled in and
// REWRITE_MODE at `all`, so the buttons that rewrite are live, plus the
// vocabulary the settings pages draw their controls from.

import type { SettingsSnapshot, SettingValue } from '$lib/settings';
import { ORIGINAL, STOCK, type Settings } from './judge';

// Every rule, its default mode and the summary its row shows, as the service
// states them.
const RULES: Record<string, { default: string; summary: string }> = {
	languages: {
		default: 'always',
		summary:
			'Drop audio and subtitle tracks in a language LANGUAGES does not name. Untagged tracks always stay.'
	},
	commentary: {
		default: 'never',
		summary:
			'Drop commentary, described-audio and isolated-score tracks. They are never downmix sources either way.'
	},
	sdh: {
		default: 'alongside',
		summary:
			'Drop an SDH (deaf and hard-of-hearing) subtitle when the same language keeps a full one. Forced subtitles always stay.'
	},
	regenerate: {
		default: 'never',
		summary:
			"Rebuild the downmixes trackstarr made when their codec or bitrate no longer matches the settings (REGENERATE_SCOPE=generated). With REGENERATE_SCOPE=all, also replace any layout-sized track reported under REGENERATE_BELOW_PERCENT of its layout's rate where a surviving bigger track has more to give."
	},
	cover_art: { default: 'always', summary: 'Drop embedded cover art.' },
	release_tags: {
		default: 'alongside',
		summary: 'Clear release tags from track and container titles.'
	},
	stray_streams: {
		default: 'alongside',
		summary: 'Drop data and timecode streams nothing plays.'
	},
	order: {
		default: 'always',
		summary:
			'Order streams: video, audio in AUDIO_LAYOUTS order then other sizes by channel count, subtitles, attachments.'
	},
	remux: {
		default: 'never',
		summary:
			'Rewrite MP4 and M4V into Matroska, the container every rule works in and the only one whose track tags the library can edit in place. Text subtitles convert to SRT.'
	}
};

const MODES = ['never', 'alongside', 'always'];

// ISO 639-2/B codes to names, for the language rows.
const LANGUAGES: Record<string, string> = {
	ara: 'Arabic',
	chi: 'Chinese',
	cze: 'Czech',
	dan: 'Danish',
	dut: 'Dutch',
	eng: 'English',
	fin: 'Finnish',
	fre: 'French',
	ger: 'German',
	gre: 'Greek',
	heb: 'Hebrew',
	hin: 'Hindi',
	hun: 'Hungarian',
	ita: 'Italian',
	jpn: 'Japanese',
	kor: 'Korean',
	nor: 'Norwegian',
	pol: 'Polish',
	por: 'Portuguese',
	rus: 'Russian',
	spa: 'Spanish',
	swe: 'Swedish',
	tha: 'Thai',
	tur: 'Turkish',
	ukr: 'Ukrainian',
	vie: 'Vietnamese'
};

const ZONES = [
	'Africa/Johannesburg',
	'America/Chicago',
	'America/Los_Angeles',
	'America/New_York',
	'America/Sao_Paulo',
	'Asia/Kolkata',
	'Asia/Singapore',
	'Asia/Tokyo',
	'Australia/Melbourne',
	'Australia/Sydney',
	'Europe/Berlin',
	'Europe/London',
	'Europe/Paris',
	'Pacific/Auckland',
	'UTC'
];

const RATES: Record<string, string[]> = {
	'1.0': ['96k', '128k', '160k', '192k'],
	'2.0': ['128k', '192k', '256k', '320k', '384k'],
	'5.1': ['384k', '448k', '512k', '640k'],
	'6.1': ['512k', '640k', '704k', '768k'],
	'7.1': ['512k', '640k', '768k', '896k']
};

const EVERY_CONTAINER = ['.m4v', '.mkv', '.mp4'];
const MATROSKA = ['.mkv'];

const CODECS = [
	{ name: 'aac', max_channels: 8, containers: EVERY_CONTAINER, lossless: false },
	{ name: 'ac3', max_channels: 6, containers: EVERY_CONTAINER, lossless: false },
	{ name: 'eac3', max_channels: 6, containers: EVERY_CONTAINER, lossless: false },
	{ name: 'libopus', max_channels: 8, containers: MATROSKA, lossless: false },
	{ name: 'libfdk_aac', max_channels: 8, containers: EVERY_CONTAINER, lossless: false },
	{ name: 'flac', max_channels: 8, containers: MATROSKA, lossless: true }
];

export const SECRETS = ['JELLYFIN_API_KEY', 'PLEX_TOKEN', 'RADARR_API_KEY', 'SONARR_API_KEY'];

/** Names the environment pins, which the pages grey out. */
export const ENV_PINNED = new Set(['MEDIA_DIRS']);

export const DEFAULTS: Settings = {
	ALLOWED_EXTS: EVERY_CONTAINER,
	AUDIO_LAYOUTS: ['2.0', '5.1'],
	COMMENTARY_PATTERN:
		"comment|descriptive|described\\s*video|audio\\s*description|isolated\\s*score|director'?s?\\s*track|interview|behind\\s*the\\s*scenes",
	FFMPEG_TIMEOUT: '7200',
	FORCED_PATTERN: '\\bforced\\b',
	HARDLINK_RECHECK: '900',
	IMDB_RATINGS: true,
	JELLYFIN_API_KEY: '',
	JELLYFIN_PATH_MAP: [],
	JELLYFIN_PUBLIC_URL: '',
	JELLYFIN_URL: '',
	LANGUAGES: ['original', 'eng'],
	MAX_CONCURRENT_REWRITES: '1',
	MEDIA_DIRS: ['/data/media/movies', '/data/media/tv'],
	PLEX_PATH_MAP: ['/data/media=/srv/media'],
	PLEX_PUBLIC_URL: 'https://app.plex.tv/desktop',
	PLEX_TOKEN: '',
	PLEX_URL: 'http://plex:32400',
	PROBE_TIMEOUT: '180',
	PROBE_WORKERS: '4',
	RADARR_API_KEY: '',
	RADARR_PUBLIC_URL: '',
	RADARR_URL: 'http://radarr:7878',
	REGENERATE_ABOVE_PERCENT: '0',
	REGENERATE_BELOW_PERCENT: '80',
	REGENERATE_SCOPE: 'generated',
	RELEASE_TAG_PATTERN:
		'\\d+\\s*k?bps|\\bx?26[45]\\b|\\bhevc\\b|\\bavc\\b|\\b(?:480|576|720|1080|2160)[pi]\\b|\\b(?:blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|remux)\\b',
	REWRITE_MODE: 'all',
	SDH_PATTERN: '\\bsdh\\b|\\bcc\\b|hearing[\\s._-]*impaired',
	SKIP_HARDLINKS: true,
	SONARR_API_KEY: '',
	SONARR_PUBLIC_URL: '',
	SONARR_URL: 'http://sonarr:8989',
	SWEEP_AT: '0 3 * * *',
	TZ: 'UTC',
	WEBHOOK_URL: 'http://trackstarr:5120',
	...Object.fromEntries(
		Object.entries(RULES).map(([name, rule]) => [`RULE_${name.toUpperCase()}`, rule.default])
	)
};

/** The names whose change drops every stored verdict: the rules, and what
 * they read. */
export const RULE_SETTINGS = new Set([
	'ALLOWED_EXTS',
	'AUDIO_LAYOUTS',
	'LANGUAGES',
	'COMMENTARY_PATTERN',
	'FORCED_PATTERN',
	'RELEASE_TAG_PATTERN',
	'SDH_PATTERN',
	'REGENERATE_SCOPE',
	'REGENERATE_ABOVE_PERCENT',
	'REGENERATE_BELOW_PERCENT',
	...Object.keys(RULES).map((name) => `RULE_${name.toUpperCase()}`)
]);

export function snapshot(settings: Settings, secretsSet: Set<string>): SettingsSnapshot {
	const values: SettingsSnapshot['settings'] = {};
	for (const [name, value] of Object.entries(settings)) {
		values[name] = { value, env: ENV_PINNED.has(name) };
		if (SECRETS.includes(name)) values[name] = { value: '', env: false, set: secretsSet.has(name) };
	}
	return {
		rules: RULES,
		modes: MODES,
		languages: LANGUAGES,
		zones: ZONES,
		containers: EVERY_CONTAINER,
		actions: ['downmix', 'keep', 'remove'],
		lang_actions: ['downmix', 'keep'],
		original_lang: ORIGINAL,
		stock: STOCK,
		rates: RATES,
		codecs: CODECS,
		secrets: SECRETS,
		settings: values
	};
}

/** What startup validation would say about these values: every problem, or
 * none. Only the checks a demo visitor is likely to trip. */
export function problems(settings: Settings): string[] {
	const found: string[] = [];
	const layouts = settings.AUDIO_LAYOUTS;
	if (Array.isArray(layouts)) {
		const counts = layouts.map((entry) => entry.split(':')[0]);
		const doubled = counts.filter((name, at) => counts.indexOf(name) !== at);
		for (const name of new Set(doubled)) found.push(`AUDIO_LAYOUTS names ${name} twice`);
		for (const entry of layouts) {
			if (!RATES[entry.split(':')[0]] && entry.split(':').length < 3)
				found.push(`AUDIO_LAYOUTS: ${entry} has no stock encoder, so it must say`);
		}
	}
	if (!Array.isArray(settings.LANGUAGES) || !settings.LANGUAGES.length)
		found.push('LANGUAGES must name at least one language');
	for (const name of [
		'COMMENTARY_PATTERN',
		'SDH_PATTERN',
		'FORCED_PATTERN',
		'RELEASE_TAG_PATTERN'
	]) {
		try {
			new RegExp(String(settings[name] ?? ''));
		} catch {
			found.push(`${name} is not a valid regular expression`);
		}
	}
	const at = String(settings.SWEEP_AT ?? '');
	if (at && at.trim().split(/\s+/).length !== 5) found.push('SWEEP_AT needs five fields');
	return found;
}

/** A value as the history spells it. */
export function spelled(value: SettingValue | null | undefined): SettingValue {
	return value ?? '';
}

/** The settings as a run's summary records them, and the id that names that
 * shape. */
export function configOf(settings: Settings, version: string): Record<string, unknown> {
	const list = (name: string) =>
		Array.isArray(settings[name]) ? (settings[name] as string[]) : [];
	const stockOf = (entry: string) => {
		const [name, second, third] = entry.split(':');
		if (third !== undefined) return `${name}:downmix:${second}:${third}`;
		if (second) return `${name}:${second}`;
		const [codec, rate] = STOCK[name] ?? ['aac', '320k'];
		return `${name}:downmix:${codec}:${rate}`;
	};
	return {
		version,
		languages: list('LANGUAGES').map((entry) => (entry.includes(':') ? entry : `${entry}:downmix`)),
		allowed_exts: [...list('ALLOWED_EXTS')].sort(),
		rule_modes: Object.keys(RULES)
			.sort()
			.map((name) => [name, settings[`RULE_${name.toUpperCase()}`]]),
		regenerate_scope: settings.REGENERATE_SCOPE,
		regenerate_below: Number(settings.REGENERATE_BELOW_PERCENT),
		regenerate_above: Number(settings.REGENERATE_ABOVE_PERCENT),
		audio_layouts: list('AUDIO_LAYOUTS').map(stockOf),
		skip_hardlinks: settings.SKIP_HARDLINKS,
		commentary_re: settings.COMMENTARY_PATTERN,
		sdh_re: settings.SDH_PATTERN,
		forced_re: settings.FORCED_PATTERN,
		release_tag_re: settings.RELEASE_TAG_PATTERN
	};
}
