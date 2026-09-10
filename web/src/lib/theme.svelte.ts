// This browser's palette and light or dark preference, resolved to <html
// data-palette data-theme> and seeded before first paint by app.html's inline
// script. The swap is CSS: layout.css transitions the palette tokens.

import { reduced } from '$lib/motion.svelte';
import { keep, stored } from '$lib/prefs';

export type ThemePreference = 'light' | 'dark' | 'system';
export type Palette = 'brass' | 'graphite' | 'fjord' | 'dusk' | 'moss' | 'clay';

const THEME_KEY = 'theme';
const PALETTE_KEY = 'palette';
const DEFAULT_PALETTE: Palette = 'brass';

// In the Appearance page's order. The colours are in layout.css under
// [data-palette]; app.html carries each one's surfaces for the first paint.
export const PALETTES: { value: Palette; label: string }[] = [
	{ value: 'brass', label: 'Brass' },
	{ value: 'graphite', label: 'Graphite' },
	{ value: 'fjord', label: 'Fjord' },
	{ value: 'dusk', label: 'Dusk' },
	{ value: 'moss', label: 'Moss' },
	{ value: 'clay', label: 'Clay' }
];

// layout.css's palette transition, plus a frame so the last read lands after it.
const SWAP_MS = 700;
const SETTLE_MS = SWAP_MS + 50;

const systemDarkQuery = window.matchMedia('(prefers-color-scheme: dark)');

let preference = $state<ThemePreference>(stored(THEME_KEY, ['light', 'dark'], 'system'));
let palette = $state<Palette>(
	stored(
		PALETTE_KEY,
		PALETTES.map((option) => option.value),
		DEFAULT_PALETTE
	)
);
let systemDark = $state(systemDarkQuery.matches);

const resolved: 'light' | 'dark' = $derived(
	preference === 'system' ? (systemDark ? 'dark' : 'light') : preference
);

export const theme = {
	get preference() {
		return preference;
	},
	get resolved() {
		return resolved;
	},
	get palette() {
		return palette;
	}
};

const root = document.documentElement;
const chromeMeta = document.querySelector('meta[name="theme-color"]');
let chromeFrame = 0;

// Mobile browser chrome is a meta tag no stylesheet can transition, so it
// follows --surface by hand each frame.
function paintChrome(animated: boolean) {
	if (!chromeMeta) return;
	cancelAnimationFrame(chromeFrame);

	const copy = () =>
		chromeMeta.setAttribute('content', getComputedStyle(root).getPropertyValue('--surface').trim());

	// Two frames at least: the first read starts the transition and reports its
	// starting value.
	const start = performance.now();
	let frames = 0;
	const step = (now: number) => {
		copy();
		frames += 1;
		if (frames < 2 || (animated && now - start < SETTLE_MS)) {
			chromeFrame = requestAnimationFrame(step);
		}
	};
	chromeFrame = requestAnimationFrame(step);
}

function apply(animated = false) {
	root.dataset.theme = resolved;
	root.dataset.palette = palette;
	paintChrome(animated);
}

const motionOk = () => !reduced();

systemDarkQuery.addEventListener('change', (event) => {
	systemDark = event.matches;
	apply(motionOk());
});

export function setTheme(next: ThemePreference) {
	const changes = (next === 'system' ? (systemDark ? 'dark' : 'light') : next) !== resolved;

	preference = next;
	keep(THEME_KEY, next, next === 'system');
	apply(changes && motionOk());
}

export function setPalette(next: Palette) {
	const changes = next !== palette;

	palette = next;
	keep(PALETTE_KEY, next, next === DEFAULT_PALETTE);
	apply(changes && motionOk());
}

// app.html painted the canvas inline for the gap before the stylesheet.
root.style.removeProperty('background');
