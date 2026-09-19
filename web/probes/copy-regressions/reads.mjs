import assert from 'node:assert/strict';

export async function readSessionRegressions(page, { site, shape, card, files, delays }) {
	for (const status of [200, 503]) {
		const held = delays.create();
		let linkReads = 0;
		let titleReads = 0;
		const queued = [{ run: 'current-run', path: files[0].path, position: 1, expected: 10 }];
		await page.route('**/api/library/title**', (route) => {
			titleReads++;
			return route.fulfill({ json: shape.title });
		});
		await page.route('**/api/library/links**', async (route) => {
			const old = ++linkReads === 1;
			const answer = {
				status: old ? status : 200,
				json: {
					links: [
						{
							server: 'plex',
							label: 'Plex',
							url: `https://example.test/${old ? 'old' : 'current'}`
						}
					]
				}
			};
			if (old) await held.respond(route, answer);
			else await route.fulfill(answer);
		});
		await page.route('**/api/library/work**', (route) =>
			route.fulfill({ json: { queued, active: [], pauses: [] } })
		);
		await page.goto(`${site}/library`);
		const sheet = page.getByRole('dialog', { name: card.name, exact: true });
		const open = () =>
			page
				.getByRole('button', { name: new RegExp(`^${card.name}`) })
				.first()
				.click();
		await open();
		await held.started();
		await sheet.getByRole('button', { name: 'Skip all', exact: true }).waitFor();
		// Reopen in the same popstate task that starts the closing slide.
		await page.evaluate(
			(name) =>
				new Promise((resolve) => {
					window.addEventListener(
						'popstate',
						() => {
							const poster = [...document.querySelectorAll('main button')].find((button) =>
								button.getAttribute('aria-label')?.startsWith(name)
							);
							if (!poster) throw new Error('Missing poster');
							poster.click();
							resolve();
						},
						{ once: true }
					);
					document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
				}),
			card.name
		);
		await sheet.locator('a[href="https://example.test/current"]').waitFor();
		await sheet.getByRole('button', { name: 'Skip all', exact: true }).waitFor();
		const lateLinks = page.waitForResponse('**/api/library/links**');
		held.release();
		await (await lateLinks).finished();
		await page.evaluate(
			() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
		);
		assert.equal(
			await sheet.locator('a[href="https://example.test/current"]').count(),
			1,
			'old link success/failure cannot replace current links'
		);

		// An action's queue refresh can overtake an in-flight background poll.
		const heldWork = delays.create();
		let reads = 0;
		await page.route('**/api/library/work**', async (route) => {
			const old = ++reads === 1;
			const answer = { json: { queued: old ? [] : queued, active: [], pauses: [] } };
			if (old) await heldWork.respond(route, answer);
			else await route.fulfill(answer);
		});
		await page.route('**/api/pauses', (route) => route.fulfill({ json: { pauses: [] } }));
		await page.route('**/api/queue', (route) => route.fulfill({ json: { changed: 1 } }));
		await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
		await heldWork.started();
		await sheet.getByRole('button', { name: `Pause ${card.name}`, exact: true }).click();
		const currentWork = page.waitForResponse('**/api/library/work**');
		await page
			.getByRole('group', { name: `Pause ${card.name} for`, exact: true })
			.getByRole('button', { name: 'Until I resume', exact: true })
			.click();
		await (await currentWork).finished();
		await sheet.getByRole('button', { name: `Pause ${card.name}`, exact: true }).waitFor();
		await page.evaluate(
			() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
		);
		const before = titleReads;
		const lateWork = page.waitForResponse('**/api/library/work**');
		heldWork.release();
		await (await lateWork).finished();
		await page.evaluate(
			() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
		);
		assert.equal(titleReads, before, 'stale queue response cannot trigger a title refresh');
		await page.keyboard.press('Escape');
		await sheet.waitFor({ state: 'hidden' });
		for (const path of ['title', 'links', 'work']) await page.unroute(`**/api/library/${path}**`);
		for (const path of ['pauses', 'queue']) await page.unroute(`**/api/${path}`);
	}
}
