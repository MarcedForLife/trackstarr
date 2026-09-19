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
	// Numbered as the service numbers them, which is what the rows lead with.
	'/api/runs': () => ({
		...shape.runs,
		queue: items.length,
		queue_preview: items.slice(0, 3).map((item, index) => ({ ...item, position: index + 1 }))
	}),
	'/api/runs/log': () => ({ lines: ['Reading the file'] }),
	'/api/library/work': () => ({ queued: [], active: [], pauses: [] }),
	'/api/library/file': (request) => ({
		current: true,
		card: shape.library.titles[0],
		file: {
			...shape.title.files[0],
			path: new URL(request.url, site).searchParams.get('path'),
			status: 'pending',
			why: {
				reasons: ['add stereo audio'],
				rules: ['stereo'],
				changes: [{ rule: 'stereo', text: 'add stereo audio' }]
			}
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
			// A press held past the long-press timeout and released where it landed.
			// Hovered first, since that waits out a sheet still easing its height;
			// a measured point would be where the row used to be.
			const hold = async (target) => {
				await target.hover();
				await page.mouse.down();
				await page.waitForTimeout(700);
				await page.mouse.up();
			};
			// A box that has stopped moving: the list slides while the select chin
			// opens, so a point measured on the way lands on the row below.
			const steady = async (target) => {
				let box = await target.boundingBox();
				for (let tries = 0; tries < 20; tries++) {
					await page.waitForTimeout(100);
					const now = await target.boundingBox();
					if (now.y === box.y && now.height === box.height) return now;
					box = now;
				}
				return box;
			};
			const errors = [];
			page.on('pageerror', (error) => errors.push(error.message));
			await page.goto(site);
			const overview = page.locator('section[aria-labelledby="now"]');
			const row = overview.getByRole('button', { name: 'Actions for Queue Movie 002' });
			await row.click();
			await page.getByRole('button', { name: 'Prioritise', exact: true }).click();
			// The overview answers with the queue itself: the row takes first place
			// as the fresh snapshot lands. Undo is the dialog's.
			await overview
				.getByRole('listitem')
				.filter({ hasText: 'Queue Movie 002' })
				.getByText('place 1', { exact: true })
				.waitFor();
			assert.equal(items[0].path, '/media/Queue Movie 002.mkv');
			assert.equal(await overview.getByRole('button', { name: 'Undo', exact: true }).count(), 0);
			// Back as it was for the dialog, which reads the queue afresh.
			items = previous;
			revision++;
			await overview.getByRole('button', { name: 'View queue', exact: true }).click();
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
			// The plan reads as it does in a title sheet: the track list, and a rule
			// chip opening on the lines it ordered.
			await firstFile.getByRole('list', { name: 'Tracks' }).waitFor();
			await firstFile.getByRole('button', { name: 'stereo', exact: true }).click();
			await page.getByText('add stereo audio', { exact: true }).waitFor();
			await page.keyboard.press('Escape');
			await page.getByText('add stereo audio', { exact: true }).waitFor({ state: 'hidden' });
			// A keyboard closes the row it opened, while a tap still means open.
			await details.focus();
			await page.keyboard.press('Enter');
			await firstFile
				.getByText('/media/Queue Movie 000.mkv', { exact: true })
				.waitFor({ state: 'detached' });
			// Picking is a mode, so a reader after one file's menu is offered no ticks.
			assert.equal(await dialog.getByRole('checkbox').count(), 0, 'Ticks wait to be asked for');
			const coverButton = dialog
				.getByRole('button', { name: 'View Queue Movie details', exact: true })
				.first();
			// FileTitle unmounts its sheet immediately on close, even with reads pending.
			let releaseTitle;
			const heldTitle = new Promise((resolve) => (releaseTitle = resolve));
			const pendingReads = [];
			for (const kind of ['title', 'links', 'work']) {
				pendingReads.push(page.waitForRequest(`**/api/library/${kind}**`));
				await page.route(`**/api/library/${kind}**`, async (route) => {
					await heldTitle;
					await route.fulfill({
						json:
							kind === 'title'
								? shape.title
								: kind === 'links'
									? { links: [] }
									: { queued: [], active: [], pauses: [] }
					});
				});
			}
			await coverButton.click();
			const titleSheet = page.getByRole('dialog', {
				name: shape.library.titles[0].name,
				exact: true
			});
			await titleSheet.waitFor();
			await Promise.all(pendingReads);
			await page.keyboard.press('Escape');
			await titleSheet.waitFor({ state: 'hidden' });
			await page.waitForFunction(
				() => document.activeElement?.getAttribute('aria-label') === 'View Queue Movie details'
			);
			const lateReads = ['title', 'links', 'work'].map((kind) =>
				page.waitForResponse(`**/api/library/${kind}**`)
			);
			releaseTitle();
			await Promise.all((await Promise.all(lateReads)).map((response) => response.finished()));
			for (const kind of ['title', 'links', 'work']) await page.unroute(`**/api/library/${kind}**`);
			assert.equal(await titleSheet.count(), 0, 'late reads cannot remount a FileTitle sheet');
			// Held rather than tapped, the same cover starts picking with that file
			// ticked, which is how the grid is selected.
			await hold(coverButton);
			const picked = dialog.locator('li').nth(1).getByRole('checkbox');
			await picked.waitFor();
			assert.equal(await picked.isChecked(), true);
			// A tap picks while picking, and the row it lands on stays shut.
			const unknown = firstFile.getByRole('checkbox');
			await unknown.check();
			assert.equal(
				await firstFile.getByText('/media/Queue Movie 000.mkv', { exact: true }).count(),
				0,
				'A picking tap does not open the row'
			);
			// The corner under the three buttons is the row's press like the rest of
			// the card, for a tap and for a hold.
			const corner = async () => {
				const box = await steady(firstFile);
				return { x: box.x + box.width - 20, y: box.y + box.height - 8 };
			};
			const low = await corner();
			await page.mouse.click(low.x, low.y);
			assert.equal(await unknown.isChecked(), false, 'A tap under the buttons picks');
			await page.mouse.click(low.x, low.y);
			assert.equal(await unknown.isChecked(), true);
			// The chevron is a press of its own, so a row still opens while picking.
			const opener = firstFile.getByRole('button', { name: 'Details for Queue Movie 000' });
			await opener.click();
			await firstFile.getByText('/media/Queue Movie 000.mkv', { exact: true }).waitFor();
			assert.equal(await unknown.isChecked(), true, 'and leaves the pick alone');
			await opener.click();
			await firstFile
				.getByText('/media/Queue Movie 000.mkv', { exact: true })
				.waitFor({ state: 'detached' });
			await unknown.uncheck();
			await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
			assert.equal(
				await dialog.getByRole('checkbox').count(),
				0,
				'Leaving picking drops the ticks'
			);
			// The whole row answers the same hold, without opening on the way.
			await hold(details);
			assert.equal(await unknown.isChecked(), true, 'A held row picks');
			assert.equal(
				await firstFile.getByText('/media/Queue Movie 000.mkv', { exact: true }).count(),
				0,
				'and does not open it'
			);
			await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
			// A file whose title is unknown wears the initials tile, which is still
			// the file, so it holds and ticks like any other cover.
			await hold(firstFile.locator('span.poster'));
			assert.equal(await unknown.isChecked(), true, 'A cover-less tile picks too');
			await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
			// Including the corner the buttons leave, which is the row's like the rest.
			const spot = await corner();
			await page.mouse.move(spot.x, spot.y);
			await page.mouse.down();
			await page.waitForTimeout(700);
			await page.mouse.up();
			assert.equal(await unknown.isChecked(), true, 'A hold under them starts picking');
			// The second row, whose top the scroller does not clip: a press aimed at
			// a point has to land on the row, not on the chrome over it.
			const mark = await dialog
				.locator('li')
				.nth(1)
				.locator('span[aria-hidden="true"].rounded-full')
				.boundingBox();
			const marked = await dialog.locator('li').nth(1).boundingBox();
			// The mark the hold raises appears under the finger, so it must not take
			// the press that raised it. Kept as an offset into the row, since the
			// list slides as the chin closes.
			const intoRow = {
				x: mark.x + mark.width / 2 - marked.x,
				y: mark.y + mark.height / 2 - marked.y
			};
			await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
			const back = await steady(dialog.locator('li').nth(1));
			await page.mouse.move(back.x + intoRow.x, back.y + intoRow.y);
			await page.mouse.down();
			await page.waitForTimeout(700);
			await page.mouse.up();
			assert.equal(await picked.isChecked(), true, 'A hold where the mark lands picks');
			await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
			// A held finger that shifts is still holding the same row; one that
			// leaves it is not.
			const middle = await steady(dialog.locator('li').nth(1));
			const at = { x: middle.x + middle.width / 2, y: middle.y + middle.height / 2 };
			await page.mouse.move(at.x, at.y);
			await page.mouse.down();
			await page.waitForTimeout(250);
			await page.mouse.move(at.x + 18, at.y + 12);
			await page.waitForTimeout(450);
			await page.mouse.up();
			assert.equal(await picked.isChecked(), true, 'A hold that shifts still picks');
			await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
			// The raise and the accent edge it arms are the row's own, so both go at
			// its edge and come back on. Re-measured, since the chin closing slides
			// the list.
			const card = dialog.locator('li').nth(1).locator('div').first();
			const drawn = () =>
				card.evaluate((el) => {
					const now = getComputedStyle(el);
					return `${now.translate} ${now.borderTopColor}`;
				});
			const still = await steady(dialog.locator('li').nth(1));
			const on = { x: still.x + still.width / 2, y: still.y + still.height / 2 };
			const off = { x: on.x, y: still.y + still.height + 20 };
			const resting = await drawn();
			await page.mouse.move(on.x, on.y);
			await page.mouse.down();
			await page.waitForTimeout(700);
			const holding = await drawn();
			assert.notEqual(holding, resting, 'A held row is raised and armed');
			await page.mouse.move(off.x, off.y);
			await page.waitForTimeout(300);
			assert.equal(await drawn(), resting, 'and lowers where the pointer leaves it');
			await page.mouse.move(on.x, on.y);
			await page.waitForTimeout(300);
			assert.equal(await drawn(), holding, 'and comes back on');
			await page.mouse.move(off.x, off.y);
			await page.mouse.up();
			assert.equal(
				await dialog.getByRole('checkbox').count(),
				0,
				'A hold carried off the row picks nothing'
			);
			await dialog.getByRole('button', { name: 'Select', exact: true }).click();
			assert.equal(await unknown.isChecked(), false, 'and what was picked with them');
			await picked.check();

			await dialog.getByRole('button', { name: 'Show more', exact: true }).click();
			await dialog.getByRole('button', { name: 'Select shown (100)', exact: true }).waitFor();
			assert.equal(
				await dialog.locator('li').nth(55).locator('img[data-cover]').count(),
				1,
				'Later batches retain artwork metadata'
			);
			await dialog.locator('li').nth(55).getByRole('checkbox').check();
			await dialog.getByRole('button', { name: 'Prioritise', exact: true }).click();
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
			await dialog.getByRole('button', { name: 'Select shown (5)', exact: true }).click();
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
			// The row fades out in place at the width it had, so the narrower
			// viewport waits until it has gone.
			await current
				.locator('li')
				.filter({ hasText: 'Queue Movie 000' })
				.waitFor({ state: 'detached' });
			await page.setViewportSize({ width: 390, height: 844 });
			assert.equal(
				await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
				false
			);
			assert.equal(await current.evaluate((el) => el.scrollWidth > el.clientWidth), false);
			await current.locator('li').nth(1).getByRole('checkbox').check();
			for (const width of [320, 390]) {
				await page.setViewportSize({ width, height: 844 });
				const bounds = await current.boundingBox();
				assert.ok(bounds.y > 80, 'The sheet leaves the page visible above it');
				const actions = await Promise.all(
					['Prioritise', 'Pause selected files', 'Skip'].map((name) =>
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
			// Escape steps out of picking before it closes anything, as back does.
			await page.keyboard.press('Escape');
			await current.getByRole('checkbox').first().waitFor({ state: 'detached' });
			await page.keyboard.press('Escape');
			await current.waitFor({ state: 'hidden' });
			assert.match(await page.locator(':focus').innerText(), /View queue/);
			await overview.getByRole('button', { name: /View queue/ }).click();
			await page.getByRole('dialog').waitFor();
			// Picking is a step back of its own, so the sheet outlives the first one.
			const selecting = page.getByRole('dialog').locator('button[aria-pressed]');
			await selecting.click();
			await page.getByRole('dialog').getByRole('checkbox').first().waitFor();
			await page.goBack();
			await page.getByRole('dialog').waitFor();
			assert.equal(await selecting.getAttribute('aria-pressed'), 'false', 'Back leaves picking');
			assert.equal(await page.getByRole('dialog').getByRole('checkbox').count(), 0);
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
			await joining.getByText('+1', { exact: true }).first().waitFor();
			stale = true;
			await joining.getByRole('searchbox').fill('Movie 01');
			await joining.getByText('checked again before it is processed').waitFor();
			await joining.getByText('Re-check pending').first().waitFor();
			// The rows the search cut are still fading out with their chips.
			await joining.getByText('+1', { exact: true }).first().waitFor({ state: 'detached' });
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
				await page.getByRole('dialog').getByRole('button', { name: 'Select', exact: true }).count(),
				0,
				'A viewer is not offered picking'
			);
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
			// The gesture on a real touch pointer, where Chrome scrolls on the first
			// few pixels unless the row has claimed the touch. CDP touch is
			// Chromium's.
			if (engine === 'chromium') {
				const phone = await browser.newPage({
					viewport: { width: 412, height: 915 },
					deviceScaleFactor: 2,
					isMobile: true,
					hasTouch: true
				});
				const cdp = await phone.context().newCDPSession(phone);
				const finger = (type, x, y) =>
					cdp.send('Input.dispatchTouchEvent', {
						type,
						touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }]
					});
				await phone.goto(site);
				await phone.getByRole('button', { name: /View queue/ }).click();
				const sheet = phone.getByRole('dialog');
				await sheet.getByText('1–50 of 119').waitFor();
				const held = await steady(sheet.locator('li').nth(1));
				const spot = { x: held.x + held.width / 2, y: held.y + held.height / 2 };
				await finger('touchStart', spot.x, spot.y);
				await phone.waitForTimeout(300);
				await finger('touchMove', spot.x + 6, spot.y + 14);
				await phone.waitForTimeout(100);
				await finger('touchMove', spot.x + 9, spot.y + 20);
				await phone.waitForTimeout(400);
				await finger('touchEnd', spot.x + 9, spot.y + 20);
				await sheet.getByRole('checkbox').first().waitFor();
				assert.equal(
					await sheet.locator('li').nth(1).getByRole('checkbox').isChecked(),
					true,
					'A finger that shifts mid-hold still picks'
				);
				await phone.keyboard.press('Escape');
				await sheet.getByRole('checkbox').first().waitFor({ state: 'detached' });
				// Carried off the row while still held, which is the one thing that
				// abandons a pick.
				const again = await steady(sheet.locator('li').nth(1));
				const point = { x: again.x + again.width / 2, y: again.y + again.height / 2 };
				await finger('touchStart', point.x, point.y);
				await phone.waitForTimeout(700);
				await finger('touchMove', point.x, again.y + again.height + 30);
				await phone.waitForTimeout(100);
				await finger('touchEnd', point.x, again.y + again.height + 30);
				await phone.waitForTimeout(300);
				assert.equal(
					await sheet.getByRole('checkbox').count(),
					0,
					'A hold carried off the row picks nothing'
				);
				// The grid answers a finger the same way, since a row claims the touch
				// to raise as a poster does. Checked here for the one touch session.
				await phone.goto(`${site}/library`);
				const tile = phone.locator('.poster').first();
				const poster = await steady(tile);
				const card = { x: poster.x + poster.width / 2, y: poster.y + poster.height / 2 };
				await finger('touchStart', card.x, card.y);
				await phone.waitForTimeout(700);
				assert.equal(
					await tile.getAttribute('aria-pressed'),
					null,
					'A grid hold picks nothing until the lift'
				);
				await finger('touchEnd', card.x, card.y);
				assert.equal(await tile.getAttribute('aria-pressed'), 'true', 'The lift makes the pick');
				await phone.close();
			}
			console.log(
				`${engine}: queue menus, full search, paging under a moving queue, stale plans, cross-page selection, holds by mouse and finger, reorder/undo, pause/skip, mobile, focus and viewer access passed`
			);
		} finally {
			await browser.close();
		}
	}
} finally {
	await close();
}
