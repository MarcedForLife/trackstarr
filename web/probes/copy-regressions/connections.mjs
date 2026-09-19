import assert from 'node:assert/strict';

export async function connectionRegressions(page, { site, shape, state, delays }) {
	await page.goto(`${site}/settings/connections`);
	const connection = page.getByRole('button', { name: /^Radarr / }).first();
	await connection.getByText('API connected', { exact: true }).waitFor();
	await connection.getByText('Library paths need attention', { exact: true }).waitFor();
	await connection.click();
	await page
		.getByText('Library roots: 1 of 2 visible and inside MEDIA_DIRS.', { exact: false })
		.first()
		.waitFor();
	await page.getByText('/data/media/4k: not visible', { exact: false }).first().waitFor();
	for (const [webhook, badge] of [
		['stale', 'API connected'],
		['unknown', 'API connected'],
		['unreachable', 'No webhook'],
		['connected', 'Connected']
	]) {
		state.webhookState = webhook;
		await page.getByRole('button', { name: 'Test Radarr', exact: true }).click();
		await connection.getByText(badge, { exact: true }).waitFor();
	}
	// Edits invalidate old results, even if the old request finishes last.
	const address = page.getByRole('textbox', { name: 'Radarr address', exact: true });
	const originalAddress = await address.inputValue();
	const key = page.getByLabel('Radarr api key', { exact: true });
	await key.fill('replacement-key');
	await connection.getByText('Configured', { exact: true }).waitFor();
	await key.fill('');
	const old = delays.create();
	let held = false;
	await page.route('**/api/connections/test', async (route) => {
		if (!held) {
			held = true;
			await old.respond(route, {
				json: {
					ok: false,
					detail: 'Old request failed',
					hint: '',
					webhook: '',
					webhook_detail: ''
				}
			});
		} else
			await route.fulfill({
				json: {
					ok: true,
					detail: 'Latest request',
					hint: '',
					webhook: 'connected',
					webhook_detail: ''
				}
			});
	});
	await page.getByRole('button', { name: 'Test Radarr', exact: true }).click();
	await old.started();
	await address.fill('http://new-radarr:7878');
	await connection.getByText('Configured', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Test Radarr', exact: true }).click();
	await connection.getByText('Connected', { exact: true }).waitFor();
	const late = page.waitForResponse('**/api/connections/test');
	old.release();
	await old.finished;
	await (await late).finished();
	await page.evaluate(() => new Promise(requestAnimationFrame));
	assert.equal(await connection.getByText('Connected', { exact: true }).count(), 1);
	await page.unroute('**/api/connections/test');
	await address.fill(originalAddress);
	await page.getByRole('button', { name: 'Test Radarr', exact: true }).click();
	await connection.getByText('Connected', { exact: true }).waitFor();

	// Saving the common callback retests sources even without URL/key changes.
	await page.getByLabel('Webhook address', { exact: true }).fill('http://new-callback:5120');
	await connection.getByText('Configured', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Test Radarr', exact: true }).click();
	await connection.getByText('API connected', { exact: true }).waitFor();
	await page.getByText('Save the webhook address to test the new callback.').first().waitFor();
	await page.route('**/api/settings', async (route) => {
		const snapshot = structuredClone(shape.settings);
		snapshot.settings.WEBHOOK_URL.value = 'http://new-callback:5120';
		await route.fulfill({ json: snapshot });
	});
	const retested = page.waitForRequest('**/api/connections/test');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await retested;
	await connection.getByText('Connected', { exact: true }).waitFor();
	assert.ok(
		await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)
	);
}
