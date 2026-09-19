import assert from 'node:assert/strict';

export async function editorSessionRegressions(page, { site, shape, card, files, delays }) {
	for (const status of [200, 503]) {
		const held = delays.create();
		let titleReads = 0;
		await page.route('**/api/library/title**', (route) => {
			titleReads++;
			return route.fulfill({ json: { ...shape.title, files: [files[0]], total: 1 } });
		});
		await page.route('**/api/library/retag', async (route) => {
			await held.respond(route, {
				status,
				json: { results: [{ path: files[0].path, status: 'retagged' }] }
			});
		});
		try {
			await page.goto(`${site}/library`);
			const poster = page.getByRole('button', { name: new RegExp(`^${card.name}`) }).first();
			const sheet = page.getByRole('dialog', { name: card.name, exact: true });
			await poster.click();
			await sheet
				.getByRole('button', { name: /^Edit tags on/ })
				.first()
				.click();
			await sheet.getByRole('switch').first().click();
			await sheet.getByRole('button', { name: /^Apply/ }).click();
			await held.started();
			await page.keyboard.press('Escape'); // editor
			await page.keyboard.press('Escape'); // sheet
			await sheet.waitFor({ state: 'hidden' });
			await poster.click();
			await sheet
				.getByRole('button', { name: /^Edit tags on/ })
				.first()
				.click();
			await sheet.getByRole('button', { name: 'Cancel', exact: true }).waitFor();
			const before = titleReads;
			const finished = page.waitForResponse('**/api/library/retag');
			held.release();
			await (await finished).finished();
			await page.evaluate(
				() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
			);
			assert.equal(
				await sheet.getByRole('button', { name: 'Cancel', exact: true }).count(),
				1,
				'late edit cannot close a newer editor'
			);
			assert.equal(titleReads, before, 'late edit cannot refresh a later opening');
		} finally {
			held.release();
			await page.unroute('**/api/library/retag');
			await page.unroute('**/api/library/title**');
		}
	}
}
