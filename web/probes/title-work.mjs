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
	await sheet.getByText('Queue position 24', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Move to top', exact: true }).click();
	await sheet.getByText('Queue position 1', { exact: true }).waitFor();
	await controls.getByRole('button', { name: 'Undo', exact: true }).click();
	await sheet.getByText('Queue position 24', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Skip', exact: true }).click();
	await controls.getByRole('button', { name: /^Pause / }).waitFor();
	await controls.getByRole('button', { name: /^Pause / }).click();
	await page.getByRole('button', { name: '1 hour', exact: true }).click();
	await sheet.getByText('Paused', { exact: true }).waitFor();
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
	await sheet.getByText('1 processing · 2 queued · next at position 12', { exact: true }).waitFor();
	await row.getByRole('button', { name: /Actions for/ }).waitFor();
	await controls.getByRole('button', { name: 'Move queued to top', exact: true }).click();
	assert.deepEqual(
		mutations.at(-1).items.map((item) => item.path),
		[second.path, hidden]
	);
	await controls.getByRole('button', { name: 'Cancel all', exact: true }).click();
	await controls.getByText('Cancellation requested.', { exact: true }).waitFor();
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
	await sheet.getByText('Queue position 12', { exact: true }).waitFor();
	assert.equal(await row.getByRole('button', { name: /Actions for/ }).count(), 0);
	assert.equal(await controls.getByRole('button').count(), 0);
	assert.deepEqual(errors, []);
	console.log(
		'chromium: title-level movie actions, series aggregate status and scoped actions, pause/resume, mobile and viewer access passed'
	);
} finally {
	await browser.close();
	await close();
}
