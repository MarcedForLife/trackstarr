// Saved plans survive an upgrade, including plans without rule associations.
import assert from 'node:assert/strict';
import playwright from 'playwright';
import { ENGINES, fixtures, serve } from './stub.mjs';

const shape = await fixtures();
const title = shape.library.titles.find((card) => card.id === shape.title.id);
const file = shape.title.files[0];
shape.title.files = [file];
shape.title.total = 1;
title.files = 1;
title.kind = 'movie';
const { site, close } = await serve(5203, shape, {
	'/api/library/work': { queued: [], active: [], pauses: [] }
});

try {
	for (const engine of ENGINES) {
		const browser = await playwright[engine].launch();
		try {
			for (const unchecked of [false, true]) {
				file.status = unchecked ? 'unchecked' : 'pending';
				file.why = unchecked
					? {}
					: {
							reasons: ['remux to Matroska'],
							incidental: ['clear release title'],
							rules: ['remux'],
							incidental_rules: ['release_tags']
						};
				const page = await browser.newPage();
				await page.goto(`${site}/library`);
				await page
					.getByRole('button', { name: new RegExp(`^${title.name}`) })
					.first()
					.click();
				const sheet = page.getByRole('dialog', { name: title.name, exact: true });
				if (unchecked) {
					await sheet
						.getByText('No plan yet. This file will be checked before processing.')
						.waitFor();
					assert.equal(await sheet.getByText('Nothing to change.', { exact: true }).count(), 0);
				} else {
					await sheet.getByText('remux to Matroska', { exact: true }).waitFor();
					await sheet.getByText('clear release title (with rewrite)', { exact: true }).waitFor();
				}
				await page.close();
			}
			console.log(`${engine}: legacy plan explanations and unchecked file wording passed`);
		} finally {
			await browser.close();
		}
	}
} finally {
	close();
}
