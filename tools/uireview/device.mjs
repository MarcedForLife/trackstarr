// The same recording, on the phone that gives the verdict.
//
//   BASE=http://<this-box-on-the-lan>:5190 \
//     node tools/uireview/device.mjs /settings/connections 'button[aria-controls="radarr-fields"]'
//
// Attaches to Chrome on the phone over wireless adb, drives it, and brings back
// a sheet of the frames its compositor produced plus the gaps between them.
// Everything else here is desktop Chromium at phone metrics, which models the
// main thread and nothing else. This is the hardware.
//
// Setup, redone whenever the phone's debugging port changes:
//   adb connect <phone-ip>:<port>
//   adb forward tcp:9222 localabstract:chrome_devtools_remote
//
// BASE has to be this box's LAN address, since the phone cannot reach its
// localhost. The screen has to be on with Chrome in front, or no frames come.
import { chromium } from 'playwright';
import {
	contactSheet,
	login,
	num,
	outDir,
	screencast,
	startGaps,
	stats,
	stopGaps
} from './harness.mjs';

const base = process.env.BASE;
if (!base) {
	console.log('set BASE to this box on the LAN, e.g. BASE=http://192.168.1.10:5190');
	process.exit(1);
}
const cdpEndpoint = process.env.CDP ?? 'http://localhost:9222';
const out = outDir();
const path = process.argv[2] ?? '/settings/connections';
const target = process.argv[3] ?? 'button[aria-controls="radarr-fields"]';
const name = process.env.NAME ?? 'device';
const hold = num('WINDOW', 400);
const tiles = num('TILES', 16);
const cols = num('COLS', 4);
const depth = num('DEPTH', 300);

const browser = await chromium.connectOverCDP(cdpEndpoint);
const context = browser.contexts()[0];
const page = await context.newPage();

await page.goto(base + path, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);
await login(page, base, path, { waitUntil: 'domcontentloaded', submit: 'element', timeout: 25000 });
await page.waitForTimeout(2500);

const control = page.locator(target).first();
await control.waitFor({ timeout: 25000 });
await control.scrollIntoViewIfNeeded();
const box = await control.boundingBox();

// The screencast delivers about 22 frames a second, so a 180ms animation gets
// two or three samples and cannot be told from a snap. SLOW=2000 stretches it
// until the sheet has enough to read. The shape of the curve survives the
// stretch; the feel does not.
if (process.env.SLOW) {
	await page.addStyleTag({
		content: `.reveal > *, .reveal > * > * { animation-duration: ${process.env.SLOW}ms !important }`
	});
}

const cdp = await context.newCDPSession(page);

// A real touch: the ripple listens for pointerdown, and hit-testing on the
// device rejects Playwright's own click here anyway.
async function tap({ x, y, width, height }) {
	const at = [{ x: Math.round(x + width / 2), y: Math.round(y + height / 2) }];
	await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: at });
	await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}

const frames = screencast(cdp);

// Frame gaps read on the device itself, the number that disagrees with this box.
await startGaps(page);

await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 85, everyNthFrame: 1 });
await page.waitForTimeout(150);
const before = frames.length;
await tap(box);
await page.waitForTimeout(hold);
await cdp.send('Page.stopScreencast');
const gaps = await stopGaps(page);

const run = frames.slice(Math.max(0, before - 1));
if (!run.length) {
	console.log('no frames: is the screen on and Chrome in front?');
	await browser.close();
	process.exit(1);
}

// The screencast is scaled to the frame the device sent, so the element box in
// CSS pixels has to be scaled with it before it crops anything.
const width = await page.evaluate(() => innerWidth);
const crop = ([w, h]) => {
	const scale = w / width;
	return [
		Math.round(Math.min((box.width + 16) * scale, w)),
		Math.round(Math.min(depth * scale, h)),
		Math.round(Math.max(0, (box.x - 8) * scale)),
		Math.round(Math.max(0, (box.y - 8) * scale))
	].join(':');
};

const { sheet, count } = contactSheet({ frames: run, out, name, crop, tiles, cols });

const span = run.at(-1).at - run[0].at;
console.log(
	`${sheet}\n${run.length} frames over ${Math.round(span * 1000)}ms, ${count} tiled\n` +
		`device frames: ${stats(gaps)}`
);

await page.close();
await browser.close();
