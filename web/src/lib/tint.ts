import { display } from '$lib/display.svelte';
import { coverUrl } from '$lib/library';
import { coverPalette } from '$lib/poster-palette';

// Saturation below which a cover counts as grey and gets no tint.
const GREY = 10;

/** Tints a row from its cover's colour through `--tint`, which layout.css
 * draws. */
export function tint(node: HTMLElement, src: string | undefined) {
	node.dataset.tint = '';
	let asked = '';
	function paint(next: string | undefined) {
		asked = next ?? '';
		if (!next) {
			node.style.removeProperty('--tint');
			return;
		}
		void coverPalette(next).then((found) => {
			if (asked !== next) return;
			if (found.saturation < GREY) node.style.removeProperty('--tint');
			else node.style.setProperty('--tint', `hsl(${found.hue} ${found.saturation}% 50%)`);
		});
	}
	paint(src);
	return { update: paint };
}

/** The cover to tint from, none while art is hidden. Call it reactively so
 * hiding art clears the tint. */
export function tintOf(id: string | undefined): string | undefined {
	return id && display.art !== 'hide' ? coverUrl(id) : undefined;
}
