import assert from 'node:assert/strict';

export async function actionSessionRegressions(
	page,
	{ site, shape, card, folder, other, delays },
	perFile = false
) {
	for (const [sameTitle, staleStatus] of [
		[false, 200],
		[true, 200],
		[false, 503],
		[true, 503]
	]) {
		const next = sameTitle ? card : other;
		const targets = [
			{
				run: 'original-run',
				path: perFile ? shape.title.files[0].path : `${folder}/queued.mkv`,
				position: 1,
				expected: 10
			},
			{
				run: 'next-run',
				path: perFile ? shape.title.files[0].path : '/remote/queued.mkv',
				position: 1,
				expected: 10
			}
		];
		const active = {
			run: 'original-run',
			path: perFile ? shape.title.files[0].path : `${folder}/active.mkv`,
			stage: 'working',
			seconds: 1,
			duration: 10,
			done: 0,
			speed: 0,
			stopping: false
		};
		let visit = 0;
		const first = delays.create();
		const second = delays.create();
		const cancellations = [];
		await page.route('**/api/library', (route) =>
			route.fulfill({ json: { ...shape.library, titles: sameTitle ? [card] : [card, next] } })
		);
		await page.route('**/api/library/title**', (route) => {
			const id = new URL(route.request().url()).searchParams.get('id');
			return route.fulfill({
				json: { ...shape.title, id, name: id === card.id ? card.name : next.name }
			});
		});
		await page.route('**/api/library/work**', (route) =>
			route.fulfill({
				json: { queued: [targets[visit]], active: visit ? [] : [active], pauses: [] }
			})
		);
		await page.route('**/api/pauses', async (route) => {
			if (route.request().method() === 'GET') return route.fulfill({ json: { pauses: [] } });
			const original = visit === 0;
			assert.deepEqual(route.request().postDataJSON()[perFile ? 'paths' : 'ids'], [
				perFile ? shape.title.files[0].path : original ? card.id : next.id
			]);
			await (original ? first : second).respond(route, {
				status: original ? staleStatus : 200,
				json: original && staleStatus === 503 ? { status: 'old pause failed' } : { pauses: [] }
			});
		});
		await page.route('**/api/queue', (route) => {
			cancellations.push(route.request().postDataJSON());
			return route.fulfill({ json: { changed: 1 } });
		});
		await page.route('**/api/runs/skip', (route) => {
			cancellations.push(route.request().postDataJSON());
			return route.fulfill({ json: { where: 'active', rewrites: 1 } });
		});
		await page.goto(`${site}/library`);
		async function startPause(title) {
			await page
				.getByRole('button', { name: new RegExp(`^${title.name}`) })
				.first()
				.click();
			const sheet = page.getByRole('dialog', { name: title.name, exact: true });
			if (perFile) {
				await sheet
					.getByRole('button', { name: /^Actions for / })
					.first()
					.click();
				await page
					.getByRole('group', { name: /^Actions for / })
					.getByRole('button', { name: 'Pause…', exact: true })
					.click();
			} else await sheet.getByRole('button', { name: `Pause ${title.name}`, exact: true }).click();
			await page
				.getByRole(
					'group',
					perFile ? { name: /^Actions for / } : { name: `Pause ${title.name} for`, exact: true }
				)
				.getByRole('button', { name: 'Until I resume', exact: true })
				.click();
			await (visit === 0 ? first : second).started();
		}
		await startPause(card);
		// Escape dismisses the menu first, then the sheet.
		await page.keyboard.press('Escape');
		await page.keyboard.press('Escape');
		await page.getByRole('dialog', { name: card.name, exact: true }).waitFor({ state: 'hidden' });
		visit = 1;
		await startPause(next);
		const skipped =
			staleStatus === 200
				? page.waitForResponse('**/api/runs/skip')
				: page.waitForResponse(
						(r) => r.url().endsWith('/api/pauses') && r.request().method() === 'POST'
					);
		first.release();
		await (await skipped).finished();
		await page.evaluate(() => new Promise(requestAnimationFrame));
		assert.deepEqual(
			cancellations,
			staleStatus === 200
				? [
						{ action: 'skip', items: [targets[0]] },
						{ run: active.run, path: active.path }
					]
				: []
		);
		assert.equal(await page.getByText('Old pause failed.', { exact: true }).count(), 0);
		assert.equal(
			await page.getByRole('button', { name: 'Pausing…', exact: true }).isDisabled(),
			true
		);
		const nextSkipped = page.waitForResponse('**/api/queue');
		second.release();
		await (await nextSkipped).finished();
		assert.deepEqual(cancellations.at(-1), { action: 'skip', items: [targets[1]] });
		for (const path of [
			'library',
			'library/title**',
			'library/work**',
			'pauses',
			'queue',
			'runs/skip'
		])
			await page.unroute(`**/api/${path}`);
	}
}
