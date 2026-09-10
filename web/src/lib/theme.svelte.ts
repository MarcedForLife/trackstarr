// This browser's palette and light or dark preference, resolved to <html
// data-palette data-theme> and seeded before first paint by app.html's inline
// script. The swap is CSS: layout.css transitions the palette tokens. The
// Custom palette has no block there; $lib/hue derives its tokens from a hue
// and they are written inline on the root.

import { paletteFromHue, TOKENS, wrapHue, type Derived } from '$lib/hue';
import { reduced } from '$lib/motion.svelte';
import { keep, stored } from '$lib/prefs';

export type ThemePreference = 'light' | 'dark' | 'system';
export type Palette = 'brass' | 'graphite' | 'fjord' | 'dusk' | 'moss' | 'clay' | 'custom';

const THEME_KEY = 'theme';
const PALETTE_KEY = 'palette';
const HUE_KEY = 'hue';
// The custom palette's two surfaces, for app.html's first paint: the inline
// script cannot derive them.
const HUE_SURFACE_KEY = 'hue-surface';
const DEFAULT_PALETTE: Palette = 'brass';
// A teal, which no named palette has.
const DEFAULT_HUE = 168;

// In the Appearance page's order. The colours are in layout.css under
// [data-palette]; app.html carries each one's surfaces for the first paint.
// Custom is last, alone on its row beside the hue slider.
export const PALETTES: { value: Palette; label: string }[] = [
	{ value: 'brass', label: 'Brass' },
	{ value: 'graphite', label: 'Graphite' },
	{ value: 'fjord', label: 'Fjord' },
	{ value: 'dusk', label: 'Dusk' },
	{ value: 'moss', label: 'Moss' },
	{ value: 'clay', label: 'Clay' },
	{ value: 'custom', label: 'Custom' }
];

// layout.css's palette transition, plus a frame so the last read lands after it.
const SWAP_MS = 700;
const SETTLE_MS = SWAP_MS + 50;

// How long after the last hue step the swap transition stays off.
const LIVE_MS = 200;

const systemDarkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function storedHue(): number {
	try {
		const found = localStorage.getItem(HUE_KEY);
		if (found !== null && /^\d{1,3}$/.test(found) && Number(found) < 360) return Number(found);
	} catch {
		/* storage blocked: the default is the right answer */
	}
	return DEFAULT_HUE;
}

let preference = $state<ThemePreference>(stored(THEME_KEY, ['light', 'dark'], 'system'));
let palette = $state<Palette>(
	stored(
		PALETTE_KEY,
		PALETTES.map((option) => option.value),
		DEFAULT_PALETTE
	)
);
let hue = $state(storedHue());
let systemDark = $state(systemDarkQuery.matches);

const resolved: 'light' | 'dark' = $derived(
	preference === 'system' ? (systemDark ? 'dark' : 'light') : preference
);

// Both themes' tokens for the hue, whichever palette is on. The picker's
// Custom swatch paints itself from these too.
const custom: Derived = $derived(paletteFromHue(hue));

export const theme = {
	get preference() {
		return preference;
	},
	get resolved() {
		return resolved;
	},
	get palette() {
		return palette;
	},
	get hue() {
		return hue;
	},
	get custom() {
		return custom;
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
	if (animated) holdControls();
	root.dataset.theme = resolved;
	root.dataset.palette = palette;
	if (palette === 'custom') {
		const tokens = custom[resolved];
		for (const name of TOKENS) root.style.setProperty(name, tokens[name]);
		keep(HUE_SURFACE_KEY, `${custom.dark['--surface']},${custom.light['--surface']}`, false);
	} else {
		for (const name of TOKENS) root.style.removeProperty(name);
		keep(HUE_SURFACE_KEY, '', true);
	}
	paintChrome(animated);
}

let liveTimer = 0;
let swapTimer = 0;

// Holds the controls' own colour transitions off for the length of a swap:
// Gecko runs one straight to the final token and flashes. The rule is in
// layout.css, under the attribute this sets.
function holdControls() {
	root.dataset.swapping = '';
	clearTimeout(swapTimer);
	swapTimer = window.setTimeout(() => delete root.dataset.swapping, SETTLE_MS);
}

// Holds layout.css's swap transition off while the hue slider moves: a 700ms
// ease behind every step read as smear. The root carries the attribute and
// the swatches under it follow.
function holdStill() {
	root.dataset.live = '';
	clearTimeout(liveTimer);
	liveTimer = window.setTimeout(() => delete root.dataset.live, LIVE_MS);
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

export function setHue(next: number) {
	const whole = wrapHue(next);
	if (whole === hue) return;

	hue = whole;
	keep(HUE_KEY, String(whole), whole === DEFAULT_HUE);
	if (palette === 'custom') {
		holdStill();
		apply(false);
	}
}

// The named palettes are on from the stylesheet; Custom needs its tokens
// written before anything paints.
apply();

// app.html painted the canvas inline for the gap before the stylesheet.
root.style.removeProperty('background');
