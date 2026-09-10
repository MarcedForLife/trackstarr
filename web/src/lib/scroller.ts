// What scrolls the page. Below lg it is the document, so a phone's browser
// chrome retracts with the page as it does everywhere else. From lg up it is
// `main`: the rail and the demo notice stand outside it and stay put while the
// page rubber-bands under a trackpad, as an app's chrome does. Anything that
// reads or sets the page's scroll comes here rather than to `window`.

// Tailwind's lg, where the layout switches.
export const WIDE = '(min-width: 1024px)';

let stage: HTMLElement | null = null;
let query: MediaQueryList | null = null;

function wide(): boolean {
	query ??= window.matchMedia(WIDE);
	return query.matches;
}

/** The layout's `main`, handed over once it exists. */
export function setStage(element: HTMLElement | null): void {
	stage = element;
}

/** How far down the page is. */
export function pageTop(): number {
	return wide() && stage ? stage.scrollTop : window.scrollY;
}

/** Put the page at `top`, at once. */
export function scrollPageTo(top: number): void {
	if (wide() && stage) stage.scrollTop = top;
	else window.scrollTo(0, top);
}
