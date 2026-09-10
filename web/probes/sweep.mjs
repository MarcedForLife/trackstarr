// What a cursor costs over the overview's shelf, on the real bundle.
//
// A stand-in row of divs with the poster's paint stack copied onto them cannot
// be made to drop a frame on an M1 Pro, in either engine, at Retina scale,
// with 120 cards. The cost is in what the stand-in leaves out: SvelteKit, the
// real PosterCard and its actions, the sheet, the feed, the stream. So this
// serves the build behind the stub service with a library of the size asked
// for and sweeps the same cursor over the shelf three ways, reading the frame
// times back. Numbers to judge, not a pass or fail: see README.md for which
// machine's numbers count.
//
//   npm run probe:sweep                   build, then both engines, 400 titles
//   TITLES=1200 ENGINES=firefox node probes/sweep.mjs   a big library, Gecko
//   GESTURES=hover HEADED=1 node probes/sweep.mjs       watch one gesture
//   SERVE=1 node probes/sweep.mjs                       just serve it

import playwright from 'playwright';
import { ENGINES, fixtures, hold, serve } from './stub.mjs';

const TITLES = Number(process.env.TITLES ?? 400);
const STEPS = 90;
const SCALE = Number(process.env.SCALE ?? 2);

// A library of the size asked for, made by cycling the fixture's own cards
// under fresh ids and names with every verdict the service knows: the shape
// stays the contract's, so a renamed key blanks the posters here the way it
// fails the fixture test, rather than being quietly restated.
const shape = await fixtures();
const { titles: cards } = shape.library;
const { states } = shape.vocabulary;
const titles = Array.from({ length: TITLES }, (_, at) => ({
	...cards[at % cards.length],
	id: `t${at}`,
	name: `${cards[at % cards.length].name} ${at}`,
	state: states[at % states.length]
}));
const counts = titles.reduce((tally, one) => {
	tally[one.state] = (tally[one.state] ?? 0) + 1;
	return tally;
}, {});

const { site, close } = await serve(5196, shape, {
	'/api/library': { ...shape.library, titles },
	'/api/library/summary': {
		...shape.summary,
		titles: titles.length,
		counts,
		head: titles.slice(0, 12)
	}
});

if (process.env.SERVE) await hold(site);

function percentile(values, at) {
	const sorted = [...values].sort((one, two) => one - two);
	return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * at))];
}

// The shelf on the overview, found the way a reader finds it.
async function middle(page) {
	return page.evaluate(() => {
		const strip = document.querySelector('ul.strip');
		if (!strip) return null;
		const box = strip.getBoundingClientRect();
		return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
	});
}

async function run(engine, gesture) {
	const browser = await playwright[engine].launch({ headless: !process.env.HEADED });
	const context = await browser.newContext({
		viewport: { width: 1728, height: 1000 },
		deviceScaleFactor: SCALE
	});
	const page = await context.newPage();
	const noise = [];
	page.on('console', (message) => message.type() === 'error' && noise.push(message.text()));
	await page.goto(`${site}/`);
	await page.waitForTimeout(2500);
	const at = await middle(page);
	if (!at) {
		await browser.close();
		return { engine, gesture, cards: 0, note: 'no shelf on the page' };
	}

	const cards = await page.evaluate(() => document.querySelectorAll('[data-tilt]').length);
	await page.evaluate(() => {
		window.frames = [];
		window.watching = false;
		let last = 0;
		const tick = (now) => {
			if (window.watching && last) window.frames.push(now - last);
			last = now;
			requestAnimationFrame(tick);
		};
		requestAnimationFrame(tick);
	});

	await page.mouse.move(at.x, at.y);
	await page.waitForTimeout(300);
	await page.evaluate(() => (window.watching = true));
	if (gesture === 'wheel') {
		// A trackpad pushing the row sideways, where the field is asleep and the
		// browser owns the scroll outright.
		for (let step = 0; step < STEPS; step++) {
			await page.mouse.wheel(14, 0);
			await page.waitForTimeout(8);
		}
	} else if (gesture === 'drag') {
		await page.mouse.down();
		for (let step = 1; step <= STEPS; step++) {
			await page.mouse.move(at.x - step * 6, at.y);
			await page.waitForTimeout(8);
		}
		await page.mouse.up();
	} else {
		for (let step = 1; step <= STEPS; step++) {
			await page.mouse.move(at.x - 400 + step * 9, at.y + Math.sin(step / 8) * 40);
			await page.waitForTimeout(8);
		}
	}
	await page.evaluate(() => (window.watching = false));
	const frames = await page.evaluate(() => window.frames);
	await browser.close();

	return {
		engine,
		gesture,
		cards,
		frames: frames.length,
		medianMs: +percentile(frames, 0.5).toFixed(1),
		p95Ms: +percentile(frames, 0.95).toFixed(1),
		worstMs: +Math.max(...frames).toFixed(1),
		late: frames.filter((one) => one > 20).length,
		dropped: frames.filter((one) => one > 33).length,
		errors: noise.length
	};
}

const rows = [];
for (const engine of ENGINES) {
	for (const gesture of (process.env.GESTURES ?? 'hover,wheel,drag').split(',')) {
		rows.push(await run(engine, gesture));
	}
}
console.table(rows);
close();
