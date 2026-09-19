// Run after `npm run build:demo`. Exercises the optional board through its UI.
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const { site, close } = await serve(5196, await fixtures());

async function expectFocus(page, locator) {
	assert.ok(
		await locator.evaluate((node) => node === document.activeElement),
		'the card just added starts in its address'
	);
}
try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch();
		try {
			for (const width of [390, 1280]) {
				const page = await browser.newPage({ viewport: { width, height: 915 } });
				const errors = [];
				page.on('pageerror', (error) => errors.push(error.message));
				await page.goto(`${site}/library`);
				await page
					.getByRole('button', { name: /^Big Buck Bunny/ })
					.first()
					.click();
				const single = page.getByRole('dialog', { name: 'Big Buck Bunny', exact: true });
				await single
					.getByLabel('Show full path for Big Buck Bunny (2008)', { exact: true })
					.click();
				await single
					.getByText('/data/media/movies/Big Buck Bunny (2008)', { exact: true })
					.waitFor();
				assert.equal(
					await single.getByRole('combobox').count(),
					0,
					'single-copy view stays simple'
				);
				await page.keyboard.press('Escape');
				await single.waitFor({ state: 'hidden' });

				await page.getByRole('button', { name: 'Debug', exact: true }).click();
				await page.getByRole('button', { name: 'Multiple variants', exact: true }).click();
				await page.getByText(/held by a 4K instance as well/).waitFor();
				await page
					.getByRole('button', { name: /^Big Buck Bunny/ })
					.first()
					.click();
				const sheet = page.getByRole('dialog', { name: 'Big Buck Bunny', exact: true });
				// One title: the header names both folders, and one selector holds
				// every copy, each saying which instance holds it.
				await sheet.getByLabel('Show full path for Radarr', { exact: true }).click();
				await sheet.getByLabel('Show full path for Radarr 4k', { exact: true }).click();
				await sheet
					.getByText('/data/media/movies/Big Buck Bunny (2008)', { exact: true })
					.waitFor();
				await sheet
					.getByText('/data/media/4k/movies/Big Buck Bunny (2008)', { exact: true })
					.waitFor();
				assert.equal(await sheet.getByRole('combobox', { name: 'Library copy' }).count(), 0);
				await sheet.getByText('Applies to every variant of this title.', { exact: true }).waitFor();
				const files = sheet.getByRole('radiogroup', { name: 'File variant for Film' });
				await files.waitFor();
				assert.equal(await files.getByRole('radio').count(), 3);
				const options = await files
					.getByRole('radio')
					.evaluateAll((options) =>
						options.map((option) => ({ value: option.value, label: option.title }))
					);
				const upgrade = options.find((option) => option.label.startsWith('Radarr 4k · '));
				assert.ok(upgrade, 'the 4K copy leads with its instance');
				await files.locator(`button[value="${upgrade.value}"]`).click();
				assert.equal(
					await files.getByRole('radio', { checked: true }).getAttribute('value'),
					upgrade.value
				);
				await sheet
					.getByLabel(`Show full path for ${upgrade.value.split('/').at(-1)}`, { exact: true })
					.click();
				await sheet
					.getByText(
						`/data/media/4k/movies/Big Buck Bunny (2008)/${upgrade.value.split('/').at(-1)}`,
						{
							exact: true
						}
					)
					.waitFor();
				await page.keyboard.press('Escape');
				await sheet.waitFor({ state: 'hidden' });
				await page
					.getByText(/2 variants/)
					.first()
					.waitFor();
				await page
					.getByRole('button', { name: /^Big Buck Bunny/ })
					.first()
					.click();
				await files.waitFor();
				await sheet.evaluate(async (el) => {
					await Promise.allSettled(el.getAnimations().map((animation) => animation.finished));
				});
				await page.screenshot({ path: `/tmp/trackstarr-copies-${engine}-${width}.png` });
				assert.ok(
					await sheet.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
					'no horizontal overflow'
				);
				await page.keyboard.press('Escape');
				await sheet.waitFor({ state: 'hidden' });
				await page
					.getByRole('button', { name: /^Sherlock Holmes/ })
					.first()
					.click();
				const series = page.getByRole('dialog', { name: 'Sherlock Holmes', exact: true });
				const episode = series.getByRole('radiogroup', { name: 'File variant for S01E01' });
				await episode.waitFor();
				assert.equal(await episode.getByRole('radio').count(), 3);
				const labels = await episode
					.getByRole('radio')
					.evaluateAll((options) => options.map((option) => option.title));
				assert.equal(labels.filter((label) => label.startsWith('Sonarr 4k · ')).length, 1);
				await episode.getByRole('radio').nth(1).click();
				assert.ok(
					(await episode.getByRole('radio', { checked: true }).getAttribute('value')).includes(
						'alternate'
					)
				);
				await page.keyboard.press('Escape');
				await series.waitFor({ state: 'hidden' });
				await page.goto(`${site}/settings/connections`);
				await page.getByRole('button', { name: 'New connection', exact: true }).click();
				await page.getByRole('button', { name: 'Radarr', exact: true }).click();
				// The new card opens under the next free number, then its keys follow
				// the name typed.
				const address = page.getByRole('textbox', { name: 'Radarr 2 address', exact: true });
				await expectFocus(page, address);
				await address.fill('http://radarr-test:7878');
				await page.getByLabel('Radarr 2 api key', { exact: true }).fill('demo-key');
				await page.getByRole('textbox', { name: 'Radarr 2 name', exact: true }).fill('Test shelf');
				await page.getByText(/Its keys, RADARR_TESTSHELF_\*, follow it until saved/).waitFor();
				await page.getByRole('button', { name: 'Save', exact: true }).click();
				await page.getByRole('button', { name: 'Save', exact: true }).waitFor({ state: 'hidden' });
				await page.getByText('Optional. Shown instead of Radarr testshelf.').waitFor();
				// A rename after the save changes the label alone.
				await page.getByRole('textbox', { name: 'Test shelf name', exact: true }).fill('UHD');
				await page.getByText('Optional. Shown instead of Radarr testshelf.').waitFor();
				await page.getByRole('button', { name: 'Save', exact: true }).click();
				await page.getByRole('button', { name: 'Save', exact: true }).waitFor({ state: 'hidden' });
				await page.getByRole('button', { name: 'Remove UHD', exact: true }).click();
				await page
					.getByText(
						'Will be removed on save. Imports from this connection will no longer be accepted.'
					)
					.waitFor();
				await page.getByRole('button', { name: 'Undo removal of UHD', exact: true }).click();
				await page.getByRole('button', { name: /^UHD/ }).click();
				await page.getByRole('button', { name: 'Remove UHD', exact: true }).waitFor();
				await page.getByRole('button', { name: 'Save', exact: true }).waitFor({ state: 'hidden' });
				await page.getByRole('button', { name: 'Remove UHD', exact: true }).click();
				await page.getByRole('button', { name: 'Discard', exact: true }).click();
				await page.getByRole('button', { name: /^UHD/ }).click();
				await page.getByRole('button', { name: 'Remove UHD', exact: true }).click();
				if (width === 390) await page.screenshot({ path: `/tmp/trackstarr-removal-${engine}.png` });
				await page.getByRole('button', { name: 'Save', exact: true }).click();
				await page.getByRole('button', { name: 'Save', exact: true }).waitFor({ state: 'hidden' });
				// A media server is one card: its menu row dims once it has one.
				await page.getByRole('button', { name: 'New connection', exact: true }).click();
				assert.ok(
					await page
						.getByRole('group', { name: 'New connection', exact: true })
						.getByRole('button', { name: /^Plex/ })
						.isDisabled()
				);
				await page.keyboard.press('Escape');
				assert.deepEqual(errors, []);
				console.log(
					`${engine} ${width}: one title per film, variants by instance, no overflow or runtime errors`
				);
				await page.close();
			}
		} finally {
			await browser.close();
		}
	}
} finally {
	await close();
}
