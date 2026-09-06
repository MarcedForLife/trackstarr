// The arithmetic under the rules page: layout variable names, what a rate
// means, and the notes a row gives before the save would refuse it. Pure, so
// the same functions answer for the page and a test.

import type { Codec } from '$lib/settings';

// A layout written 5.1 is set by AUDIO_CODEC_5_1 and AUDIO_BITRATE_5_1.
export function bitrateName(layout: string): string {
	return 'AUDIO_BITRATE_' + layout.replaceAll('.', '_');
}

export function codecName(layout: string): string {
	return 'AUDIO_CODEC_' + layout.replaceAll('.', '_');
}

/** A layout and the rate it is set to be made at. */
export type Rated = { layout: string; rate: string };

// 5.1 is six channels. The service's own reading, so notes and errors agree.
export function channelsOf(layout: string): number {
	const matched = /^(\d)\.(\d)$/.exec(layout);
	return matched ? Number(matched[1]) + Number(matched[2]) : 0;
}

// The service's own grammar for a rate.
export function rateBps(rate: string): number {
	const matched = /^(\d+)([km]?)$/.exec(rate.trim().toLowerCase());
	if (!matched) return 0;
	return Number(matched[1]) * { '': 1, k: 1000, m: 1000000 }[matched[2] as '' | 'k' | 'm'];
}

// The band the service accepts for the low-bitrate share.
export const BELOW_MIN = 10;
export const BELOW_MAX = 90;

/** What startup would check, said while the field is still open. */
export function belowProblem(entry: string): string {
	const typed = entry.trim();
	if (!/^-?\d+$/.test(typed)) return 'That has to be a whole number.';
	const percent = Number(typed);
	if (percent < BELOW_MIN || percent > BELOW_MAX) {
		return `That has to be between ${BELOW_MIN} and ${BELOW_MAX}.`;
	}
	return '';
}

/** The share as a rate per layout: "under 160k" is what a track reports. */
export function belowNote(entry: string, rated: readonly Rated[]): string {
	if (belowProblem(entry)) return '';
	const percent = Number(entry.trim());
	const each = rated
		.map(({ layout, rate }) => [layout, rateBps(rate)] as const)
		.filter(([, rate]) => rate > 0)
		.map(([layout, rate]) => `a ${layout} under ${Math.round((rate * percent) / 100000)}k`);
	return each.length ? `Low bitrate means ${each.join(', ')}.` : '';
}

/** What a layout's encoder cannot do, given the output containers. Each would
 * refuse the save. An unlisted codec saves and is checked against ffmpeg. */
export function codecNotes(
	layout: string,
	codec: Codec | undefined,
	outputExts: readonly string[]
): string[] {
	if (!codec) return [];
	const notes: string[] = [];
	const channels = channelsOf(layout);
	if (channels > codec.max_channels) {
		notes.push(
			`${codec.name} encodes at most ${codec.max_channels} channels, so it cannot make a ${layout} track.`
		);
	}
	const unwritable = outputExts.filter((ext) => !codec.containers.includes(ext));
	if (unwritable.length) {
		notes.push(
			`${codec.name} cannot be written into ${unwritable.join(', ')}, selected under Containers below.`
		);
	}
	if (codec.lossless) {
		notes.push(`${codec.name} is lossless, so the rate beside ${layout} does nothing.`);
	}
	return notes;
}

// A new layout arrives with its size's usual encoder and rate, since either
// missing refuses the save. 6.1 and 7.1 are past ac3's ceiling, so aac.
export const STOCK_BITRATES: Record<string, string> = {
	'1.0': '160k',
	'2.0': '320k',
	'5.1': '640k',
	'6.1': '704k',
	'7.1': '768k'
};
export const STOCK_CODECS: Record<string, string> = {
	'1.0': 'aac',
	'2.0': 'aac',
	'5.1': 'ac3',
	'6.1': 'aac',
	'7.1': 'aac'
};

// The display name for an ISO 639-2/B code, or the code itself when unnamed.
export function langLabel(languages: Record<string, string>, code: string): string {
	return languages[code] ?? code;
}

/** A typed language as a code: "English" and "eng" both land as eng; anything
 * else is taken as a code. */
export function langCode(languages: Record<string, string>, entry: string): string {
	const typed = entry.trim().toLowerCase();
	for (const [code, name] of Object.entries(languages)) {
		if (name.toLowerCase() === typed) return code;
	}
	return typed;
}
