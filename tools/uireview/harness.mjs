// What the scripts here share: paths, the phone context, login, frame
// sampling, and turning a screencast into a contact sheet.
import { readFileSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

export const num = (name, fallback) => Number(process.env[name] ?? fallback);

// Default under dev/ because dev/ is gitignored. OUT sends the sheets
// somewhere they can be read.
export function outDir() {
	const out = process.env.OUT ?? join(root, 'dev', 'shots');
	mkdirSync(out, { recursive: true });
	return out;
}

// UIREVIEW_USER and UIREVIEW_PASS win, else KEY=value lines in the file named
// by UIREVIEW_ENV: any key holding USER, EMAIL or NAME is the username, any key
// holding PASS is the password.
export function credentials() {
	if (process.env.UIREVIEW_USER && process.env.UIREVIEW_PASS) {
		return { user: process.env.UIREVIEW_USER, pass: process.env.UIREVIEW_PASS };
	}
	const file = process.env.UIREVIEW_ENV ?? join(root, 'dev', 'ui-review.env');
	let text;
	try {
		text = readFileSync(file, 'utf8');
	} catch {
		throw new Error(`no credentials: set UIREVIEW_USER and UIREVIEW_PASS, or write ${file}`);
	}
	const env = Object.fromEntries(
		text
			.split('\n')
			.filter((l) => l.trim() && !l.startsWith('#'))
			.map((l) => {
				const i = l.indexOf('=');
				return [l.slice(0, i).trim(), l.slice(i + 1).trim().replace(/^["']|["']$/g, '')];
			})
	);
	const pick = (re) => env[Object.keys(env).find((k) => re.test(k))];
	return { user: pick(/USER|EMAIL|NAME/), pass: pick(/PASS/) };
}

export const viewport = { width: num('WIDTH', 412), height: num('HEIGHT', 915) };

// Scale 1 for anything about motion: a frame at 2x is four times the bytes to
// shuttle per vsync.
export const phone = (browser, { scale = 2 } = {}) =>
	browser.newContext({ viewport, deviceScaleFactor: scale, isMobile: true, hasTouch: true });

// Fills the login form if the page is showing one, then returns to `path`.
// submit: 'element' is for the device, where the keyboard is up and <main>
// takes the pointer events, so a hit-tested click never lands.
export async function login(
	page,
	base,
	path,
	{ waitUntil = 'networkidle', submit = 'click', timeout = 20000 } = {}
) {
	const field = page.locator('input[autocomplete="username"]');
	if (!(await field.count())) return false;
	const { user, pass } = credentials();
	await field.fill(user);
	await page.fill('input[autocomplete="current-password"]', pass);
	if (submit === 'element') await page.locator('form button').evaluate((el) => el.click());
	else await page.click('form button');
	await page.waitForURL((u) => !u.pathname.includes('login'), { timeout });
	await page.goto(base + path, { waitUntil });
	return true;
}

// Gaps between animation frames. 16.7ms is the browser keeping up; past ~20 is
// a frame that did not make it.
export const startGaps = (page) =>
	page.evaluate(() => {
		window.__gaps = [];
		let last = performance.now();
		window.__on = true;
		const tick = (t) => {
			window.__gaps.push(t - last);
			last = t;
			if (window.__on) requestAnimationFrame(tick);
		};
		requestAnimationFrame(tick);
	});

// The first gap spans from before the click, and the sampler's own start.
export const stopGaps = async (page) =>
	(await page.evaluate(() => ((window.__on = false), window.__gaps))).slice(1);

export function stats(gaps) {
	const sorted = [...gaps].sort((a, b) => a - b);
	const at = (q) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))]?.toFixed(1);
	const over = (n) => sorted.filter((g) => g > n).length;
	return (
		`${sorted.length} frames  median ${at(0.5)}ms  p95 ${at(0.95)}ms  ` +
		`worst ${sorted.at(-1)?.toFixed(1)}ms  | over 20ms: ${over(20)}  over 32ms: ${over(32)}`
	);
}

// Unacked frames stop the stream, so this answers every one even while the
// page is busy.
export function screencast(cdp) {
	const frames = [];
	cdp.on('Page.screencastFrame', async ({ data, sessionId, metadata }) => {
		frames.push({ data, at: metadata.timestamp });
		await cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
	});
	return frames;
}

export const frameSize = (file) =>
	execFileSync('ffprobe', [
		'-v',
		'error',
		'-select_streams',
		'v:0',
		'-show_entries',
		'stream=width,height',
		'-of',
		'csv=p=0',
		file
	])
		.toString()
		.trim()
		.split(',')
		.map(Number);

// `crop` is ffmpeg's w:h:x:y in the frames' own pixels, or a function given the
// first frame's [width, height]: the device scales its screencast to whatever
// it sent, and that size is only knowable once a frame lands.
export function contactSheet({ frames, out, name, crop, tiles = 16, cols = 4 }) {
	const dir = join(out, 'frames');
	rmSync(dir, { recursive: true, force: true });
	mkdirSync(dir, { recursive: true });

	// Evenly spaced, so a long window fits the sheet instead of showing only
	// its first sixteenth.
	const step = Math.max(1, Math.floor(frames.length / tiles));
	const picked = frames.filter((_, i) => i % step === 0).slice(0, tiles);
	picked.forEach((f, i) =>
		writeFileSync(join(dir, `f${String(i).padStart(3, '0')}.jpg`), Buffer.from(f.data, 'base64'))
	);

	const box = typeof crop === 'function' ? crop(frameSize(join(dir, 'f000.jpg'))) : crop;
	const sheet = join(out, `${name}.png`);
	execFileSync(
		'ffmpeg',
		[
			'-y',
			'-loglevel',
			'error',
			'-framerate',
			'1',
			'-i',
			join(dir, 'f%03d.jpg'),
			'-frames:v',
			'1',
			'-filter_complex',
			`crop=${box},scale=iw/2:ih/2,` +
				`tile=${cols}x${Math.ceil(picked.length / cols)}:padding=6:margin=6:color=0x202020`,
			sheet
		],
		{ stdio: 'inherit' }
	);
	return { sheet, count: picked.length };
}
