// Call off the posters the reader has navigated away from. The browser would
// finish every one for a page that no longer exists, tripling the next page's
// load on a throttled phone. Clearing `src` is the only way to stop an <img>,
// and it must still be in the document, so this hangs off beforeNavigate.

const MARK = 'img[data-cover]';

export function dropPendingCovers() {
	for (const image of document.querySelectorAll<HTMLImageElement>(MARK)) {
		// A finished poster is cached; clearing it would only blank the tile.
		if (!image.complete) image.removeAttribute('src');
	}
}
