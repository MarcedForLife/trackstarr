import assert from 'node:assert/strict';

export async function mappingRegressions(page, { site, shape }) {
	await page.goto(`${site}/settings/connections`);
	let saved = false;
	const snapshot = structuredClone(shape.settings);
	await page.route('**/api/connections/test', (route) =>
		route.fulfill({
			json: {
				ok: true,
				detail: 'Plex answered',
				hint: saved ? 'Saved mapping checked' : 'Old mapping warning',
				webhook: '',
				webhook_detail: ''
			}
		})
	);
	await page.route('**/api/settings', async (route) => {
		if (route.request().method() === 'GET') {
			await route.fulfill({ json: snapshot });
			return;
		}
		saved = true;
		snapshot.settings.PLEX_PATH_MAP.value = ['/data/media=/srv/media'];
		await route.fulfill({ json: snapshot });
	});
	const card = page.getByRole('button', { name: /^Plex / });
	await card.click();
	await page.getByRole('button', { name: 'Test Plex', exact: true }).click();
	await page.getByText('Old mapping warning', { exact: true }).waitFor();
	await page.getByRole('button', { name: '+ Add a pair', exact: true }).click();
	await page.getByRole('textbox', { name: 'Our path, pair 1', exact: true }).fill('/data/media');
	await page.getByRole('textbox', { name: "Plex's path, pair 1", exact: true }).fill('/srv/media');
	await card.getByText('Configured', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Test Plex', exact: true }).click();
	await page
		.getByText('Path checks use the saved mapping. Save your changes to test the new mapping.')
		.waitFor();
	const retest = page.waitForRequest(
		(request) =>
			request.url().endsWith('/api/connections/test') && request.postDataJSON().service === 'plex'
	);
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await retest;
	await page.getByText('Saved mapping checked', { exact: true }).waitFor();
	assert.equal(await page.getByText('Old mapping warning', { exact: true }).count(), 0);
	await page.unroute('**/api/connections/test');
}
