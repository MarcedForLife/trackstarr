// A series' files by season, for a sheet that would otherwise open on two
// hundred episodes in no useful order.

import { named } from '$lib/format';
import { worstOf, type LibraryFile, type Verdict } from '$lib/library';

export type Season = {
	// The number, 0 for specials and null for a file that names none.
	number: number | null;
	label: string;
	files: LibraryFile[];
	// The worst verdict in it, so a shut season still says what it holds.
	state: Verdict;
};

// Sonarr's season and episode off the name. `named` has already cut the tags
// and the title away.
const NUMBERED = /^S(\d{1,3})E(\d{1,3})/i;
// The folder the *arrs put a season in, for a file named some other way.
const FOLDER = /\/Season[ ._-]*(\d{1,3})\//i;
const SPECIALS = /\/Specials?\//i;

// A file's place in its show. An extra's -1 sorts it after the numbered
// episodes.
function placed(file: LibraryFile): { season: number | null; episode: number } {
	const found = named(file.name).episode.match(NUMBERED);
	if (found) return { season: Number(found[1]), episode: Number(found[2]) };
	const folder = file.path.match(FOLDER);
	const season = folder ? Number(folder[1]) : SPECIALS.test(file.path) ? 0 : null;
	return { season, episode: -1 };
}

/** Match the service's file order before pagination, including a single season. */
export function newestFirst(one: LibraryFile, two: LibraryFile): number {
	const first = placed(one);
	const second = placed(two);
	return (
		(second.season ?? -1) - (first.season ?? -1) ||
		second.episode - first.episode ||
		one.path.localeCompare(two.path)
	);
}

function label(number: number | null): string {
	if (number === null) return 'Other';
	return number === 0 ? 'Specials' : `Season ${number}`;
}

// Newest numbered season first, then the files naming no season, specials
// last. The sheet opens the first group, which should be the show itself.
function rank(number: number | null): number {
	return number === 0 ? -2 : (number ?? -1);
}

/**
 * A title's files by season, latest first, unnumbered files then specials last.
 *
 * Null where the grouping would say nothing: a film, or a series in one season.
 */
export function seasons(files: LibraryFile[]): Season[] | null {
	const held = new Map<number | null, { file: LibraryFile; episode: number }[]>();
	for (const file of files) {
		const { season, episode } = placed(file);
		const kept = held.get(season);
		if (kept) kept.push({ file, episode });
		else held.set(season, [{ file, episode }]);
	}
	if (held.size < 2) return null;
	const grouped = [...held].map(([number, entries]) => {
		// Keep the latest episodes first inside each season.
		entries.sort(
			(one, two) => two.episode - one.episode || one.file.name.localeCompare(two.file.name)
		);
		const episodes = entries.map((entry) => entry.file);
		return { number, label: label(number), files: episodes, state: worstOf(episodes) };
	});
	grouped.sort((one, two) => rank(two.number) - rank(one.number));
	return grouped;
}
