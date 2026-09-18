// A finger dragging the overview strip must not twitch the lean: the finger
// and the scroll it drives arrive as separate samples, so the field holds the
// pointer on the content while a drag runs.
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const { site, close } = await serve(5198, await fixtures());
try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch({ headless: true });
		try {
			const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
			await page.goto(`${site}/`);
			const strip = page.locator('ul.strip');
			await strip.waitFor();
			// Room to drag the content rightward under the finger.
			await strip.evaluate((el) => (el.scrollLeft = 120));
			await page.waitForTimeout(300);
			const tile = strip.locator('li').nth(2);
			const card = tile.locator('[data-tilt]');
			await card.waitFor();

			const touch = async (type, shift) => {
				await tile.evaluate(
					(el, { type, shift }) => {
						const box = el.getBoundingClientRect();
						const event = new Event(type, { bubbles: true });
						Object.defineProperty(event, 'touches', {
							value:
								shift === null
									? []
									: [
											{
												clientX: box.left + box.width * 0.7 + shift,
												clientY: box.top + box.height * 0.4
											}
										]
						});
						el.dispatchEvent(event);
					},
					{ type, shift }
				);
			};
			const lean = () =>
				card.evaluate((el) => ({
					ry: parseFloat(el.style.getPropertyValue('--ry')),
					rx: parseFloat(el.style.getPropertyValue('--rx'))
				}));
			await touch('touchstart', 0);
			await page.waitForTimeout(300);
			const held = await lean();
			assert(Math.abs(held.ry) > 0.5, `${engine}: the touched card leans (${held.ry})`);

			// The drag begins.
			await strip.evaluate((el) => (el.scrollLeft -= 2));
			await page.waitForTimeout(50);
			// The finger's sample a frame before the scroll's.
			await touch('touchmove', 40);
			await page.waitForTimeout(40);
			const ahead = await lean();
			await strip.evaluate((el) => (el.scrollLeft -= 40));
			await page.waitForTimeout(50);
			const caught = await lean();
			const close = (a, b) => Math.abs(a - b) < 0.05;
			assert(
				close(ahead.ry, held.ry) && close(ahead.rx, held.rx),
				`${engine}: lean holds with the finger sampled ahead of the scroll (${held.ry.toFixed(2)} → ${ahead.ry.toFixed(2)})`
			);
			assert(
				close(caught.ry, held.ry),
				`${engine}: lean holds once the content catches up (${held.ry.toFixed(2)} → ${caught.ry.toFixed(2)})`
			);
			await touch('touchend', null);
			console.log(
				`${engine}: a dragged strip keeps its lean (${held.ry.toFixed(2)}deg throughout)`
			);
		} finally {
			await browser.close();
		}
	}
} finally {
	close();
}
