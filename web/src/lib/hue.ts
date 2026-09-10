// A palette from one hue: the Custom option on the Appearance page. The six
// named palettes in layout.css were tuned by hand to shared contrast targets;
// this derives the same tokens for any hue and solves each tone's lightness
// to the target instead of guessing. Colours are built in OKLCH, where a tint
// of a given chroma reads as strong on a blue as on a yellow, then written as
// sRGB hex. Pure, so it runs in node; $lib/theme.svelte.ts applies the result.

export const TOKENS = [
	'--surface',
	'--sunken',
	'--raised',
	'--field',
	'--line',
	'--line-strong',
	'--line-control',
	'--fg',
	'--dim',
	'--faint',
	'--accent',
	'--accent-fill',
	'--on-accent',
	'--accent-soft',
	'--ok',
	'--on-ok',
	'--danger',
	'--toggle-off',
	'--knob',
	'--knob-on'
] as const;

export type Token = (typeof TOKENS)[number];
export type Tokens = Record<Token, string>;
export type Theme = 'light' | 'dark';
export type Derived = Record<Theme, Tokens>;

/** Red, green and blue in 0..1, gamma-encoded sRGB. */
type Rgb = [number, number, number];

const WHITE: Rgb = [1, 1, 1];

/** Any number to a whole hue in 0..359. */
export function wrapHue(value: number): number {
	const whole = Math.round(Number.isFinite(value) ? value : 0);
	return ((whole % 360) + 360) % 360;
}

/** How far apart two hues are around the wheel. */
function apart(a: number, b: number): number {
	const delta = Math.abs(a - b) % 360;
	return delta > 180 ? 360 - delta : delta;
}

function encode(linear: number): number {
	return linear <= 0.0031308 ? 12.92 * linear : 1.055 * Math.pow(linear, 1 / 2.4) - 0.055;
}

function decode(channel: number): number {
	return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
}

/** OKLCH to linear sRGB, which may leave 0..1 for a chroma the gamut lacks. */
function linear(lightness: number, chroma: number, hue: number): Rgb {
	const radians = (hue * Math.PI) / 180;
	const a = chroma * Math.cos(radians);
	const b = chroma * Math.sin(radians);
	const long = Math.pow(lightness + 0.3963377774 * a + 0.2158037573 * b, 3);
	const medium = Math.pow(lightness - 0.1055613458 * a - 0.0638541728 * b, 3);
	const short = Math.pow(lightness - 0.0894841775 * a - 1.291485548 * b, 3);
	return [
		4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short,
		-1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short,
		-0.0041960863 * long - 0.7034186147 * medium + 1.707614701 * short
	];
}

function inGamut(channels: Rgb): boolean {
	return channels.every((channel) => channel >= -0.0005 && channel <= 1.0005);
}

/** The colour in sRGB, its chroma pulled in until the gamut holds it. */
function rgb(lightness: number, chroma: number, hue: number): Rgb {
	let fits = 0;
	let spills = chroma;
	let channels = linear(lightness, chroma, hue);
	if (!inGamut(channels)) {
		for (let step = 0; step < 24; step++) {
			const middle = (fits + spills) / 2;
			channels = linear(lightness, middle, hue);
			if (inGamut(channels)) fits = middle;
			else spills = middle;
		}
		channels = linear(lightness, fits, hue);
	}
	return channels.map((channel) => Math.min(1, Math.max(0, encode(channel)))) as Rgb;
}

function hex(channels: Rgb): string {
	return `#${channels
		.map((channel) =>
			Math.round(channel * 255)
				.toString(16)
				.padStart(2, '0')
		)
		.join('')}`;
}

