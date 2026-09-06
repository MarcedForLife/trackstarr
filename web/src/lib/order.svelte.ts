// What order a shelf is in, and which order a page opens on. Shared by the
// grid's menu, the overview's strip and the Appearance settings. Defaults are
// per browser, like the theme.

import type { Card } from '$lib/library';
import { keep, stored } from '$lib/prefs';

export type Sort = 'worst' | 'name' | 'added' | 'processed' | 'year' | 'size' | 'changes';

// Which way an order runs.
export type Flow = 'asc' | 'desc';

export const FLOWS: readonly Flow[] = ['asc', 'desc'];

// "Newest" is arrival, "Release year" is the decade; "Processed" is when
// trackstarr last had the files open, the order to read a night's sweep in.
export const SORTS: { value: Sort; label: string }[] = [
	{ value: 'worst', label: 'Worst first' },
	{ value: 'name', label: 'Name' },
	{ value: 'added', label: 'Newest' },
	{ value: 'processed', label: 'Processed' },
	{ value: 'year', label: 'Release year' },
	{ value: 'size', label: 'Size' },
	{ value: 'changes', label: 'Changes' }
];

const VALUES = SORTS.map((option) => option.value);

// Each order's natural direction, so the flip button can say which. Only Name
// reads upward; the rest lead with the most of what they sort on.
export const FLOW: Record<Sort, Flow> = {
	worst: 'desc',
	name: 'asc',
	added: 'desc',
	processed: 'desc',
	year: 'desc',
	size: 'desc',
	changes: 'desc'
};

// Worst first is missing on purpose: the shelf arrives in that order, and a
// second implementation would drift. Newest is when the title joined the
// library, with release year breaking ties.
export const ORDER: Record<string, (a: Card, b: Card) => number> = {
	name: (a, b) => a.name.localeCompare(b.name),
	added: (a, b) => (b.added ?? 0) - (a.added ?? 0) || (b.year ?? 0) - (a.year ?? 0),
	processed: (a, b) => (b.processed ?? 0) - (a.processed ?? 0),
	year: (a, b) => (b.year ?? 0) - (a.year ?? 0),
	// Bytes on disk, summed over the title's files.
	size: (a, b) => (b.bytes ?? 0) - (a.bytes ?? 0),
	// The service puts the weight on the card, since the strip sorts on it too.
	// See _weight in library.py.
	changes: (a, b) => (b.weight ?? 0) - (a.weight ?? 0)
};

const GRID_KEY = 'library-order';
const GRID_FLOW_KEY = 'library-flow';
const STRIP_KEY = 'strip-order';

// Both open on what trackstarr last had open: the titles just swept.
const GRID_DEFAULT: Sort = 'processed';
const STRIP_DEFAULT: Sort = 'processed';

// Read once before the state below, since the flow's default depends on it.
const opensOn = stored(GRID_KEY, VALUES, GRID_DEFAULT);

let grid = $state<Sort>(opensOn);
// Which way the grid's order runs when it opens: somebody turning an order over
// for good.
let gridFlow = $state<Flow>(stored(GRID_FLOW_KEY, FLOWS, FLOW[opensOn]));
let strip = $state<Sort>(stored(STRIP_KEY, VALUES, STRIP_DEFAULT));

export const order = {
	// What the grid opens on; its menu still changes it for a visit.
	get grid() {
		return grid;
	},
	get gridFlow() {
		return gridFlow;
	},
	// What the strip leads with. A request to the service, which makes the cut.
	get strip() {
		return strip;
	}
};

export function setGridOrder(next: Sort) {
	grid = next;
	keep(GRID_KEY, next, next === GRID_DEFAULT);
	// A new order arrives the right way up, as picking one on the page does.
	setGridFlow(FLOW[next]);
}

export function setGridFlow(next: Flow) {
	gridFlow = next;
	// The natural direction writes no key, so a changed order resets it.
	keep(GRID_FLOW_KEY, next, next === FLOW[grid]);
}

export function setStripOrder(next: Sort) {
	strip = next;
	keep(STRIP_KEY, next, next === STRIP_DEFAULT);
}
