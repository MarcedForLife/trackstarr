// A touch scroll must pause lighting only while scrolling, not until release.
// Uses the actual built field and DOM scrolling; synthetic touches keep the
// finger down deterministically in both engines (without OS gesture timing).
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const { site, close } = await serve(5198, await fixtures());
try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch({ headless: true });
		try {
			const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
			await page.addInitScript(() => localStorage.setItem('poster-sheen', 'foil'));
			await page.goto(`${site}/library`);
			const card = page.locator('main ul.shelf [data-sheen]').first();
			await card.waitFor();
			await card.scrollIntoViewIfNeeded();
			await page.waitForTimeout(300);

			const touch = async (type, fraction) => {
				await card.evaluate(
					(el, { type, fraction }) => {
						// The field measures the untransformed tile, not the turned art.
						const box = el.closest('li').getBoundingClientRect();
						const event = new Event(type, { bubbles: true });
						Object.defineProperty(event, 'touches', {
							value:
								fraction === null
									? []
									: [
											{
												clientX: box.left + box.width * fraction,
												clientY: box.top + box.height * 0.35
											}
										]
						});
						el.dispatchEvent(event);
					},
					{ type, fraction }
				);
			};
			const light = () =>
				card.evaluate((el) => ({
					x: parseFloat(el.style.getPropertyValue('--mx')),
					sheen: parseFloat(el.style.getPropertyValue('--sheen'))
				}));
			await touch('touchstart', 0.2);
			await page.waitForTimeout(350);
			const before = await light();
			assert(before.sheen > 0, `${engine}: initial touch lights the card`);

			await card.evaluate((el) => {
				let scroller = el.parentElement;
				while (
					scroller &&
					!(
						scroller.scrollHeight > scroller.clientHeight &&
						/auto|scroll/.test(getComputedStyle(scroller).overflowY)
					)
				)
					scroller = scroller.parentElement;
				(scroller ?? document.scrollingElement).scrollTop += 30;
			});
			await page.waitForTimeout(40);
			await touch('touchmove', 0.8);
			await page.waitForTimeout(450);
			const resumed = await light();
			assert(
				resumed.x > before.x + 30,
				`${engine}: light follows the same finger after scroll stops (${before.x} → ${resumed.x})`
			);
			assert(resumed.sheen > 0, `${engine}: finish resumes without lifting the finger`);
			await touch('touchmove', 0.35);
			await page.waitForTimeout(150);
			assert(
				(await light()).x < resumed.x - 20,
				`${engine}: continued movement updates the finish`
			);

			// Releasing during the scroll debounce must not resurrect the light.
			await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
			await touch('touchcancel', null);
			await page.waitForTimeout(700);
			assert.equal((await light()).sheen, 0, `${engine}: cancelled touch stays unlit`);
			console.log(
				`${engine}: touch-scroll lighting resumes within the same gesture; cancellation settles`
			);
		} finally {
			await browser.close();
		}
	}
} finally {
	close();
}
