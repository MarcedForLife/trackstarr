// Run after npm run build: node probes/processing.mjs
// Exercise the overview's live, failed, offline and saved-result states.
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
			assert.match(
				await page.locator('section[aria-labelledby="now"]').innerText(),
				/Rewriting 1 file · 5 waiting/
			);
			assert.match(await page.locator('#pause-explanation').innerText(), /already started finish/);

			await page.getByRole('button', { name: '1 failed', exact: true }).click();
			const failures = page.getByRole('region', { name: 'Failed files', exact: true });
			await failures.waitFor();
			assert.equal(await failures.evaluate((node) => document.activeElement === node), true);
			assert.match(await failures.innerText(), /ffmpeg exited 1/);
			await failures.getByText('ffmpeg exited 1: invalid input', { exact: true }).waitFor();
			assert.equal(
				await page.evaluate(() => {
					const ids = [...document.querySelectorAll('[id]')].map((node) => node.id);
					return ids.length === new Set(ids).size;
				}),
				true,
				'Failure preview must not duplicate disclosure IDs'
			);

			await page.getByRole('button', { name: 'Results', exact: true }).click();
			const results = page.getByRole('region', { name: 'Results', exact: true });
			assert.equal(await results.locator('article').count(), 4);
			assert.match(await results.innerText(), /Applying · In progress/);
			assert.match(await results.innerText(), /Plan only · Completed/);
			assert.equal(await results.locator('time').count(), 4);

			await page.getByRole('button', { name: 'Pause', exact: true }).click();
			await page.getByRole('heading', { name: /Paused/ }).waitFor();
			assert.match(
				await page.locator('section[aria-labelledby="now"]').innerText(),
				/Nothing new starts until you resume/
			);
			await page.getByRole('button', { name: 'Resume', exact: true }).click();
			await page.getByRole('heading', { name: /Working/ }).waitFor();

			offline = true;
			await wake(page);
			await page.getByRole('heading', { name: /Connection lost/ }).waitFor();
			assert.match(
				await page.locator('section[aria-labelledby="now"]').innerText(),
				/Showing the last update/
			);
			const bars = page.getByRole('progressbar');
			const before = await bars.evaluateAll((nodes) => nodes.map((node) => node.innerHTML));
			const elapsed = await results.innerText();
			await page.waitForTimeout(2200);
			assert.deepEqual(
				await bars.evaluateAll((nodes) => nodes.map((node) => node.innerHTML)),
				before
			);
			assert.equal(await results.innerText(), elapsed, 'Offline elapsed values must stay fixed');
			offline = false;
			await wake(page);
			await page.getByRole('heading', { name: /Working/ }).waitFor();

			// Complete the applying sweep and preserve its separate receipt after reload.
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
			await results.getByText('Applied · Completed', { exact: true }).first().waitFor();
			await page.reload();
			await page.getByRole('button', { name: 'Results', exact: true }).click();
			assert.equal(await results.locator('article').count(), 2);
			assert.match(await results.innerText(), /Applied · Completed/);
			assert.match(await results.innerText(), /Plan only · Completed/);
			await page.getByRole('button', { name: '1 failed', exact: true }).click();
			assert.match(await failures.innerText(), /File details are no longer in this preview/);
			assert.equal(
				await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
				true
			);
			await failures.getByRole('link', { name: 'View issues in activity' }).click();
			await page.waitForURL('**/events?filter=issues');
			await page.waitForFunction(() => {
				const button = [...document.querySelectorAll('button')].find(
					(node) => node.textContent.trim() === 'Issues'
				);
				return button?.getAttribute('aria-checked') === 'true';
			});
			assert.deepEqual(errors, []);
			console.log(
				`${engine}: processing states, failure focus, offline freeze, reload and issue navigation passed`
			);
		} finally {
			await browser.close();
		}
	}
} finally {
	close();
}
