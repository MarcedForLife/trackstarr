// A series' files by season, for a sheet that would otherwise open on two
// hundred episodes in no useful order.

import { named } from '$lib/format';
import { VERDICTS, type LibraryFile, type Verdict } from '$lib/library';

export type Season = {
	// The number, 0 for specials and null for a file that names none.
	number: number | null;
	label: string;
	files: LibraryFile[];
	// The worst verdict in it, so a shut season still says what it holds.
	state: Verdict;
};

// Sonarr's season and episode off the name; `named` has already cut the tags
// and the title away.
const NUMBERED = /^S(\d{1,3})E(\d{1,3})/i;
// The folder the *arrs put a season in, for a file named some other way.
const FOLDER = /\/Season[ ._-]*(\d{1,3})\//i;
const SPECIALS = /\/Specials?\//i;

function seasonOf(file: LibraryFile): number | null {
	const found = named(file.name).episode.match(NUMBERED);
	if (found) return Number(found[1]);
	const folder = file.path.match(FOLDER);
	if (folder) return Number(folder[1]);
	return SPECIALS.test(file.path) ? 0 : null;
}

function episodeOf(file: LibraryFile): number {
	const found = named(file.name).episode.match(NUMBERED);
	// An extra belongs behind the numbered episodes, not ahead of them.
	return found ? Number(found[2]) : Number.MAX_SAFE_INTEGER;
}

function worst(files: LibraryFile[]): Verdict {
	const held = new Set(files.map((file) => file.status));
	return VERDICTS.find((verdict) => held.has(verdict)) ?? 'unchecked';
}

function label(number: number | null): string {
	if (number === null) return 'Other';
	return number === 0 ? 'Specials' : `Season ${number}`;
}

/**
 * A title's files by season, latest first, specials and unnumbered files last.
 *
 * Null where the grouping would say nothing: a film, or a series in one season.
 */
export function seasons(files: LibraryFile[]): Season[] | null {
	const held = new Map<number | null, LibraryFile[]>();
	for (const file of files) {
		const number = seasonOf(file);
		const kept = held.get(number);
		if (kept) kept.push(file);
		else held.set(number, [file]);
	}
	if (held.size < 2) return null;
	const grouped = [...held].map(([number, episodes]) => ({
		number,
		label: label(number),
		// Episode order inside a season: the service sorts the flat list worst
		// first, which reads as shuffled once the seasons are apart.
		files: episodes.sort(
			(one, two) => episodeOf(one) - episodeOf(two) || one.name.localeCompare(two.name)
		),
		state: worst(episodes)
	}));
	// Latest first, so a season with no number sorts behind every one that has.
	grouped.sort((one, two) => (two.number ?? -1) - (one.number ?? -1));
	return grouped;
}
