// What an overlay does to the page underneath it, and what a save tells a
// reader who cannot see the bar go.
//
// Drives the real build behind the stub service: the title sheet, the nav
// drawer and the general settings page. Focus, `inert` and the live regions
// are read back out of the DOM rather than out of the markup we wrote; see
// README.md for why that is the only place they can be read.
//
//   npm run probe                          build, then both engines
//   ENGINES=firefox node probes/focus.mjs  one engine, on the last build
//   SERVE=1 node probes/focus.mjs          just serve it, for a phone
//   ROOT=/tmp/build-before node ...        the same checks over another bundle

import playwright from 'playwright';
import { ENGINES, fixtures, hold, serve } from './stub.mjs';

// What the next POST /api/settings answers, set by the probe over /probe/refuse.
let refusing = false;
const shape = await fixtures();
const { site, close } = await serve(5197, shape, {
	'/probe/refuse': () => {
		refusing = true;
		return { ok: true };
	},
	'/probe/accept': () => {
		refusing = false;
		return { ok: true };
	},
	'/api/settings': (request, response) => {
		if (request.method !== 'POST' || !refusing) return shape.settings;
		response.statusCode = 400;
		return { error: 'invalid settings', problems: ['PROBE_WORKERS must be at least 1'] };
	}
});

const results = [];
function check(name, got, want) {
	const ok = JSON.stringify(got) === JSON.stringify(want);
	results.push({ ok, name, got: JSON.stringify(got), want: JSON.stringify(want) });
}

