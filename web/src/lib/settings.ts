// The settings API. Values travel typed, land in STATE_DIR/settings.json and
// apply live. A 400 carries startup validation's messages.

import { ApiError, request } from '$lib/api';

export type SettingValue = string | boolean | string[];
// One encoder the layout rows offer, with the limits the page warns about
// before a save. Not an allow-list: an unlisted name is checked against ffmpeg.
export type Codec = {
	name: string;
	max_channels: number;
	containers: string[];
	lossless: boolean;
};
// A credential comes back as set-or-not with an empty value; the service never
// echoes one.
export type Setting = { value: SettingValue; env: boolean; set?: boolean };
export type SettingsSnapshot = {
	// Every rule, its summary and its default mode. Each is set by RULE_<NAME>.
	rules: Record<string, { default: string; summary: string }>;
	// What a rule may be set to, weakest first.
	modes: string[];
	// ISO 639-2/B code to display name. An unlisted code is still valid.
	languages: Record<string, string>;
	// Every zone the service knows. Empty without tzdata; the field is typed into.
	zones: string[];
	// Every extension ALLOWED_EXTS may hold.
	containers: string[];
	// What a layout row may be set to: add, keep or remove.
	actions: string[];
	// A language row's, narrower: the languages rule does the dropping.
	lang_actions: string[];
	// The reserved LANGUAGES name for the title's own language.
	original_lang: string;
	// What a new row of each size is made at, as [encoder, rate].
	stock: Record<string, string[]>;
	// The rates each size is offered at, low to high. Not a limit.
	rates: Record<string, string[]>;
	// The encoders the layout rows offer, in order.
	codecs: Codec[];
	// Names whose value is withheld. An empty field means "leave it alone", so
	// only null clears one.
	secrets: string[];
	settings: Record<string, Setting>;
};

export class SettingsError extends Error {
	constructor(public problems: string[]) {
		super(problems.join('; '));
	}
}

export function getSettings(fetcher: typeof fetch = fetch): Promise<SettingsSnapshot> {
	return request<SettingsSnapshot>('/api/settings', undefined, fetcher);
}

export async function saveSettings(
	changes: Record<string, SettingValue | null>
): Promise<SettingsSnapshot> {
	try {
		return await request<SettingsSnapshot>('/api/settings', {
			method: 'POST',
			body: JSON.stringify(changes)
		});
	} catch (error) {
		// The one refusal with a list: every value the validator turned down.
		if (error instanceof ApiError && error.status === 400) {
			throw new SettingsError(error.answer.problems ?? ['The service refused the change.']);
		}
		throw error;
	}
}
