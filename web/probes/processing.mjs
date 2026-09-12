// Run after npm run build: node probes/processing.mjs
// Exercise the overview's live work, failure navigation, offline progress and completed activity.
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const shape = await fixtures();
let activity;
let offline = false;
let events;
const { site, close } = await serve(5198, shape, {
	'/api/runs': (_request, response) => {
		if (offline) {
			response.statusCode = 503;
			return { error: 'unavailable' };
		}
		return activity;
	},
	'/api/events': () => events,
	'/api/runs/log': () => ({ lines: ['ffmpeg exited 1: invalid input'] }),
	'/api/runs/pause': () => ({ ...activity, paused: true }),
	'/api/runs/resume': () => activity
});

const wake = (page) => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
const saved = {
	event: 'sweep',
	run: 'saved-plan',
	ts: '2026-09-12T12:00:00Z',
	version: 'test',
	dry_run: true,
	files: 12,
	counts: { pending: 12 },
	seconds: 30
};

try {
	for (const engine of ENGINES) {
		activity = structuredClone(shape.runs);
		events = { ...shape.events, events: [saved] };
		offline = false;
		const browser = await playwright[engine].launch();
		try {
			const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
			const errors = [];
			page.on('pageerror', (error) => errors.push(error.message));
			await page.goto(site);
			await page.getByRole('heading', { name: /Working/ }).waitFor();
			assert.doesNotMatch(
				await page.locator('[aria-label="Overall processing progress"]').innerText(),
				/to rewrite|Rewriting|waiting|Sweep:/
			);
			assert.match(await page.locator('#pause-explanation').innerText(), /already started finish/);
			const eta = page.getByLabel('Estimated time remaining', { exact: true });
			assert.match(await eta.innerText(), /^~.* left$/);
			assert.match(await eta.locator('..').innerText(), /left\s*·\s*99%/);

			const processing = page.locator('section[aria-labelledby="now"]');
			const feed = page.locator('section[aria-labelledby="activity-heading"]');
			assert.equal(await page.getByRole('button', { name: 'Results', exact: true }).count(), 0);
			assert.doesNotMatch(await processing.innerText(), /Recently processed|Results/);
			await feed.getByText('Swept 12 files', { exact: true }).waitFor();
			await page.getByRole('link', { name: '1 failed', exact: true }).click();
			await page.waitForURL('**/events?filter=issues');
			await page.waitForFunction(
				() =>
					document.querySelector('[role="radio"][aria-checked="true"]')?.textContent.trim() ===
					'Issues'
			);
			await page.goBack();
			await page.getByRole('heading', { name: /Working/ }).waitFor();

			await page.getByRole('button', { name: 'Pause', exact: true }).click();
			await page.getByRole('heading', { name: /Paused/ }).waitFor();
			assert.equal(await eta.count(), 0);
			assert.match(
				await page.locator('section[aria-labelledby="now"]').innerText(),
				/Nothing new starts until you resume/
			);
			await page.getByRole('button', { name: 'Resume', exact: true }).click();
			await page.getByRole('heading', { name: /Working/ }).waitFor();

			offline = true;
			await wake(page);
			await page.getByRole('heading', { name: /Connection lost/ }).waitFor();
			assert.equal(await eta.count(), 0);
			assert.match(
				await page.locator('section[aria-labelledby="now"]').innerText(),
				/Showing the last update/
			);
			const bars = page.getByRole('progressbar');
			const before = await bars.evaluateAll((nodes) => nodes.map((node) => node.innerHTML));
			const frozen = await processing.innerText();
			await page.waitForTimeout(2200);
			assert.deepEqual(
				await bars.evaluateAll((nodes) => nodes.map((node) => node.innerHTML)),
				before
			);
			assert.equal(await processing.innerText(), frozen, 'Offline readouts must stay fixed');
			offline = false;
			await wake(page);
			await page.getByRole('heading', { name: /Working/ }).waitFor();

			// Completion moves to Activity exactly once, and survives reload.
			const sweep = activity.runs[0];
			events.events.unshift({
				...saved,
				run: sweep.id,
				dry_run: false,
				files: 1180,
				counts: { conform: 1178, failed: 1, modified: 1 },
				ts: '2026-09-13T12:00:00Z'
			});
			activity = { ...activity, runs: [], queue: 0, working: 0, rewrites: 0 };
			await wake(page);
			await page.getByRole('heading', { name: /Idle/ }).waitFor();
			assert.equal(await page.locator('#pause-explanation').count(), 0);
			const suspend = page.getByRole('button', { name: 'Suspend', exact: true });
			assert.equal(await suspend.getAttribute('aria-describedby'), null);
			await suspend.click();
			await page.getByRole('heading', { name: 'Suspended', exact: true }).waitFor();
			await page.getByRole('button', { name: 'Resume', exact: true }).click();
			await page.getByRole('heading', { name: /Idle/ }).waitFor();

			await feed.getByText('Swept 1,180 files', { exact: true }).waitFor();
			assert.equal(await feed.getByText('Swept 1,180 files', { exact: true }).count(), 1);
			assert.equal(await processing.getByRole('link', { name: /failed/ }).count(), 0);
			assert.doesNotMatch(await processing.innerText(), /Dune|Recently processed|Results/);
			await page.reload();
			await feed.getByText('Swept 1,180 files', { exact: true }).waitFor();
			assert.equal(await feed.getByText('Swept 1,180 files', { exact: true }).count(), 1);
			assert.equal(
				await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
				true
			);

			assert.deepEqual(errors, []);
			console.log(
				`${engine}: live controls, failure navigation, offline freeze and completed activity passed`
			);
		} finally {
			await browser.close();
		}
	}
} finally {
	close();
}
