// Measure the frames an expanding event row actually produces.
//
//   node tools/uireview/expand-frames.mjs [label]
//
// Against the *built* app on :5190 with the CPU throttled 4x, the only setting
// here that resembles the phone. A screenshot cannot answer "is it smooth".
import { chromium } from 'playwright';
import { login, num, phone, startGaps, stats, stopGaps } from './harness.mjs';

const base = process.env.BASE ?? 'http://localhost:5190';
const label = process.argv[2] ?? 'current';
const runs = num('RUNS', 5);

const browser = await chromium.launch();
const context = await phone(browser);
const page = await context.newPage();

const cdp = await context.newCDPSession(page);
const rate = num('THROTTLE', 4);
await cdp.send('Emulation.setCPUThrottlingRate', { rate });

await page.goto(base + '/events', { waitUntil: 'networkidle' });
await login(page, base, '/events');

// Another timing without a rebuild, so two curves can be compared in the time
// one build takes: CURVE="240ms cubic-bezier(0.22,1,0.36,1)".
if (process.env.CURVE) {
	await page.addStyleTag({
		content: `.reveal { animation: reveal ${process.env.CURVE} !important }`
	});
}

const rows = page.locator('button[aria-controls^="event-"]');
await rows.first().waitFor({ timeout: 20000 });
console.log(`${await rows.count()} rows on the page, throttled ${rate}x`);

async function sample(ms) {
	await startGaps(page);
	await page.waitForTimeout(ms);
	return stopGaps(page);
}

const all = [];
for (let run = 0; run < runs; run++) {
	// A row a few down from the top, so the whole rest of the list is below it.
	const row = rows.nth(2);
	const started = sample(700);
	await page.waitForTimeout(30);
	await row.click();
	all.push(...(await started));
	await row.click();
	await page.waitForTimeout(200);
}

console.log(`${label}:  ${stats(all)}`);

// The panel's position on screen each frame. If the two layers are cancelling,
// the content never moves and this reads the same number throughout. Any drift
// and the reveal is sliding the contents in again.
const panel = await rows.nth(2).getAttribute('aria-controls');
await page.evaluate((id) => {
	window.__h = [];
	const start = performance.now();
	const tick = () => {
		const box = document.getElementById(id);
		const content = box?.firstElementChild?.firstElementChild;
		window.__h.push([
			box ? Math.round(box.getBoundingClientRect().height) : 0,
			content ? Math.round(content.getBoundingClientRect().top) : 0
		]);
		if (performance.now() - start < 500) requestAnimationFrame(tick);
	};
	requestAnimationFrame(tick);
}, panel);
await rows.nth(2).click();
await page.waitForTimeout(600);
const trace = await page.evaluate(() => window.__h);
const from = Math.max(0, trace.findIndex(([h]) => h > 0));
const run = trace.slice(from, from + 16);
console.log('box height:  ', run.map(([h]) => String(h).padStart(5)).join(''));
console.log('content top: ', run.map(([, y]) => String(y).padStart(5)).join(''));

await browser.close();