function rgba(channels: Rgb, alpha: number): string {
	const [red, green, blue] = channels.map((channel) => Math.round(channel * 255));
	return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

/** WCAG relative luminance. */
function luminance([red, green, blue]: Rgb): number {
	return 0.2126 * decode(red) + 0.7152 * decode(green) + 0.0722 * decode(blue);
}

/** WCAG contrast ratio, 1 to 21. */
export function contrast(a: Rgb | string, b: Rgb | string): number {
	const first = luminance(typeof a === 'string' ? parse(a) : a);
	const second = luminance(typeof b === 'string' ? parse(b) : b);
	const [high, low] = first > second ? [first, second] : [second, first];
	return (high + 0.05) / (low + 0.05);
}

/** `#rrggbb` to channels. */
export function parse(color: string): Rgb {
	const digits = color.replace('#', '');
	return [0, 2, 4].map((at) => parseInt(digits.slice(at, at + 2), 16) / 255) as Rgb;
}

/**
 * The lightness at which a colour of this chroma and hue meets `target`
 * against `ground`, by bisection. `lighter` says which side of the ground
 * the colour sits: text on a dark page gets lighter as it gains contrast,
 * text on a light page darker. The side that clears the target is returned,
 * so the result is never a hair under it.
 */
function solve(chroma: number, hue: number, target: number, ground: Rgb, lighter: boolean): Rgb {
	let low = 0;
	let high = 1;
	for (let step = 0; step < 32; step++) {
		const middle = (low + high) / 2;
		const ratio = contrast(rgb(middle, chroma, hue), ground);
		if (ratio > target === lighter) high = middle;
		else low = middle;
	}
	return rgb(lighter ? high : low, chroma, hue);
}

// The state colours keep their own hues, green for ok and red for danger,
// unless the palette's hue sits on one of them: then, as Moss and Clay do by
// hand, the state colour moves 40 degrees to whichever side puts it further
// from the accent, ok staying within the greens and danger within the reds,
// so the two stay two colours.
const OK_HUE = 152;
const OK_SPAN: [number, number] = [120, 185];
const DANGER_HUE = 26;
const DANGER_ASIDE: [number, number] = [40, 8];
const CLASH = 45;
const ASIDE = 40;

function stateHues(hue: number): { ok: number; danger: number } {
	let ok = OK_HUE;
	if (apart(hue, OK_HUE) < CLASH)
		ok = hue < OK_HUE ? Math.min(hue + ASIDE, OK_SPAN[1]) : Math.max(hue - ASIDE, OK_SPAN[0]);
	let danger = DANGER_HUE;
	if (apart(hue, DANGER_HUE) < CLASH) danger = hue < DANGER_HUE ? DANGER_ASIDE[0] : DANGER_ASIDE[1];
	return { ok, danger };
}

// Lightness, chroma and contrast targets are read off the six named palettes
// in layout.css (see its header comment for the targets). Tints of the page
// take the accent's hue, as Fjord, Moss and Clay do.
function light(hue: number): Tokens {
	const surface = rgb(0.96, 0.009, hue);
	const sunken = rgb(0.93, 0.013, hue);
	// The page, not the white cards, since the focus ring and the bars land there.
	const fill = solve(0.15, hue, 3.0, surface, false);
	const lineBase = rgb(0.3, 0.04, hue);
	const { ok, danger } = stateHues(hue);
	return {
		'--surface': hex(surface),
		'--sunken': hex(sunken),
		'--raised': hex(WHITE),
		'--field': hex(WHITE),
		'--line': rgba(lineBase, 0.1),
		'--line-strong': rgba(lineBase, 0.2),
		'--line-control': rgba(lineBase, 0.53),
		'--fg': hex(rgb(0.235, 0.015, hue)),
		'--dim': hex(solve(0.018, hue, 6.3, surface, false)),
		'--faint': hex(solve(0.025, hue, 3.6, surface, false)),
		// 5.4 on the page leaves 4.5 under an --accent-soft pill.
		'--accent': hex(solve(0.12, hue, 5.4, surface, false)),
		'--accent-fill': hex(fill),
		'--on-accent': hex(rgb(0.23, 0.045, hue)),
		'--accent-soft': rgba(fill, 0.16),
		'--ok': hex(rgb(0.525, 0.115, ok)),
		'--on-ok': hex(rgb(0.985, 0.014, ok)),
		'--danger': hex(rgb(0.545, 0.175, danger)),
		'--toggle-off': hex(solve(0.028, hue, 3.0, surface, false)),
		'--knob': hex(WHITE),
		'--knob-on': hex(WHITE)
	};
}

function dark(hue: number): Tokens {
	const surface = rgb(0.19, 0.01, hue);
	const accent = solve(0.11, hue, 8.5, surface, true);
	const lineBase = rgb(0.9, 0.03, hue);
	const knob = rgb(0.97, 0.004, hue);
	const { ok, danger } = stateHues(hue);
	return {
		'--surface': hex(surface),
		'--sunken': hex(rgb(0.165, 0.007, hue)),
		'--raised': hex(rgb(0.235, 0.012, hue)),
		'--field': hex(rgb(0.225, 0.012, hue)),
		'--line': rgba(lineBase, 0.07),
		'--line-strong': rgba(lineBase, 0.13),
		'--line-control': rgba(lineBase, 0.46),
		'--fg': hex(rgb(0.935, 0.008, hue)),
		'--dim': hex(solve(0.018, hue, 6.9, surface, true)),
		'--faint': hex(solve(0.018, hue, 3.5, surface, true)),
		'--accent': hex(accent),
		'--accent-fill': hex(accent),
		'--on-accent': hex(rgb(0.23, 0.04, hue)),
		'--accent-soft': rgba(accent, 0.13),
		'--ok': hex(rgb(0.74, 0.11, ok)),
		'--on-ok': hex(rgb(0.24, 0.04, ok)),
		'--danger': hex(rgb(0.695, 0.15, danger)),
		'--toggle-off': hex(rgb(0.3, 0.014, hue)),
		'--knob': hex(knob),
		'--knob-on': hex(knob)
	};
}

/** Both themes' tokens for a hue. */
export function paletteFromHue(hue: number): Derived {
	const whole = wrapHue(hue);
	return { light: light(whole), dark: dark(whole) };
}

/** The accent fill around the wheel, for the hue slider's track. */
export function spectrum(theme: Theme, stops = 12): string[] {
	return Array.from({ length: stops + 1 }, (_, index) => {
		const hue = (index * 360) / stops;
		return paletteFromHue(hue)[theme]['--accent-fill'];
	});
}

/** The tokens as one inline style, for an element painting itself. */
export function inlineTokens(tokens: Tokens): string {
	return TOKENS.map((name) => `${name}: ${tokens[name]}`).join('; ');
}
