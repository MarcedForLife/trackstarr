import assert from 'node:assert/strict';

export async function selectionRegressions(page, { site, card, files, state, requests }) {
	await page.goto(`${site}/library`);
	await page.getByText('1 shared folder claimed by multiple instances').click();
	await page.getByText('Owned by Sonarr; also claimed by Sonarr remote.').waitFor();
	state.failRefresh = true;
	await page
		.getByRole('button', { name: new RegExp(`^${card.name}`) })
		.first()
		.click();
	const sheet = page.getByRole('dialog', { name: card.name, exact: true });
	await sheet.getByText('Could not read that title.', { exact: false }).waitFor();
	state.failRefresh = false;
	await sheet.getByRole('button', { name: 'Retry loading title' }).click();
	const selection = sheet.getByRole('radiogroup', {
		name: 'File variant for S01E01',
		exact: true
	});
	await selection.locator(`button[value="${files[1].path}"]`).click();
	const group = selection.locator('xpath=ancestor::li[1]');
	await group.getByText('1 variant needs attention.', { exact: true }).waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path
	);
	// Move a complete selected episode beyond the first page while editing.
	await group
		.getByRole('button', { name: /^Edit tags on/ })
		.first()
		.click();
	await group.getByRole('button', { name: 'Cancel', exact: true }).waitFor();
	state.moveGroup = true;
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await selection.waitFor({ state: 'hidden' });
	state.moveGroup = false;
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await selection.waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'returning group retains selection'
	);
	assert.equal(
		await group.getByRole('button', { name: 'Cancel', exact: true }).count(),
		0,
		'editor closes when its group leaves the loaded prefix'
	);
	const load = sheet.getByRole('button', { name: 'Load more files', exact: true });
	await load.click();
	await sheet.getByText('Could not load more files. Try again.').waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'failed loading preserves selection'
	);
	state.failMore = false;
	await load.click();
	// The original locator also disappears when its label becomes Loading….
	// Wait for the control itself to leave before triggering a refresh.
	await sheet
		.getByRole('button', { name: /^(Load more files|Loading…)$/ })
		.waitFor({ state: 'hidden' });
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'loading more preserves selection'
	);
	await sheet
		.getByRole('radiogroup', { name: 'File variant for S01E12', exact: true })
		.scrollIntoViewIfNeeded();
	await sheet
		.getByRole('radiogroup', { name: 'File variant for S01E15', exact: true })
		.scrollIntoViewIfNeeded();
	await group
		.getByRole('button', { name: /^Edit tags on/ })
		.first()
		.click();
	const before = requests.length;
	const untouched = sheet.getByRole('radiogroup', {
		name: 'File variant for S01E02',
		exact: true
	});
	assert.equal(
		await untouched.locator('[aria-checked="true"]').getAttribute('value'),
		files[2].path
	);
	state.reverse = true;
	state.moveGroup = true;
	const refreshed = page.waitForResponse(
		(response) => response.url().includes('/api/library/title') && response.status() === 200
	);
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await refreshed;
	await page.waitForFunction(() =>
		[...document.querySelectorAll('[role="radiogroup"]')].some((group) =>
			group.querySelector('button')?.value.includes('S01E01.2160p')
		)
	);
	assert.equal(
		await untouched.locator('[aria-checked="true"]').getAttribute('value'),
		files[2].path,
		'default selection survives a reordered refresh'
	);
	assert.equal(requests.length > before, true);
	assert.equal(requests.at(-1), 2, 'refresh retains the loaded page depth');
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'verdict refresh preserves selection'
	);
	await group.getByRole('button', { name: 'Cancel', exact: true }).waitFor();
	await group.getByRole('button', { name: 'Cancel', exact: true }).click();
	state.moveGroup = false;
	await group.getByText('1 variant needs attention.', { exact: true }).waitFor();
	assert.ok(await sheet.evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
	state.missingSelected = true;
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await sheet.getByText('The selected file disappeared. Showing the remaining variant.').waitFor();
	state.missingSelected = false;
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await selection.waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[0].path,
		'a returning file does not steal the fallback selection'
	);
	await selection.locator(`button[value="${files[1].path}"]`).click();
	await sheet
		.getByText('The selected file disappeared. Showing another variant.')
		.waitFor({ state: 'hidden' });
	state.failRefresh = true;
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await sheet.getByText('Could not refresh verdicts. Showing the last update.').waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path
	);
	state.failRefresh = false;
	state.addSeason = true;
	await sheet.getByRole('button', { name: 'Retry verdict refresh' }).click();
	await sheet
		.getByText('Could not refresh verdicts. Showing the last update.')
		.waitFor({ state: 'hidden' });
	const season = sheet.getByRole('button', { name: /^Season 1/ });
	await season.click();
	await selection.waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'selection survives introduction of seasons'
	);
	await season.click();
	await selection.waitFor({ state: 'hidden' });
	await season.click();
	await selection.waitFor();
	assert.equal(
		await selection.locator('[aria-checked="true"]').getAttribute('value'),
		files[1].path,
		'selection survives collapsing a season'
	);
}
