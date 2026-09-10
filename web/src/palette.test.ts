// The palette tokens are written out three times: once as the @property
// initial values, once as the six [data-palette] blocks, and once as the
// surface pairs app.html paints with before the stylesheet arrives. Nothing
// in the build checks them against each other, and dev never shows a miss
// because Vite inlines the CSS early enough to cover it.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, test } from 'vitest';
import { TOKENS } from '$lib/hue';

const read = (name: string) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8');
const css = read('./routes/layout.css');
const shell = read('./app.html');

/** Every `--token: value;` in one block of declarations. */
function tokens(block: string): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [, name, value] of block.matchAll(/(--[\w-]+):\s*([^;]+);/g)) {
		out[name] = value.trim().replace(/\s+/g, ' ');
	}
	return out;
}

/** The tokens each `[data-palette][data-theme]` pair sets, keyed "brass dark". */
const palettes = new Map<string, Record<string, string>>(
	[...css.matchAll(/\[data-palette='(\w+)'\]\[data-theme='(dark|light)'\]\s*\{([^}]*)\}/g)].map(
		([, palette, theme, block]) => [`${palette} ${theme}`, tokens(block)]
	)
);

/** What a colour token starts at, before any palette is on the element. */
const initial = new Map<string, string>(
	[...css.matchAll(/@property\s+(--[\w-]+)\s*\{([^}]*)\}/g)]
		.filter(([, , block]) => block.includes("syntax: '<color>'"))
		.map(([, name, block]) => [
			name,
			(block.match(/initial-value:\s*([^;]+);/)?.[1] ?? '').trim().replace(/\s+/g, ' ')
		])
);

const DEFAULT = 'brass dark';

describe('the palettes layout.css writes', () => {
	test('are the six the appearance page offers, in light and dark', () => {
		expect([...palettes.keys()]).toEqual([
			'brass dark',
			'brass light',
			'graphite dark',
			'graphite light',
			'fjord dark',
			'fjord light',
			'dusk dark',
			'dusk light',
			'moss dark',
			'moss light',
			'clay dark',
			'clay light'
		]);
	});

	test('each set every token the others do', () => {
		const expected = Object.keys(palettes.get(DEFAULT) ?? {});
		expect(expected.length).toBeGreaterThan(0);
		for (const [name, block] of palettes)
			expect([name, Object.keys(block)]).toEqual([name, expected]);
	});

	// An element reading a token before a palette reaches it — the swatches on
	// the appearance page paint themselves, and the transition interpolates
	// from wherever it starts — gets the initial value, so a stale one is a
	// wrong colour for one frame of the swap.
	test('start at the default palette', () => {
		expect(Object.fromEntries(initial)).toEqual(palettes.get(DEFAULT));
	});

	// The Custom palette writes the same set inline, so a token added here
	// without a derivation would be missing from it.
	test('are the tokens $lib/hue derives', () => {
		expect(Object.keys(palettes.get(DEFAULT) ?? {})).toEqual([...TOKENS]);
	});

	// Registering them is also what lets the swap animate: an unregistered
	// custom property is a string and flips on the spot.
	test('are all registered as colours', () => {
		expect([...initial.keys()].sort()).toEqual(Object.keys(palettes.get(DEFAULT) ?? {}).sort());
	});
});

describe("app.html's pre-paint script", () => {
	const surfaces = new Map(
		[...shell.matchAll(/(\w+): \['(#[0-9a-f]{6})', '(#[0-9a-f]{6})'\]/g)].map(
			([, palette, dark, light]) => [palette, { dark, light }]
		)
	);

	test('carries a dark and a light surface for every palette', () => {
		const named = [...palettes.keys()].map((key) => key.split(' ')[0]);
		expect([...surfaces.keys()]).toEqual([...new Set(named)]);
	});

	// The document paints this before the stylesheet loads. A hex that has
	// drifted is a flash of the last palette on every cold visit.
	test('paints the surface the stylesheet is about to', () => {
		for (const [palette, pair] of surfaces) {
			expect([palette, pair.dark]).toEqual([
				palette,
				palettes.get(`${palette} dark`)?.['--surface']
			]);
			expect([palette, pair.light]).toEqual([
				palette,
				palettes.get(`${palette} light`)?.['--surface']
			]);
		}
	});

	// The browser chrome the shell ships with, before the script repaints it
	// to whatever was chosen last.
	test('ships a theme-color matching the default', () => {
		const meta = shell.match(/<meta name="theme-color" content="(#[0-9a-f]{6})"/)?.[1];
		expect(meta).toBe(palettes.get(DEFAULT)?.['--surface']);
	});
});
