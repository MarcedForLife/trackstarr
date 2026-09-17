// The built UI with a queue larger than the overview preview and two pages.
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const shape = await fixtures();
const EPOCH = 'probe';
let items,
	previous,
	revision = 0,
	token = 0;
// Workers claiming files between the pages of a joined read, counted as how
// many later pages arrive from a queue that has moved on since the first.
let churn = 0;
let viewer = false;
let fail = false;
// Rules changed since the queue was last judged, so the stored plans describe
// a check the work phase is going to redo.
let stale = false;
const key = (item) => JSON.stringify([item.run, item.path]);
const run = shape.runs.runs[0].id;
const { site, close } = await serve(5199, shape, {
	'/api/auth/me': () => ({ name: 'tester', role: viewer ? 'viewer' : 'admin', must_change: false }),
	'/api/runs': () => ({ ...shape.runs, queue: items.length, queue_preview: items.slice(0, 3) }),
	'/api/runs/log': () => ({ lines: ['Reading the file'] }),
	'/api/library/work': () => ({ queued: [], active: [], pauses: [] }),
	'/api/library/file': (request) => ({
		current: true,
		card: shape.library.titles[0],
		file: {
			...shape.title.files[0],
			path: new URL(request.url, site).searchParams.get('path'),
			status: 'pending',
			why: { reasons: ['add stereo audio'], rules: ['stereo'] }
		}
	}),
	'/api/queue': async (request, response) => {
		if (fail) {
			response.statusCode = 503;
			return { status: 'queue unavailable' };
		}
		if (request.method === 'POST') {
			let raw = '';
			for await (const chunk of request) raw += chunk;
			const body = JSON.parse(raw);
			revision++;
			if (body.action === 'undo') {
				items = previous;
				return { restored: true };
			}
			const keys = new Set(body.items.map(key));
			const selected = items.filter((item) => keys.has(key(item)));
			previous = [...items];
			items = items.filter((item) => !keys.has(key(item)));
			if (body.action === 'top') {
				items.unshift(...selected);
				return { moved: selected.length, undo: String(++token) };
			}
			return { changed: selected.length };
		}
		const query = new URL(request.url, site).searchParams;
		const filtered = items.filter((item) =>
			item.path.toLowerCase().includes((query.get('q') || '').toLowerCase())
		);
		const offset = Number(query.get('offset')) || 0;
		if (offset && churn) {
			churn--;
			revision++;
		}
		const page = filtered.slice(offset, offset + 50);
		return {
			items: page,
			covers: Object.fromEntries(
				page
					.filter((item) => !item.path.includes('Movie 000'))
					// The title the file endpoint answers with. A row raises its sheet
					// from the cover it already has, and the card fills it in.
					.map((item) => [item.path, { id: shape.library.titles[0].id, name: 'Queue Movie' }])
			),
			plans: Object.fromEntries(
				page.map((item) => [
					item.path,
					{ status: 'pending', changes: 1, adds: ['2.0'], rebuilds: [], drops: 0 }
				])
			),
			plans_current: !stale,
			total: items.length,
			matched: filtered.length,
			offset,
			epoch: EPOCH,
			revision
		};
	}
});
try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch();
		try {
			items = Array.from({ length: 125 }, (_, i) => ({
				run,
				path: `/media/Queue Movie ${String(i).padStart(3, '0')}.mkv`,
				expected: 60
			}));
			const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
			const errors = [];
			page.on('pageerror', (error) => errors.push(error.message));
			await page.goto(site);
			const overview = page.locator('section[aria-labelledby="now"]');
			const row = overview.getByRole('button', { name: 'Actions for Queue Movie 002' });
			await row.click();
			await page.getByRole('button', { name: 'Move to top', exact: true }).click();
			await overview.getByText('File moved to top.', { exact: false }).waitFor();
			assert.equal(items[0].path, '/media/Queue Movie 002.mkv');
			await overview.getByRole('button', { name: 'Undo', exact: true }).click();
			await page.getByText('Queue order restored for files still waiting.').waitFor();
			assert.equal(items[0].path, '/media/Queue Movie 000.mkv');
			await overview.getByRole('button', { name: 'View queue (125)', exact: true }).click();
			const dialog = page.getByRole('dialog', { name: 'Queue 125' });
			await dialog.getByText('1–50 of 125').waitFor();
			await dialog
				.locator('img[data-cover]')
				.first()
				.evaluate((img) => img.decode());
			assert.equal(
				await dialog.locator('li').first().locator('img').count(),
				0,
				'Unknown files retain a placeholder'
			);
			const firstFile = dialog.locator('li').first();
			const details = firstFile.locator('button[aria-expanded]').first();
			assert.equal(
				await firstFile.getByText('/media/Queue Movie 000.mkv', { exact: true }).count(),
				0
			);
			await details.click();
			await firstFile.getByText('/media/Queue Movie 000.mkv', { exact: true }).waitFor();
			await firstFile.getByText('add stereo audio', { exact: true }).waitFor();
			assert.equal(
				await firstFile.getByRole('checkbox').isChecked(),
				false,
				'Expanding does not select'
			);
			await firstFile.getByRole('checkbox').check();
			assert.equal(
				await details.getAttribute('aria-expanded'),
				'true',
				'Selection preserves expansion'
			);
			await firstFile.getByRole('checkbox').uncheck();
			await details.focus();
			await page.keyboard.press('Enter');
			await firstFile
				.getByText('/media/Queue Movie 000.mkv', { exact: true })
				.waitFor({ state: 'detached' });
			await dialog.getByRole('checkbox', { name: 'Select Queue Movie 001', exact: true }).check();
			const coverButton = dialog
				.getByRole('button', { name: 'View Queue Movie details', exact: true })
				.first();
			await coverButton.click();
			const titleSheet = page.getByRole('dialog', {
				name: shape.library.titles[0].name,
				exact: true
			});
			await titleSheet.waitFor();
			await page.keyboard.press('Escape');
			await titleSheet.waitFor({ state: 'hidden' });
			await dialog.getByRole('checkbox', { name: 'Select Queue Movie 001', exact: true }).waitFor();
			assert.equal(
				await dialog
					.getByRole('checkbox', { name: 'Select Queue Movie 001', exact: true })
					.isChecked(),
				true
			);
			await page.waitForFunction(
				() => document.activeElement?.getAttribute('aria-label') === 'View Queue Movie details'
			);

			await dialog.getByRole('button', { name: 'Show more', exact: true }).click();
			await dialog.getByRole('checkbox', { name: 'Select shown (100)' }).waitFor();
			assert.equal(
				await dialog.locator('li').nth(55).locator('img[data-cover]').count(),
				1,
				'Later batches retain artwork metadata'
			);
			await dialog.getByRole('checkbox', { name: 'Select Queue Movie 055', exact: true }).check();
			await dialog.getByRole('button', { name: 'Move to top', exact: true }).click();
			await dialog.getByText('2 files moved to top.', { exact: false }).waitFor();
			assert.deepEqual(
				items.slice(0, 2).map((item) => item.path),
				['/media/Queue Movie 001.mkv', '/media/Queue Movie 055.mkv']
			);
			await dialog.getByRole('button', { name: 'Undo', exact: true }).click();
			await dialog
				.getByText('Queue order restored for files still waiting.', { exact: false })
				.waitFor();
			await dialog.getByRole('searchbox', { name: 'Search queue' }).fill('Movie 12');
			// Every match is on screen, so the count stands alone.
			await dialog.getByText('5 matching', { exact: true }).waitFor();
			await dialog.getByRole('checkbox', { name: 'Select shown (5)' }).check();
			await dialog.getByRole('button', { name: 'Skip', exact: true }).click();
			await page.getByRole('dialog').getByText('5 files skipped.', { exact: false }).waitFor();
			assert.equal(items.length, 120);
			const current = page.getByRole('dialog');
			await current.getByRole('searchbox').fill('Movie 00');
			await current.getByText('10 matching', { exact: true }).waitFor();
			await current.getByRole('button', { name: 'Actions for Queue Movie 000' }).click();
			await current.getByRole('button', { name: 'Pause…', exact: true }).click();
			await current.getByRole('button', { name: '1 hour', exact: true }).click();
			await current.getByText('1 file paused.', { exact: false }).waitFor();
			assert.equal(items.length, 119);
			await page.setViewportSize({ width: 390, height: 844 });
			assert.equal(
				await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
				false
			);
			assert.equal(await current.evaluate((el) => el.scrollWidth > el.clientWidth), false);
			await current.getByRole('checkbox', { name: 'Select Queue Movie 001', exact: true }).check();
			for (const width of [320, 390]) {
				await page.setViewportSize({ width, height: 844 });
				const bounds = await current.boundingBox();
				assert.ok(bounds.y > 80, 'The sheet leaves the page visible above it');
				const actions = await Promise.all(
					['Move to top', 'Pause selected files', 'Skip'].map((name) =>
						current.getByRole('button', { name, exact: true }).boundingBox()
					)
				);
				assert.ok(
					actions.every((box) => box && box.width >= 80 && box.x >= 0 && box.x + box.width <= width)
				);
				assert.ok(
					actions.every((box) => Math.abs(box.y - actions[0].y) < 1),
					'Bulk actions stay on one row'
				);
				assert.equal(await current.evaluate((el) => el.scrollWidth > el.clientWidth), false);
			}
			await page.keyboard.press('Escape');
			await current.waitFor({ state: 'hidden' });
			assert.match(await page.locator(':focus').innerText(), /View queue/);
			await overview.getByRole('button', { name: /View queue/ }).click();
			await page.getByRole('dialog').waitFor();
			await page.goBack();
			await page.getByRole('dialog').waitFor({ state: 'hidden' });
			fail = true;
			await overview.getByRole('button', { name: /View queue/ }).click();
			await page.getByRole('dialog').getByRole('alert').waitFor();
			fail = false;
			await page.getByRole('dialog').getByRole('button', { name: 'Retry' }).click();
			await page.getByRole('dialog').getByText('1–50 of 119').waitFor();
			// A claim between two pages, so the reader starts the read again rather
			// than joining pages taken from two different queues.
			const joining = page.getByRole('dialog');
			churn = 1;
			await joining.getByRole('button', { name: 'Show more', exact: true }).click();
			await joining.getByText('1–100 of 119').waitFor();
			assert.equal(await joining.locator('li').count(), 100);
			// A queue that keeps moving, where the rows already read stay put.
			churn = 30;
			await joining.getByRole('button', { name: 'Show more', exact: true }).click();
			await joining.getByRole('alert').getByText('changing quickly').waitFor();
			assert.equal(await joining.locator('li').count(), 100);
			churn = 0;
			await joining.getByRole('button', { name: 'Retry' }).click();
			// The whole queue is read, so it says what it holds.
			await joining.getByText('119 waiting', { exact: true }).waitFor();
			// Rules changed after the last check: the row cannot go on badging
			// layouts the replan may not ask for, and the page says why.
			await joining.getByText('+2.0', { exact: true }).first().waitFor();
			stale = true;
			await joining.getByRole('searchbox').fill('Movie 01');
			await joining.getByText('checked again before it is processed').waitFor();
			await joining.getByText('Re-check pending').first().waitFor();
			assert.equal(await joining.getByText('+2.0', { exact: true }).count(), 0);
			stale = false;
			await joining.getByRole('searchbox').fill('');
			await joining.getByText('1–50 of 119').waitFor();
			await page
				.getByRole('dialog')
				.getByRole('button', { name: 'Close', exact: true })
				.last()
				.click();
			await page.getByRole('dialog').waitFor({ state: 'hidden' });
			viewer = true;
			await page.reload();
			await overview.getByRole('button', { name: /View queue/ }).click();
			await page.getByRole('dialog').getByText('1–50 of 119').waitFor();
			assert.equal(await page.getByRole('dialog').getByRole('checkbox').count(), 0);
			assert.equal(
				await page
					.getByRole('dialog')
					.getByRole('button', { name: /Actions for/ })
					.count(),
				0
			);
			items[0].path =
				'/media/Shows/A Very Long Series Name/Season 02/A Very Long Series Name - S02E05-E06 - Episode title.mkv';
			await page.reload();
			await overview.getByRole('button', { name: /View queue/ }).click();
			const episodeRow = page.getByRole('dialog').locator('li').first();
			await episodeRow.getByText('S02E05-E06', { exact: true }).waitFor();
			assert.equal(await episodeRow.getByText(items[0].path, { exact: true }).count(), 0);
			await episodeRow.locator('button[aria-expanded]').first().click();
			await episodeRow.getByText(items[0].path, { exact: true }).waitFor();
			assert.equal(
				await page.getByRole('dialog').evaluate((el) => el.scrollWidth > el.clientWidth),
				false
			);
			assert.deepEqual(errors, []);
			viewer = false;
			console.log(
				`${engine}: queue menus, full search, paging under a moving queue, stale plans, cross-page selection, reorder/undo, pause/skip, mobile, focus and viewer access passed`
			);
		} finally {
			await browser.close();
		}
	}
} finally {
	await close();
}
