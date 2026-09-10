// The derived palette is held to the targets the six named ones were tuned to
// by hand (layout.css's header comment), at every hue, since a slider reaches
// all of them.

import { describe, expect, test } from 'vitest';
import { contrast, paletteFromHue, parse, spectrum, TOKENS, wrapHue } from './hue';

const HUES = Array.from({ length: 24 }, (_, index) => index * 15);
const HEX = /^#[0-9a-f]{6}$/;
const RGBA = /^rgba\(\d{1,3}, \d{1,3}, \d{1,3}, 0\.\d+\)$/;
const TRANSLUCENT = ['--line', '--line-strong', '--line-control', '--accent-soft'];

/** The worst 3:1 a control's boundary manages against any ground it touches. */
function controlEdge(tokens: Record<string, string>) {
	const grounds = ['--raised', '--field', '--surface', '--sunken'].map((name) => tokens[name]);
	return Math.min(
		...grounds.map((ground) => contrast(flatten(tokens['--line-control'], ground), ground))
	);
}

/** An `rgba()` token over an opaque ground, as the browser paints it. */
function flatten(translucent: string, ground: string) {
	const [red, green, blue, alpha] = translucent.slice(5, -1).split(',').map(Number);
	return parse(ground).map((base, index) => {
		const front = [red, green, blue][index] / 255;
		return front * alpha + base * (1 - alpha);
	}) as [number, number, number];
}

describe('a palette from a hue', () => {
	test('writes every token layout.css does, in its order', () => {
		for (const hue of HUES)
			for (const theme of ['light', 'dark'] as const)
				expect(Object.keys(paletteFromHue(hue)[theme])).toEqual([...TOKENS]);
	});

	test('writes hex, and rgba only where the stylesheet does', () => {
		for (const hue of HUES)
			for (const theme of ['light', 'dark'] as const)
				for (const [name, value] of Object.entries(paletteFromHue(hue)[theme]))
					expect([hue, theme, name, value]).toEqual([
						hue,
						theme,
						name,
						expect.stringMatching(TRANSLUCENT.includes(name) ? RGBA : HEX)
					]);
	});

	/** The ratios in `actual` that fall under their floor in `floors`. */
	function under(actual: Record<string, number>, floors: Record<string, number>) {
		return Object.entries(actual)
			.filter(([name, ratio]) => ratio < floors[name])
			.map(([name, ratio]) => `${name} ${ratio.toFixed(2)} < ${floors[name]}`);
	}

	test('meets the light targets at every hue', () => {
		const floors = {
			fg: 12,
			dim: 6.2,
			faint: 3.5,
			ink: 5.15,
			inkOnPill: 4.5,
			fill: 2.95,
			control: 2.95,
			onAccent: 4.5,
			ok: 4.4,
			onOk: 4.4,
			danger: 4.4,
			toggle: 2.95,
			lift: 1.08
		};
		for (const hue of HUES) {
			const tokens = paletteFromHue(hue).light;
			const page = tokens['--surface'];
			const ratios = {
				fg: contrast(tokens['--fg'], page),
				dim: contrast(tokens['--dim'], page),
				faint: contrast(tokens['--faint'], page),
				ink: contrast(tokens['--accent'], page),
				// Ink on an --accent-soft pill, which the active tab and nav item sit on.
				inkOnPill: contrast(tokens['--accent'], flatten(tokens['--accent-soft'], page)),
				// The page, not the white cards, where the ring and the bars land.
				fill: contrast(tokens['--accent-fill'], page),
				control: controlEdge(tokens),
				onAccent: contrast(tokens['--on-accent'], tokens['--accent-fill']),
				ok: contrast(tokens['--ok'], page),
				onOk: contrast(tokens['--on-ok'], tokens['--ok']),
				danger: contrast(tokens['--danger'], page),
				toggle: contrast(tokens['--toggle-off'], page),
				lift: contrast(tokens['--raised'], page)
			};
			expect([hue, under(ratios, floors)]).toEqual([hue, []]);
		}
	});

	test('meets the dark targets at every hue', () => {
		const floors = {
			fg: 12,
			dim: 6.8,
			faint: 3.4,
			accent: 8.3,
			control: 2.95,
			onAccent: 4.5,
			ok: 7,
			onOk: 4.5,
			danger: 5,
			lift: 1.08
		};
		for (const hue of HUES) {
			const tokens = paletteFromHue(hue).dark;
			const page = tokens['--surface'];
			const ratios = {
				fg: contrast(tokens['--fg'], page),
				dim: contrast(tokens['--dim'], page),
				faint: contrast(tokens['--faint'], page),
				accent: contrast(tokens['--accent'], page),
				control: controlEdge(tokens),
				onAccent: contrast(tokens['--on-accent'], tokens['--accent']),
				ok: contrast(tokens['--ok'], page),
				onOk: contrast(tokens['--on-ok'], tokens['--ok']),
				danger: contrast(tokens['--danger'], page),
				lift: contrast(tokens['--raised'], page)
			};
			expect([hue, under(ratios, floors)]).toEqual([hue, []]);
		}
	});

	test('sets both accents the same in the dark, as the named palettes do', () => {
		for (const hue of HUES) {
			const tokens = paletteFromHue(hue).dark;
			expect(tokens['--accent-fill']).toBe(tokens['--accent']);
		}
	});

	// A green accent next to the green ok, or a red one next to danger, would
	// be one colour saying two things.
	test('moves ok and danger off a hue that lands on them', () => {
		const away = paletteFromHue(250).light;
		expect(paletteFromHue(60).light['--ok']).toBe(away['--ok']);
		expect(paletteFromHue(150).light['--ok']).not.toBe(away['--ok']);
		expect(paletteFromHue(250).light['--danger']).toBe(paletteFromHue(120).light['--danger']);
		expect(paletteFromHue(20).light['--danger']).not.toBe(away['--danger']);
	});

	test('wraps any number to a whole hue', () => {
		expect([wrapHue(-10), wrapHue(360), wrapHue(725), wrapHue(359.6), wrapHue(NaN)]).toEqual([
			350, 0, 5, 0, 0
		]);
	});

	test('draws the slider track as one stop a step, closing the wheel', () => {
		const stops = spectrum('light');
		expect(stops).toHaveLength(13);
		expect(stops[0]).toBe(stops[12]);
		expect(new Set(stops).size).toBe(12);
	});
});
