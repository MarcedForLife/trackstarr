import assert from 'node:assert/strict';

export async function readingRegressions(
	page,
	{ site, card, folder, remote, files, state, requests, width, engine }
) {
	await page.goto(`${site}/library`);
	state.failMore = false;
	state.addSeason = true;
	await page
		.getByRole('button', { name: new RegExp(`^${card.name}`) })
		.first()
		.click();
	const sheet = page.getByRole('dialog', { name: card.name, exact: true });
	const selection = sheet.getByRole('radiogroup', { name: 'File variant for S01E01', exact: true });
	await selection.locator(`button[value="${files[1].path}"]`).click();
	await sheet.getByRole('button', { name: 'Load more files', exact: true }).click();
	const season = sheet.getByRole('button', { name: /^Season 1/ });
	await season.click();
	await selection.waitFor();

	// One title, two folders: the header names both, and a file's choices lead
	// with the instance holding it.
	await sheet.getByLabel('Show full path for Sonarr', { exact: true }).click();
	await sheet.getByText(folder, { exact: true }).waitFor();
	await sheet.getByLabel('Show full path for Sonarr remote', { exact: true }).click();
	await sheet.getByText(remote, { exact: true }).waitFor();
	assert.match(
		await selection.getByRole('radio').nth(1).getAttribute('title'),
		/^Sonarr remote · 2160p/,
		'a copy from another instance says whose it is'
	);
	const last = sheet.getByRole('radiogroup', { name: 'File variant for S01E15', exact: true });
	// Scroll through the first twelve groups to trigger rendering the rest.
	await sheet
		.getByRole('radiogroup', { name: 'File variant for S01E12', exact: true })
		.scrollIntoViewIfNeeded();
	await last.scrollIntoViewIfNeeded();
	await last.locator(`button[value="${files[29].path}"]`).click();
	const otherSeason = sheet.getByRole('button', { name: /^Season 2/ });
	await otherSeason.click();
	// A refresh keeps the loaded depth, the open seasons and a deep selection.
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await last.waitFor();
	assert.equal(requests.at(-1), 2, 'refresh keeps page depth');
	assert.equal(await season.getAttribute('aria-expanded'), 'true');
	assert.equal(await otherSeason.getAttribute('aria-expanded'), 'false');
	assert.equal(
		await last.locator('[aria-checked="true"]').getAttribute('value'),
		files[29].path,
		'deep selection is kept'
	);

	if (width === 390) await page.screenshot({ path: `/tmp/trackstarr-copy-review-${engine}.png` });
	await page.keyboard.press('Escape');
	await sheet.waitFor({ state: 'hidden' });
	state.reverse = false;
	// A failed open offers a retry, which reads from the first page again.
	state.failRefresh = true;
	await page
		.getByRole('button', { name: new RegExp(`^${card.name}`) })
		.first()
		.click();
	await sheet.getByRole('button', { name: 'Retry loading title' }).waitFor();
	state.failRefresh = false;
	await sheet.getByRole('button', { name: 'Retry loading title' }).click();
	await selection.waitFor();
	assert.equal(requests.at(-1), 1, 'closing clears remembered page depth');
	await selection.locator(`button[value="${files[1].path}"]`).click();
	await sheet.getByRole('button', { name: 'Load more files', exact: true }).click();
	await season.click();
	await selection.waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'loading another season preserves selection'
	);
	await page.keyboard.press('Escape');
	state.emptyTitle = true;
	state.addSeason = false;
	await sheet.waitFor({ state: 'hidden' });
	await page
		.getByRole('button', { name: new RegExp(`^${card.name}`) })
		.first()
		.click();
	await sheet.getByText(/No sweep has walked/).waitFor();
	state.failRefresh = true;
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await sheet.getByRole('button', { name: 'Retry verdict refresh' }).waitFor();
	state.failRefresh = false;
	state.emptyTitle = false;
	await sheet.getByRole('button', { name: 'Retry verdict refresh' }).click();
	await selection.waitFor();
	assert.equal(requests.at(-1), 1, 'closing clears remembered page depth');
	await page.keyboard.press('Escape');
}
