// Sample a small thumbnail once, never on pointer movement. Hue buckets avoid
// averaging opposite colours into grey; chroma weighting keeps black borders
// and white lettering from overpowering the artwork.
export type PosterPalette = {
	hue: number;
	saturation: number;
	accentHue: number;
	accentSaturation: number;
};

export const NEUTRAL_PALETTE: PosterPalette = {
	hue: 0,
	saturation: 0,
	accentHue: 0,
	accentSaturation: 0
};

export function posterPalette(pixels: ArrayLike<number>): PosterPalette {
	const buckets = Array.from({ length: 24 }, () => ({ weight: 0, x: 0, y: 0, saturation: 0 }));
	for (let i = 0; i + 3 < pixels.length; i += 4) {
		const [r, g, b] = [pixels[i], pixels[i + 1], pixels[i + 2]].map((v) => v / 255);
		const high = Math.max(r, g, b);
		const low = Math.min(r, g, b);
		const chroma = high - low;
		const light = (high + low) / 2;
		if (chroma < 0.04 || light < 0.08 || light > 0.92 || pixels[i + 3] < 128) continue;
		const sector =
			high === r ? (g - b) / chroma : high === g ? (b - r) / chroma + 2 : (r - g) / chroma + 4;
		const hue = (sector * 60 + 360) % 360;
		const weight = chroma * (pixels[i + 3] / 255);
		const bucket = buckets[Math.round(hue / 15) % buckets.length];
		bucket.weight += weight;
		bucket.x += Math.cos((hue * Math.PI) / 180) * weight;
		bucket.y += Math.sin((hue * Math.PI) / 180) * weight;
		bucket.saturation += (chroma / (1 - Math.abs(2 * light - 1))) * weight;
	}
	const best = buckets.reduce((a, b) => (b.weight > a.weight ? b : a));
	if (!best.weight) return { ...NEUTRAL_PALETTE };
	const colour = (bucket: typeof best) => ({
		hue: Math.round(((Math.atan2(bucket.y, bucket.x) * 180) / Math.PI + 360) % 360) % 360,
		saturation: Math.round(Math.min(75, (bucket.saturation / bucket.weight) * 100))
	});
	const primary = colour(best);
	// A second colour must be distinct and substantial, not a stray compression
	// pixel or the neighbouring bucket of a single-colour gradient.
	const accent = buckets
		.filter((bucket) => {
			if (bucket.weight < best.weight * 0.2) return false;
			const distance = Math.abs(colour(bucket).hue - primary.hue);
			return Math.min(distance, 360 - distance) >= 45;
		})
		.reduce((a, b) => (b.weight > a.weight ? b : a), { weight: 0, x: 0, y: 0, saturation: 0 });
	const secondary = accent.weight ? colour(accent) : primary;
	return { ...primary, accentHue: secondary.hue, accentSaturation: secondary.saturation };
}

// Pending ones too, so two cards showing one cover decode it once.
const cache = new Map<string, Promise<PosterPalette>>();

// The thumbnail the sampler reads.
const THUMB = { width: 16, height: 24 };

/** A same-origin cover, read back from the HTTP cache. A blob decodes off the
 * main thread, where drawing the <img> decoded it in place. */
export function coverPalette(src: string): Promise<PosterPalette> {
	let palette = cache.get(src);
	if (!palette) {
		if (cache.size >= 512) cache.delete(cache.keys().next().value!);
		palette = sample(src);
		cache.set(src, palette);
	}
	return palette;
}

async function sample(src: string): Promise<PosterPalette> {
	try {
		const blob = await (await fetch(src)).blob();
		const bitmap = await createImageBitmap(blob, {
			resizeWidth: THUMB.width,
			resizeHeight: THUMB.height,
			resizeQuality: 'medium'
		});
		const canvas = document.createElement('canvas');
		canvas.width = THUMB.width;
		canvas.height = THUMB.height;
		const context = canvas.getContext('2d', { willReadFrequently: true });
		// Sized here too, for a browser that ignores the resize.
		context?.drawImage(bitmap, 0, 0, THUMB.width, THUMB.height);
		bitmap.close();
		if (context) return posterPalette(context.getImageData(0, 0, THUMB.width, THUMB.height).data);
	} catch {
		// Unreadable or cross-origin artwork keeps a neutral metallic finish.
	}
	return { ...NEUTRAL_PALETTE };
}
