// Run after npm run build: node probes/replan-scroll.mjs
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const shape = await fixtures();
let activity;
let library;
const { site, close } = await serve(5199, shape, {
	'/api/runs': () => activity,
	'/api/library': () => library
});

try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch();
		try {
			for (const width of [390, 1280]) {
				activity = structuredClone(shape.runs);
				activity.runs[0].dry_run = true;
				const page = await browser.newPage({
					viewport: { width, height: 844 },
					// Firefox does not support Playwright's mobile mode.
					isMobile: engine !== 'firefox' && width < 1024,
					hasTouch: width < 1024
				});
				const errors = [];
				page.on('pageerror', (error) => errors.push(error.message));
				await page.goto(site);
				const processing = page.locator('section[aria-labelledby="now"]');
				await processing.getByRole('listitem').first().waitFor();
				await page.waitForTimeout(500);
				const position = () =>
					page.evaluate(() =>
						innerWidth < 1024 ? scrollY : document.querySelector('main').scrollTop
					);
				await page.evaluate(() => {
					if (innerWidth < 1024) window.scrollTo(0, 450);
					else document.querySelector('main').scrollTop = 450;
				});
				const held = await position();
				assert.ok(held > 0, 'The reader is partway down the page');
				for (let update = 0; update < 5; update++) {
					activity.runs[0].active[0].path = `/media/Processing Movie ${update}.mkv`;
					activity.queue_preview = activity.queue_preview.map((row, index) => ({
						...row,
						path: `/media/Queued Movie ${update}-${index}.mkv`
					}));
					await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
					await processing.getByText(`Processing Movie ${update}`, { exact: true }).waitFor();
					// Include the leaving rows' fade and the remaining rows' movement.
					await page.waitForTimeout(350);
					const now = await position();
					assert.ok(
						Math.abs(now - held) <= 1,
						`${engine} at ${width}px: update ${update} moved the reader from ${held} to ${now}`
					);
				}
				// The library re-sorts as a sweep updates each title's processed time.
				library = {
					...shape.library,
					titles: Array.from({ length: 36 }, (_, index) => ({
						...shape.library.titles[1],
						id: `scroll-${index}`,
						name: `Scroll Movie ${index}`,
						processed: 1000 - index
					}))
				};
				await page.goto(`${site}/library`);
				await page.locator('.shelf .tile').first().waitFor();
				await page.waitForTimeout(500);
				await page.evaluate(() => {
					if (innerWidth < 1024) window.scrollTo(0, 450);
					else document.querySelector('main').scrollTop = 450;
				});
				const libraryHeld = await position();
				assert.ok(libraryHeld > 0, 'The library is scrolled');
				for (let update = 0; update < 5; update++) {
					const card = library.titles[12 + update];
					card.processed = 2000 + update;
					await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
					await page.waitForFunction(
						(name) => document.querySelector('.shelf .tile')?.textContent.includes(name),
						card.name
					);
					await page.waitForTimeout(500);
					const now = await position();
					assert.ok(
						Math.abs(now - libraryHeld) <= 1,
						`${engine} at ${width}px: library update ${update} moved the reader from ${libraryHeld} to ${now}`
					);
				}
				assert.deepEqual(errors, []);
				await page.close();
				console.log(`${engine} at ${width}px: replan and library updates preserve scroll position`);
			}
		} finally {
			await browser.close();
		}
	}
} finally {
	await close();
}
