// Real browser + HTTP measurements against an explicitly configured test instance.
// Configuration (including credentials) stays in an ignored/private JSON file.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import playwright from 'playwright';

const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
const repeats = config.repeats ?? 5;
const maxPages = config.pages ?? 5;
const samples = [];
const browser = await playwright.chromium.launch();
try {
	const page = await browser.newPage({ viewport: { width: 1280, height: 915 } });
	const errors = [];
	page.on('pageerror', (error) => errors.push(error.message));
	const login = await page.request.post(`${config.url}/api/auth/login`, {
		headers: { Origin: config.url },
		data: { username: config.username, password: config.password }
	});
	assert.ok(login.ok(), 'benchmark login failed');
	for (let repeat = 0; repeat <= repeats; repeat++) {
		await page.goto(`${config.url}/library`);
		const card = page.getByRole('button', { name: new RegExp(config.cardLabel) });
		await card.waitFor();
		for (let pages = 1; pages <= maxPages; pages++) {
			await page.evaluate(() => performance.clearResourceTimings());
			const response = page.waitForResponse((response) => {
				const url = new URL(response.url());
				return (
					url.pathname === '/api/library/title' &&
					url.searchParams.get('id') === config.titleId &&
					Number(url.searchParams.get('pages') ?? 1) === pages
				);
			});
			const control =
				pages === 1 ? card : page.getByRole('button', { name: 'Load more files', exact: true });
			await control.scrollIntoViewIfNeeded();
			const start = await page.evaluate(() => performance.now());
			await control.click();
			const answer = await response;
			assert.ok(answer.ok(), 'title request failed');
			await answer.finished();
			const body = await answer.json();
			assert.equal(body.id, config.titleId);
			await page.getByRole('dialog').getByText(body.folder, { exact: true }).waitFor();
			await page.evaluate(
				() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
			);
			const timing = await page.evaluate((start) => {
				const request = performance
					.getEntriesByType('resource')
					.find((entry) => new URL(entry.name).pathname === '/api/library/title');
				return {
					click_to_settled_ms: performance.now() - start,
					request_ms: request.duration,
					ttfb_ms: request.responseStart - request.requestStart,
					transfer_bytes: request.transferSize,
					encoded_body_bytes: request.encodedBodySize,
					decoded_body_bytes: request.decodedBodySize
				};
			}, start);
			if (repeat) samples.push({ repeat, pages, files: body.files.length, ...timing });
		}
	}
	assert.deepEqual(errors, []);
	const median = (values) => {
		const ordered = values.toSorted((a, b) => a - b);
		const middle = Math.floor(ordered.length / 2);
		return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
	};
	const results = Array.from({ length: maxPages }, (_, index) => {
		const rows = samples.filter((row) => row.pages === index + 1);
		return {
			pages: index + 1,
			files: rows[0].files,
			...Object.fromEntries(
				Object.keys(rows[0])
					.filter((key) => !['repeat', 'pages', 'files'].includes(key))
					.map((key) => [key, Math.round(median(rows.map((row) => row[key])) * 100) / 100])
			)
		};
	});
	console.log(
		JSON.stringify(
			{ browser: browser.version(), viewport: '1280x915', repeats, results, samples },
			null,
			2
		)
	);
	await page.request.post(`${config.url}/api/auth/logout`, {
		headers: { Origin: config.url },
		data: {}
	});
} finally {
	await browser.close();
}
