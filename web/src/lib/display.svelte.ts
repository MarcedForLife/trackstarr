// What the library grid spends on looking good, and what it opens showing.
// Per browser, like the theme: how this device shows the app, not a service
// setting.

import { FILTERS, MISSING, UNSUPPORTED, type Verdict } from '$lib/library';
import { keep, keepAll, stored, storedAll } from '$lib/prefs';

// How loud the poster effects are. One axis rather than a switch: "steadier on
// an old phone" and "as much as it will give" are the same question.
export type Effects = 'off' | 'subtle' | 'balanced' | 'lively' | 'balatro';
export type Art = 'show' | 'hide';
// Whether a verdict is held back from the default view. Its own chip still
// reaches it, so nothing is hidden for good.
export type Shown = 'show' | 'hide';
export type Spread = 'narrow' | 'medium' | 'wide';
export type Scale = 'small' | 'medium' | 'large';
// What a turning card catches, in cost order: each step up to `foil` is another
// blended layer, and a blend mode is a composited layer. `holo` is `foil` with a
// busier gradient.
export type Sheen = 'none' | 'gloss' | 'foil' | 'holo';

// The slider's stops, weakest first; the order is part of the meaning.
export const EFFECTS: { value: Effects; label: string }[] = [
	{ value: 'off', label: 'Off' },
	{ value: 'subtle', label: 'Subtle' },
	{ value: 'balanced', label: 'Balanced' },
	{ value: 'lively', label: 'Lively' },
	// A nod to the card game whose cards will not sit still.
	{ value: 'balatro', label: 'Balatro' }
];

// What each stop multiplies the lean, lift, shadow and sheen by. Balanced is 1,
// where the effect was tuned.
const STRENGTHS: Record<Effects, number> = {
	off: 0,
	subtle: 0.5,
	balanced: 1,
	lively: 1.6,
	balatro: 2.4
};

const EFFECTS_KEY = 'poster-effects';
const ART_KEY = 'cover-art';
const SPREAD_KEY = 'tilt-spread';
const SCALE_KEY = 'poster-scale';
const SHEEN_KEY = 'poster-sheen';
const MISSING_KEY = 'library-missing';
const UNSUPPORTED_KEY = 'library-unsupported';
const FILTERS_KEY = 'library-filters';

// Poster size as a multiple of the default. The grid fits as many columns as
// it can, so a wider screen still means more posters at every setting.
const SCALES: Record<Scale, number> = {
	small: 0.72,
	medium: 1,
	large: 1.5
};

// How far the tilt carries past the pointed-at card, in card widths. Taste
// rather than cost: wide is a dozen writes a frame instead of half.
const SPREADS: Record<Spread, number> = {
	narrow: 1.1,
	medium: 1.6,
	wide: 2.4
};

let effects = $state<Effects>(
	stored(
		EFFECTS_KEY,
		EFFECTS.map((step) => step.value),
		'balanced'
	)
);
let art = $state<Art>(stored(ART_KEY, ['show', 'hide'], 'show'));
let spread = $state<Spread>(stored(SPREAD_KEY, ['narrow', 'medium', 'wide'], 'medium'));
let scale = $state<Scale>(stored(SCALE_KEY, ['small', 'medium', 'large'], 'medium'));
let sheen = $state<Sheen>(stored(SHEEN_KEY, ['none', 'gloss', 'foil', 'holo'], 'gloss'));
// Hidden by default: a page led by undownloaded films is a wishlist.
let missing = $state<Shown>(stored(MISSING_KEY, ['show', 'hide'], 'hide'));
// Shown by default: real files nobody has seen yet, to hide once they have
// looked.
let unsupported = $state<Shown>(stored(UNSUPPORTED_KEY, ['show', 'hide'], 'show'));
// Which chips the grid opens held; none is All. A per-browser habit. Read
// against the chip row, not the verdicts, or the two chips that are no verdict
// press on Appearance and never take.
let filters = $state<Verdict[]>(storedAll(FILTERS_KEY, FILTERS));

export const display = {
	get effects() {
		return effects;
	},
	// The stop as a multiplier, 0 for a still grid. Every part of the effect
	// reads this one number.
	get strength() {
		return STRENGTHS[effects];
	},
	get art() {
		return art;
	},
	get spread() {
		return spread;
	},
	// The spread in card widths.
	get reach() {
		return SPREADS[spread];
	},
	get scale() {
		return scale;
	},
	get sheen() {
		return sheen;
	},
	get missing() {
		return missing;
	},
	get unsupported() {
		return unsupported;
	},
	// The verdicts the grid opens without, derived once so the All count, the
	// filter and the hint agree.
	get hidden(): Verdict[] {
		return [
			...(missing === 'hide' ? [MISSING] : []),
			...(unsupported === 'hide' ? [UNSUPPORTED] : [])
		];
	},
	// A copy: the grid seeds its live filter row from this and changes it.
	get filters() {
		return [...filters];
	},
	// Whether a pointed-at card builds its blended layers at all; which ones is
	// the stylesheet's.
	get lights() {
		return sheen !== 'none';
	},
	// The size as a multiplier on the tile width.
	get tile() {
		return SCALES[scale];
	}
};

export function setEffects(next: Effects) {
	effects = next;
	keep(EFFECTS_KEY, next, next === 'balanced');
}

export function setArt(next: Art) {
	art = next;
	keep(ART_KEY, next, next === 'show');
}

export function setSpread(next: Spread) {
	spread = next;
	keep(SPREAD_KEY, next, next === 'medium');
}

export function setScale(next: Scale) {
	scale = next;
	keep(SCALE_KEY, next, next === 'medium');
}

export function setSheen(next: Sheen) {
	sheen = next;
	keep(SHEEN_KEY, next, next === 'gloss');
}

export function setMissing(next: Shown) {
	missing = next;
	keep(MISSING_KEY, next, next === 'hide');
}

export function setUnsupported(next: Shown) {
	unsupported = next;
	keep(UNSUPPORTED_KEY, next, next === 'show');
}

export function setFilters(next: Verdict[]) {
	// In vocabulary order, not press order, so both chip rows agree.
	filters = FILTERS.filter((state) => next.includes(state));
	keepAll(FILTERS_KEY, filters);
}
