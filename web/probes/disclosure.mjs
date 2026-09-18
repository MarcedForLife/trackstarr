// Run after building: node probes/disclosure.mjs
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const shape = await fixtures();
const items = shape.runs.queue_preview;
const { site, close } = await serve(Number(process.env.PORT ?? 5206), shape, {
	'/api/queue': () => ({
		items,
		total: items.length,
		matched: items.length,
		offset: 0,
		epoch: 'disclosure-probe',
		revision: 0,
		plans_current: true
	})
});
try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch();
		try {
			for (const width of [412, 1280]) {
				const page = await browser.newPage({ viewport: { width, height: 1000 } });
				page.setDefaultTimeout(15000);
				await page.goto(site);
				const rows = page.locator('section[aria-labelledby="now"] li');
				const buttons = rows.locator('button[aria-expanded]');
				await buttons.first().waitFor();
				await page.waitForTimeout(500);
				for (let i = 0; i < Math.min(await rows.count(), 3); i++) {
					await check(
						rows.nth(i).locator('button[aria-expanded]').first(),
						`${engine} ${width} work ${i}`
					);
				}
				await page.getByRole('button', { name: 'View queue', exact: true }).click();
				const queued = page.getByRole('dialog').locator('li button[aria-expanded]').first();
				await queued.waitFor();
				await page.waitForTimeout(500);
				await check(queued, `${engine} ${width} queue`);
				await page.goto(`${site}/events`);
				const event = page.locator('li button[aria-expanded]').first();
				await event.waitFor();
				await page.waitForTimeout(500);
				await check(event, `${engine} ${width} event`);
				await page.emulateMedia({ reducedMotion: 'reduce' });
				await event.click();
				await page.waitForTimeout(50);
				assert.equal(
					await page
						.locator('.reveal')
						.first()
						.evaluate(
							(node) => node.getAnimations().filter((a) => a.playState === 'running').length
						),
					0
				);
				await page.close();
			}
		} finally {
			await browser.close();
		}
	}
} finally {
	await close();
}

async function check(button, label) {
	await button.page().bringToFront();
	const result = await button.evaluate(async (trigger) => {
		const row = trigger.closest('li');
		const frames = [];
		async function sample(action) {
			action();
			const samples = [];
			const start = performance.now();
			while (performance.now() - start < 360) {
				await new Promise(requestAnimationFrame);
				const panel = document.getElementById(trigger.getAttribute('aria-controls'));
				if (!panel) continue;
				const rect = panel.getBoundingClientRect();
				const content = panel.firstElementChild.getBoundingClientRect();
				samples.push({
					height: rect.height,
					gap: row.getBoundingClientRect().bottom - Math.min(rect.bottom, content.bottom)
				});
			}
			frames.push(samples);
		}
		await sample(() => trigger.click());
		await sample(() => {
			const extra = document.createElement('div');
			extra.style.height = '100px';
			document
				.getElementById(trigger.getAttribute('aria-controls'))
				.firstElementChild.firstElementChild.append(extra);
		});
		await sample(() => trigger.click());
		await sample(() => {
			trigger.click();
			setTimeout(() => trigger.click(), 70);
			setTimeout(() => trigger.click(), 120);
		});
		trigger.click();
		return frames;
	});
	for (const [index, frames] of result.entries()) {
		const gaps = frames.map((frame) => frame.gap);
		const drift = Math.max(...gaps) - Math.min(...gaps);
		assert.ok(drift < 2, `${label} phase ${index}: frame/detail gap drifted ${drift}px`);
		assert.ok(
			new Set(frames.map((frame) => Math.round(frame.height))).size > 2,
			`${label} phase ${index}: height must animate`
		);
	}
	console.log(`PASS ${label}: open, resize, close, reversal`);
	await button.page().waitForTimeout(300);
}
