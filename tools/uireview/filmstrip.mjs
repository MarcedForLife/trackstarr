// Watch an animation, one frame per tile.
//
//   node tools/uireview/filmstrip.mjs /settings/connections 'button[aria-controls="radarr-fields"]'
//   node tools/uireview/filmstrip.mjs /events 'button[aria-controls^="event-"]'
//
// Clicks the selector and tiles what the compositor put on screen into a sheet
// in OUT. `page.screenshot()` finishes a running animation before it captures,
// so it can only ever show the end state; CDP's screencast hands over the
// frames themselves. Dropping frames and not animating at all read the same in
// a number and have opposite fixes, and sixteen tiles tell them apart.
import { chromium } from 'playwright';
import { contactSheet, login, num, outDir, phone, screencast, viewport } from './harness.mjs';

const base = process.env.BASE ?? 'http://localhost:5180';
const out = outDir();
const path = process.argv[2] ?? '/settings/connections';
const target = process.argv[3] ?? 'button[aria-controls="radarr-fields"]';
const name = process.env.NAME ?? 'filmstrip';
// How long to keep recording after the click.
const hold = num('WINDOW', 600);
const tiles = num('TILES', 16);
const cols = num('COLS', 4);
// Rows below the control, so the strip frames what opens rather than the page.
const depth = num('DEPTH', 300);

const browser = await chromium.launch();
const context = await phone(browser, { scale: 1 });
const page = await context.newPage();

await page.goto(base + path, { waitUntil: 'networkidle' });
await login(page, base, path);
await page.waitForTimeout(2000);

const control = page.locator(target).first();
await control.waitFor({ timeout: 20000 });
const box = await control.boundingBox();
await control.scrollIntoViewIfNeeded();

const cdp = await context.newCDPSession(page);
if (process.env.THROTTLE) {
	await cdp.send('Emulation.setCPUThrottlingRate', { rate: num('THROTTLE', 4) });
}

const frames = screencast(cdp);
await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 85, everyNthFrame: 1 });
await page.waitForTimeout(120);
const before = frames.length;
await control.click();
await page.waitForTimeout(hold);
await cdp.send('Page.stopScreencast');

// From the click, plus one frame before it as a baseline.
const run = frames.slice(Math.max(0, before - 1));
if (!run.length) {
	console.log('no frames captured');
	await browser.close();
	process.exit(1);
}

const crop = [
	Math.round(Math.min(box.width + 16, viewport.width)),
	Math.round(depth),
	Math.round(Math.max(0, box.x - 8)),
	Math.round(Math.max(0, box.y - 8))
].join(':');

const { sheet, count } = contactSheet({ frames: run, out, name, crop, tiles, cols });

const span = run.at(-1).at - run[0].at;
console.log(
	`${sheet}\n${run.length} frames over ${Math.round(span * 1000)}ms, ` +
		`${count} tiled left to right, top to bottom`
);

await browser.close();
