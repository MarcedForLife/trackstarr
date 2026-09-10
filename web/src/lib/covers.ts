// Covers arriving, and covers called off.
//
// Call off the posters the reader has navigated away from. The browser would
// finish every one for a page that no longer exists, tripling the next page's
// load on a throttled phone. Clearing `src` is the only way to stop an <img>,
// and it must still be in the document, so this hangs off beforeNavigate.

const MARK = 'img[data-cover]';

/** The fade a cover arrives with, so a poster, a list row and the sheet agree.
 * Goes on whatever hides the artwork: the tile a card wears until its cover
 * lands, or the picture itself where that is the top layer.
 *
 * Slow enough to be seen, and linear: an eased fade crosses the middle in a
 * few frames and then spends a third of its length between a tenth and
 * nothing, which the eye cannot see. Even steps read as the fade they are. */
export const COVER_FADE = 'transition-opacity duration-[460ms] ease-linear';

/** How a cover reached the page: still coming, seen here before, or fading in
 * over the tile that stood in for it. */
export type Arrival = 'coming' | 'seen' | 'fading';

// The covers this page has already shown. A reader coming back to the grid
// should find it whole rather than watch every poster fade in again, and the
// sheet's header should not fade in the cover the card under it is wearing.
// Held for the session: a reload is a fresh page and fades once.
const seen = new Set<string>();

/** How a cover that has just landed should show. Whether the reader has been
 * shown it before, rather than how long it took: on a library served over a
 * LAN a first fetch and a cache hit both land within a frame or two, and the
 * picture's own `complete` is true on mount in Chromium and false in Gecko,
 * so neither tells one from the other. */
export function arrival(url: string): Arrival {
	if (seen.has(url)) return 'seen';
	seen.add(url);
	return 'fading';
}

/** How to show a cover that is its own top layer, as a list row's thumb and
 * the sheet's header are: nothing until it lands, then the fade, unless the
 * reader has been shown it already. A card's artwork is a background layer
 * instead, and PosterCard fades the tile over it. */
export function coverShow(state: Arrival): string {
	return state === 'coming' ? 'opacity-0' : state === 'fading' ? COVER_FADE : '';
}

export function dropPendingCovers() {
	for (const image of document.querySelectorAll<HTMLImageElement>(MARK)) {
		// A finished poster is cached; clearing it would only blank the tile.
		if (!image.complete) image.removeAttribute('src');
	}
}
