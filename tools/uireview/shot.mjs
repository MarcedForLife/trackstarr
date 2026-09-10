// Screenshot the trackstarr web UI at phone metrics.
//
//   node tools/uireview/shot.mjs /activity /sweep
//   BASE=http://localhost:5190 node tools/uireview/shot.mjs /
//
// Desktop Chromium at phone metrics, so it settles layout, wrapping, tap
// targets, theme and overflow. It says nothing about smoothness.
import { chromium } from 'playwright';
import { join } from 'node:path';
import { login, outDir, phone } from './harness.mjs';

const base = process.env.BASE ?? 'http://localhost:5180';
const paths = process.argv.slice(2).length ? process.argv.slice(2) : ['/'];
const out = outDir();

const browser = await chromium.launch();
const context = await phone(browser);
const page = await context.newPage();
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

for (const path of paths) {
	await page.goto(base + path, { waitUntil: 'networkidle' });
	await login(page, base, path, { timeout: 15000 });

	const file = join(out, `${path.replace(/\W+/g, '-').replace(/^-|-$/g, '') || 'root'}.png`);
	await page.screenshot({ path: file, fullPage: true });

	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth > document.documentElement.clientWidth
	);
	console.log(`${path} -> ${file}${overflow ? '  ⚠ horizontal overflow' : ''}`);
}

if (errors.length) console.log(`console errors:\n  ${errors.join('\n  ')}`);
await browser.close();
