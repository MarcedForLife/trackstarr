// Artwork for the demo's titles: the Wikimedia Commons files ./posters.json
// names, fetched at build time by scripts/demo-posters.mjs under the licences
// it records. Every title has one; a checkout where the script has not run
// gets no address that loads, so each card falls to the tile the app draws
// for any missing poster.

import manifest from './posters.json';
import { catalogue } from './catalogue';
import { slug } from './util';

type Listed = { file: string; by: string; licence: string };

// Which title takes which Commons file, and whose it is.
const LISTED: Record<string, Listed> = manifest;

// The posters the build was given, by path: what ./posters holds once the
// script has run, and nothing in a plain checkout. Resolved at build time, so a
// missing file costs no request.
const fetched = import.meta.glob('./posters/*.{jpg,png}', {
	eager: true,
	query: '?url',
	import: 'default'
}) as Record<string, string>;

// An address that is no image, so the card reports it missing without a
// request and shows its own tile.
const NONE = 'data:,';

function fetchedFor(id: string): string | undefined {
	const stem = `./posters/${slug(id)}`;
	return fetched[`${stem}.jpg`] ?? fetched[`${stem}.png`];
}

/** One poster's provenance, for the credits: whose it is, under what licence,
 * and where on Commons. */
export type Credit = Listed & { id: string; name: string; url: string };

/** Every real poster this build shows, in the order the manifest lists them. */
export function credits(): Credit[] {
	const names = new Map(catalogue().map((title) => [title.id, title.name]));
	return Object.entries(LISTED)
		.filter(([id]) => fetchedFor(id))
		.map(([id, listed]) => ({
			...listed,
			id,
			name: names.get(id) ?? id,
			url: `https://commons.wikimedia.org/wiki/${encodeURIComponent(listed.file.replaceAll(' ', '_'))}`
		}));
}

/** The poster for a title: the real one where the build has it. */
export function poster(id: string): string {
	return fetchedFor(id) ?? NONE;
}
