import assert from 'node:assert/strict';

export async function pauseRegressions(page, { site, card, folder, remote, delays }) {
	for (const staleStatus of [200, 503]) {
		const heldRead = delays.create();
		let pauses = [];
		let failPause = true;
		await page.route('**/api/library/work**', (route) =>
			route.fulfill({ json: { queued: [], active: [], pauses } })
		);
		await page.route('**/api/pauses', async (route) => {
			if (route.request().method() === 'GET') {
				await heldRead.respond(route, { status: staleStatus, json: { pauses: [] } });
			} else if (failPause) {
				await route.fulfill({ status: 503, json: { status: 'pause temporarily unavailable' } });
			} else {
				assert.deepEqual(route.request().postDataJSON().ids, [card.id]);
				// A title pause holds every folder the title lives in.
				pauses = [folder, remote].map((path) => ({
					path,
					seconds: null,
					until: null,
					by: 'admin',
					reason: '',
					at: new Date().toISOString(),
					title: card.id,
					name: card.name
				}));
				await route.fulfill({ json: { pauses } });
			}
		});
		await page.goto(`${site}/library`);
		await page
			.getByRole('button', { name: new RegExp(`^${card.name}`) })
			.first()
			.click();
		await heldRead.started();
		const sheet = page.getByRole('dialog', { name: card.name, exact: true });
		await sheet.getByRole('button', { name: `Pause ${card.name}`, exact: true }).click();
		const menu = page.getByRole('group', { name: `Pause ${card.name} for`, exact: true });
		await menu.getByRole('button', { name: 'Until I resume', exact: true }).click();
		await menu.getByRole('alert').getByText('Pause temporarily unavailable.').waitFor();
		failPause = false;
		await menu.getByRole('button', { name: 'Until I resume', exact: true }).click();
		await sheet.getByRole('button', { name: 'Resume', exact: true }).waitFor();
		const late = page.waitForResponse(
			(response) => response.url().endsWith('/api/pauses') && response.request().method() === 'GET'
		);
		heldRead.release();
		await (await late).finished();
		await page.evaluate(() => new Promise(requestAnimationFrame));
		assert.equal(
			await sheet.getByRole('button', { name: 'Resume', exact: true }).count(),
			1,
			`late pause lookup (${staleStatus}) must preserve the newer pause`
		);
		await page.unroute('**/api/pauses');
		await page.unroute('**/api/library/work**');
	}
}
