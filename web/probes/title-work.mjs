import assert from 'node:assert/strict';
import playwright from 'playwright';
import { fixtures, serve } from './stub.mjs';
const shape = await fixtures();
const title = shape.library.titles.find((card) => card.id === shape.title.id);
const file = shape.title.files[0];
title.kind = 'movie';
title.files = 1;
shape.title.total = 1;
shape.title.files = [file];
const mutations = [];
let work = {
	queued: [{ run: 'test', path: file.path, expected: 60, position: 24 }],
	active: [],
	pauses: []
};
let viewer = false;
const body = async (request) => {
	let raw = '';
	for await (const chunk of request) raw += chunk;
	return JSON.parse(raw);
};
const { site, close } = await serve(5197, shape, {
	'/api/auth/me': () => ({ name: 'tester', role: viewer ? 'viewer' : 'admin', must_change: false }),
	'/api/library/work': () => work,
	'/api/queue': async (request) => {
		const data = await body(request);
		mutations.push(data);
		if (data.action === 'top') {
			work.queued[0].position = 1;
			return { moved: 1, undo: 'undo' };
		}
		if (data.action === 'undo') {
			work.queued[0].position = 24;
			return { restored: true };
		}
		work.queued = [];
		return { changed: 1 };
	},
	'/api/pauses': async (request) => {
		if (request.method === 'POST') {
			const data = await body(request);
			mutations.push(data);
			work.pauses = [
				{
					path: data.paths?.[0] ?? shape.title.folder,
					seconds: data.seconds,
					until: null,
					by: 'tester',
					reason: '',
					at: '',
					title: data.ids?.[0] ?? '',
					name: ''
				}
			];
		}
		return { pauses: work.pauses };
	},
	'/api/runs/skip': async (request) => {
		const data = await body(request);
		mutations.push(data);
		work.active = work.active.filter((item) => item.path !== data.path);
		return {};
	},
	'/api/pauses/resume': () => {
		work.pauses = [];
		return { pauses: [] };
	}
});
const browser = await playwright.chromium.launch();
try {
	const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
	const errors = [];
	page.on('pageerror', (e) => errors.push(e.message));
	await page.goto(`${site}/library`);
	await page
		.getByRole('button', { name: new RegExp(`^${title.name}`) })
		.first()
		.click();
	const sheet = page.getByRole('dialog', { name: title.name, exact: true });
	const row = sheet
		.locator('li')
		.filter({ has: page.getByText(file.name, { exact: true }) })
		.first();
	const controls = sheet.getByRole('group', { name: 'Title controls', exact: true });
	assert.equal(await row.getByRole('button', { name: /Actions for/ }).count(), 0);
	// The header names the file's place, and a reorder answers with the new one.
	const status = sheet.locator('h2 ~ p').nth(1);
	await status.filter({ hasText: /^Queued place 24$/ }).waitFor();
	// One file, so the header carries the verdict and the row does not repeat it.
	// "Failed:" leading the reason below is a different string.
	assert.equal(await row.getByText('Failed', { exact: true }).count(), 0);
	await page.getByRole('button', { name: 'Prioritise', exact: true }).click();
	await status.filter({ hasText: /^Queued place 1$/ }).waitFor();
	assert.equal(work.queued[0].position, 1);
	assert.equal(await controls.getByRole('button', { name: 'Undo', exact: true }).count(), 0);
	// A rule chip opens on the lines that rule ordered. Escape answers the
	// popover before the sheet it stands in.
	const line = 'add 2.0 downmix (from track 1)';
	// In the DOM all along; the popover is what makes it visible.
	assert.equal(await page.getByText(line, { exact: true }).isVisible(), false);
	await row.getByRole('button', { name: 'downmix', exact: true }).click();
	await page.getByText(line, { exact: true }).waitFor();
	await page.keyboard.press('Escape');
	await page.getByText(line, { exact: true }).waitFor({ state: 'hidden' });
	await sheet.waitFor({ state: 'visible' });
	// A track row is somewhere to tap the popover away, so only the pencil at its
	// end opens the editor.
	const pencil = row.getByRole('button', { name: /^Edit tags on / }).first();
	await row.getByRole('button', { name: 'downmix', exact: true }).click();
	await page.getByText(line, { exact: true }).waitFor();
	await sheet.getByText('EAC3 · 5.1 · eng', { exact: true }).click();
	await page.getByText(line, { exact: true }).waitFor({ state: 'hidden' });
	assert.equal(await sheet.getByRole('button', { name: 'Cancel', exact: true }).count(), 0);
	await pencil.click();
	await sheet.getByRole('button', { name: 'Cancel', exact: true }).waitFor();
	await page.keyboard.press('Escape');
	await sheet.getByRole('button', { name: 'Cancel', exact: true }).waitFor({ state: 'detached' });
	await sheet.waitFor({ state: 'visible' });
	await page.getByRole('button', { name: 'Skip', exact: true }).click();
	await controls.getByRole('button', { name: /^Pause / }).waitFor();
	await controls.getByRole('button', { name: /^Pause / }).click();
	await page.getByRole('button', { name: '1 hour', exact: true }).click();
	await sheet.getByText('Paused', { exact: true }).waitFor();
	// A press answers with its own line, so the skip's went as this one started.
	assert.equal(await controls.getByText('Skipped.', { exact: true }).count(), 0);
	await controls.getByRole('button', { name: 'Resume', exact: true }).click();
	await controls.getByRole('button', { name: /^Pause / }).waitFor();
	assert.equal(await sheet.evaluate((el) => el.scrollWidth > el.clientWidth), false);
	await page.keyboard.press('Escape');
	await sheet.waitFor({ state: 'hidden' });
	// A series combines live and queued files, and keeps individual controls.
	title.kind = 'series';
	title.files = 3;
	shape.title.total = 3;
	const second = { ...file, path: `${shape.title.folder}/second.mkv`, name: 'second.mkv' };
	shape.title.files = [file, second];
	const hidden = `${shape.title.folder}/not-loaded.mkv`;
	work.queued = [
		{ run: 'test', path: second.path, expected: 60, position: 12 },
		{ run: 'test', path: hidden, expected: 60, position: 90 }
	];
	work.active = [{ run: 'test', path: file.path, stage: 'working', stopping: false }];
	await page.reload();
	await page
		.getByRole('button', { name: new RegExp(`^${title.name}`) })
		.first()
		.click();
	await status.filter({ hasText: /^1 processing · Queued place 12 · 2 files$/ }).waitFor();
	await row.getByRole('button', { name: /Actions for/ }).waitFor();
	// Equal shares at every width, as the idle row has. These fell back to
	// natural widths from sm up, so the check has to leave the phone.
	await page.setViewportSize({ width: 800, height: 900 });
	const shares = await controls
		.getByRole('button')
		.evaluateAll((nodes) => nodes.map((node) => Math.round(node.getBoundingClientRect().width)));
	assert.equal(shares.length, 3);
	assert.equal(new Set(shares).size, 1, `title controls share the row: ${shares}`);
	await page.setViewportSize({ width: 390, height: 844 });
	await controls.getByRole('button', { name: 'Prioritise all', exact: true }).click();
	assert.deepEqual(
		mutations.at(-1).items.map((item) => item.path),
		[second.path, hidden]
	);
	await controls.getByRole('button', { name: 'Cancel all', exact: true }).click();
	// Nothing left in the queue, so the header goes back to the verdict.
	await status.filter({ hasText: /^Failed$/ }).waitFor();
	assert.equal(work.queued.length, 0);
	assert.equal(work.active.length, 0);
	assert.equal(mutations.at(-1).path, file.path);
	await page.keyboard.press('Escape');
	await sheet.waitFor({ state: 'hidden' });
	viewer = true;
	work.queued = [{ run: 'test', path: file.path, expected: 60, position: 12 }];
	await page.reload();
	await page
		.getByRole('button', { name: new RegExp(`^${title.name}`) })
		.first()
		.click();
	// Still the series, so the header counts as well as places.
	await status.filter({ hasText: /^Queued place 12 · 1 file$/ }).waitFor();
	assert.equal(await row.getByRole('button', { name: /Actions for/ }).count(), 0);
	assert.equal(await controls.getByRole('button').count(), 0);
	assert.deepEqual(errors, []);
	console.log(
		'chromium: title-level movie actions, rule chip popovers, series aggregate status and scoped actions, pause/resume, mobile and viewer access passed'
	);
} finally {
	await browser.close();
	await close();
}