// Everything the page can say about focus and reach, in one read. Whole rather
// than in pieces because it runs in the browser, where a helper of ours is not
// in scope.
const state = () => {
	const node = document.activeElement;
	const name = () => {
		if (!node || node === document.body) return 'body';
		if (node.getAttribute('role') === 'dialog') return 'dialog';
		if (node.hasAttribute('data-drawer-close')) return 'drawer-close';
		if (node.dataset.probe) return `probe:${node.dataset.probe}`;
		return `${node.tagName.toLowerCase()}[${node.getAttribute('aria-label') ?? ''}]`;
	};
	return {
		active: name(),
		// The sheet's own panel is inert while it is down, which is how a closed
		// one is unreachable rather than absent. What these checks are about is
		// everything else.
		inert: Array.from(document.querySelectorAll('[inert]'))
			.filter((found) => found.getAttribute('role') !== 'dialog')
			.map((found) => `${found.tagName.toLowerCase()}${found.id ? `#${found.id}` : ''}`),
		overflow: document.body.style.overflow,
		// Whether what an overlay came up over can still be tabbed into: the grid
		// under the sheet, and the page under the drawer.
		gridReachable: !document.querySelector('main ul.shelf')?.closest('[inert]'),
		pageReachable: !document.querySelector('main')?.closest('[inert]'),
		// SvelteKit's route announcer, which an overlay must not silence.
		announcerHeard: !document.getElementById('svelte-announcer')?.closest('[inert]')
	};
};

async function sheetChecks(browser, engine) {
	const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
	const page = await context.newPage();
	await page.goto(`${site}/library`);
	await page.waitForSelector('main ul.shelf button', { timeout: 10000 });
	await page.waitForTimeout(600);

	// The poster the reader presses, marked so focus coming back is provable.
	await page.evaluate(() => {
		document.querySelector('main ul.shelf button').dataset.probe = 'poster';
	});
	const before = await page.evaluate(state);
	check(`${engine} sheet: nothing inert before`, before.inert.length, 0);
	check(`${engine} sheet: the grid is reachable before`, before.gridReachable, true);
	check(`${engine} sheet: the body scrolls before`, before.overflow, '');

	await page.click('[data-probe="poster"]');
	await page.waitForTimeout(700);
	const up = await page.evaluate(state);
	check(`${engine} sheet: the panel takes the keyboard`, up.active, 'dialog');
	check(`${engine} sheet: the grid is out of reach`, up.gridReachable, false);
	check(`${engine} sheet: the header is inert`, up.inert.includes('header'), true);
	check(`${engine} sheet: the aside is inert`, up.inert.includes('aside#app-sidebar'), true);
	check(`${engine} sheet: the body is held still`, up.overflow, 'hidden');
	check(`${engine} sheet: the route announcer still speaks`, up.announcerHeard, true);

	// One tab out of the panel: an aria-modal sheet with a live page behind it
	// hands the next stop to the page.
	await page.keyboard.press('Tab');
	const tabbed = await page.evaluate(
		() => !!document.activeElement.closest('[role="dialog"], .fixed.inset-0.z-50')
	);
	check(`${engine} sheet: tab stays over the page`, tabbed, true);

	await page.keyboard.press('Escape');
	await page.waitForTimeout(700);
	const gone = await page.evaluate(state);
	check(`${engine} sheet: focus goes back to the poster`, gone.active, 'probe:poster');
	check(`${engine} sheet: nothing left inert`, gone.inert.length, 0);
	check(`${engine} sheet: the body scrolls again`, gone.overflow, '');
	await context.close();
}

async function drawerChecks(browser, engine) {
	const context = await browser.newContext({ viewport: { width: 412, height: 915 } });
	const page = await context.newPage();
	await page.goto(`${site}/`);
	await page.waitForSelector('[aria-label="Settings and account"]', { timeout: 10000 });
	await page.waitForTimeout(600);
	await page.evaluate(() => {
		document.querySelector('[aria-label="Settings and account"]').dataset.probe = 'gear';
	});

	await page.click('[data-probe="gear"]');
	await page.waitForTimeout(600);
	const open = await page.evaluate(state);
	check(`${engine} drawer: the close button takes the keyboard`, open.active, 'drawer-close');
	check(`${engine} drawer: the page is out of reach`, open.pageReachable, false);
	check(`${engine} drawer: the drawer is not`, open.inert.includes('aside#app-sidebar'), false);
	check(`${engine} drawer: the body is held still`, open.overflow, 'hidden');
	check(`${engine} drawer: the route announcer still speaks`, open.announcerHeard, true);

	await page.keyboard.press('Escape');
	await page.waitForTimeout(600);
	const shut = await page.evaluate(state);
	check(`${engine} drawer: focus goes back to the gear`, shut.active, 'probe:gear');
	check(`${engine} drawer: nothing left inert`, shut.inert.length, 0);
	check(`${engine} drawer: the body scrolls again`, shut.overflow, '');
	await context.close();
}

// The live regions, read as a reader hears them: the words in them, and whether
// the element carrying the words was on the page before they arrived.
const regions = () => {
	const status = document.querySelector('[role="status"]');
	const alert = document.querySelector('[role="alert"]');
	return {
		status: status?.textContent.trim() ?? null,
		statusKept: status?.dataset.probe === 'status',
		alert: alert?.textContent.trim() ?? null,
		alertKept: alert?.dataset.probe === 'alert',
		bar: !!document.querySelector('button.probe-save')
	};
};

async function saveChecks(browser, engine) {
	const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
	const page = await context.newPage();
	await page.goto(`${site}/settings`);
	await page.waitForSelector('input[inputmode="numeric"]', { timeout: 10000 });
	await page.waitForTimeout(600);

	const mark = () => {
		const status = document.querySelector('[role="status"]');
		if (status) status.dataset.probe = 'status';
		const alert = document.querySelector('[role="alert"]');
		if (alert) alert.dataset.probe = 'alert';
		for (const button of document.querySelectorAll('button')) {
			if (button.textContent.trim() === 'Save') button.classList.add('probe-save');
		}
	};

	await page.evaluate(mark);
	const quiet = await page.evaluate(regions);
	check(`${engine} save: a status region before anything is edited`, quiet.status, '');
	check(`${engine} save: no bar before anything is edited`, quiet.bar, false);

	// One edit, which is what raises the bar.
	const bump = async () => {
		const box = page.locator('input[inputmode="numeric"]').first();
		await box.fill('7');
		await box.blur();
		await page.waitForTimeout(300);
		await page.evaluate(mark);
	};
	await bump();
	const edited = await page.evaluate(regions);
	check(`${engine} save: the bar is up on an edit`, edited.bar, true);
	check(`${engine} save: the status is still empty`, edited.status, '');
	check(`${engine} save: the alert region is up empty`, edited.alert, '');
	check(`${engine} save: the status element was already there`, edited.statusKept, true);

	await page.click('button.probe-save');
	await page.waitForTimeout(700);
	const saved = await page.evaluate(regions);
	check(`${engine} save: the bar has gone`, saved.bar, false);
	check(`${engine} save: the save is read out`, saved.status, 'Settings saved.');
	check(`${engine} save: from the element that was already there`, saved.statusKept, true);

	await bump();
	const again = await page.evaluate(regions);
	check(`${engine} save: a fresh edit takes the answer off`, again.status, '');

	// The same press, refused.
	await page.evaluate(() => fetch('/probe/refuse'));
	await page.click('button.probe-save');
	await page.waitForTimeout(700);
	const refused = await page.evaluate(regions);
	check(
		`${engine} save: the refusal is read out`,
		refused.alert,
		'PROBE_WORKERS must be at least 1'
	);
	check(`${engine} save: from the element that was already there`, refused.alertKept, true);
	check(`${engine} save: the bar stayed up`, refused.bar, true);
	check(`${engine} save: nothing says it saved`, refused.status, '');

	await page.evaluate(() => fetch('/probe/accept'));
	await page.evaluate(() => {
		for (const button of document.querySelectorAll('button')) {
			if (button.textContent.trim() === 'Discard') button.classList.add('probe-discard');
		}
	});
	await page.click('button.probe-discard');
	await page.waitForTimeout(500);
	const dropped = await page.evaluate(regions);
	check(`${engine} save: the discard is read out`, dropped.status, 'Changes discarded.');
	await context.close();
}

if (process.env.SERVE) await hold(site);

for (const engine of ENGINES) {
	const browser = await playwright[engine].launch({ headless: !process.env.HEADED });
	try {
		await sheetChecks(browser, engine);
		await drawerChecks(browser, engine);
		await saveChecks(browser, engine);
	} catch (error) {
		results.push({ ok: false, name: `${engine}: probe threw`, got: String(error), want: '' });
	}
	await browser.close();
}

close();
const failed = results.filter((row) => !row.ok);
console.table(results.map(({ ok, name, got, want }) => ({ ok: ok ? '✓' : '✗', name, got, want })));
console.log(`${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
