import { describe, expect, it } from 'vitest';
import { posterPalette, NEUTRAL_PALETTE } from './poster-palette';

const pixel = (r: number, g: number, b: number, a = 255) => [r, g, b, a];

describe('posterPalette', () => {
	it('keeps monochrome, empty and transparent artwork neutral', () => {
		for (const pixels of [
			[],
			pixel(0, 0, 0),
			pixel(128, 128, 128),
			pixel(255, 255, 255),
			pixel(255, 0, 0, 0)
		]) {
			expect(posterPalette(pixels)).toEqual(NEUTRAL_PALETTE);
		}
	});

	it('finds artwork colour despite black borders and white lettering', () => {
		const pixels = [
			...Array(20)
				.fill(pixel(0, 0, 0))
				.flat(),
			...Array(20)
				.fill(pixel(255, 255, 255))
				.flat(),
			...pixel(0, 150, 150)
		];
		expect(posterPalette(pixels)).toMatchObject({ hue: 180, saturation: 75 });
	});

	it('keeps the dominant hue instead of averaging complementary colours to grey', () => {
		const palette = posterPalette([
			...pixel(200, 0, 0),
			...pixel(200, 0, 0),
			...pixel(0, 200, 200)
		]);
		expect(palette).toMatchObject({ hue: 0, saturation: 75 });
	});

	it('averages reds across the hue boundary without turning cyan', () => {
		const palette = posterPalette([...pixel(220, 10, 15), ...pixel(220, 15, 10)]);
		expect(palette.hue % 360).toBe(0);
	});

	it('preserves the low saturation of muted artwork', () => {
		const palette = posterPalette(pixel(115, 125, 135));
		expect(palette.hue).toBe(210);
		expect(palette.saturation).toBeLessThan(15);
	});
});

describe('secondary poster colour', () => {
	it('uses a substantial contrasting colour for the accent', () => {
		expect(
			posterPalette([...pixel(200, 0, 0), ...pixel(200, 0, 0), ...pixel(0, 200, 200)])
		).toMatchObject({ hue: 0, accentHue: 180, accentSaturation: 75 });
	});
	it('ignores isolated colour noise', () => {
		const palette = posterPalette([
			...Array(20)
				.fill(pixel(200, 0, 0))
				.flat(),
			...pixel(0, 200, 200)
		]);
		expect(palette.accentHue).toBe(palette.hue);
	});
	it('does not mistake adjacent warm hues for a contrasting accent', () => {
		const palette = posterPalette([...pixel(200, 0, 0), ...pixel(200, 50, 0)]);
		expect(palette.accentHue).toBe(palette.hue);
		expect(palette.accentSaturation).toBe(palette.saturation);
	});
	it('treats both sides of the red boundary as neighbouring hues', () => {
		const palette = posterPalette([...pixel(200, 0, 35), ...pixel(200, 35, 0)]);
		expect(palette.accentHue).toBe(palette.hue);
	});
});
