// The arithmetic under the rules page: layout variable names, what a rate
// means, and the notes a row gives before the save would refuse it. Pure, so
// the same functions answer for the page and a test.

import type { Codec } from '$lib/settings';

/** One AUDIO_LAYOUTS entry. `codec` and `bitrate` are empty on anything but a
 * downmix, which is the only action that encodes. */
export type Row = { name: string; action: string; codec: string; bitrate: string };

/** An entry's fields, told apart by count as the service tells them apart:
 * `5.1` is a downmix at its stock spec, `7.1:remove` an action, `5.1:eac3:448k`
 * a downmix at that encoder and rate. A shape with no meaning reads as a bare
 * downmix, so the row renders and the save carries the service's refusal. */
export function parseRow(entry: string, stock: Record<string, string[]> = {}): Row {
	const parts = entry.split(':').map((part) => part.trim());
	if (parts.length === 2) return { name: parts[0], action: parts[1], codec: '', bitrate: '' };
	if (parts.length === 3) {
		return { name: parts[0], action: 'downmix', codec: parts[1], bitrate: parts[2] };
	}
	const [codec = '', bitrate = ''] = stock[parts[0]] ?? [];
	return { name: parts[0], action: 'downmix', codec, bitrate };
}

/** A row back as the entry the service reads. A downmix writes its spec out even
 * where it matches the stock one, so editing the stock table later cannot move
 * a layout somebody already chose. */
export function formatRow(row: Row): string {
	if (row.action !== 'downmix') return `${row.name}:${row.action}`;
	return `${row.name}:${row.codec}:${row.bitrate}`;
}

/** One row rewritten in place, or moved across the remove boundary.
 *
 * Removed rows are the list's suffix, so a row crossing that boundary joins the
 * end of its new block and one staying on its own side keeps its place. */
export function placeRow(
	entries: string[],
	index: number,
	written: string,
	was: string,
	now: string,
	orderable: number
): string[] {
	const next = [...entries];
	if ((was === 'remove') === (now === 'remove')) {
		next[index] = written;
		return next;
	}
	next.splice(index, 1);
	next.splice(now === 'remove' ? next.length : orderable, 0, written);
	return next;
}

/** One LANGUAGES entry: a language kept, and whether layouts are made in it.
 * `name` is
 * an ISO 639-2/B code, or `original` for the title's own language. */
export type LangRow = { name: string; action: string };

/** One field is a name and reads as a downmix, two state an action. A shape
 * with no meaning reads as a bare downmix, so the row renders and the save
 * carries the service's refusal. */
export function parseLang(entry: string): LangRow {
	const parts = entry.split(':').map((part) => part.trim());
	return { name: parts[0], action: parts.length === 2 ? parts[1] : 'downmix' };
}

/** A language row back as the entry the service reads. A downmix is bare, since
 * that is what it means and there is no spec to pin. */
export function formatLang(row: LangRow): string {
	return row.action === 'downmix' ? row.name : `${row.name}:${row.action}`;
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

/** Whether two rates mean the same, so 640000 and 640k are one notch. */
export function sameRate(left: string, right: string): boolean {
	const bps = rateBps(left);
	return left.trim() === right.trim() || (bps > 0 && bps === rateBps(right));
}

/** The rates offered for a size, with whatever the row already holds folded in
 * so an unlisted one stays visible and selectable. */
export function rateNotches(notches: string[], held: string): string[] {
	const rate = held.trim();
	if (!rate || notches.some((notch) => sameRate(notch, rate))) return notches;
	return [...notches, rate].sort((left, right) => rateBps(left) - rateBps(right));
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
