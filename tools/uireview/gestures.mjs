// Check that a poster answers a held press the same way under a thumb and a
// cursor.
//
//   node tools/uireview/gestures.mjs
//   BASE=http://localhost:5190 node tools/uireview/gestures.mjs
//
// The grid gives one gesture two meanings: a short press opens a title, a long
// one picks it. The tuning behind that sits in PosterCard and is the same for
// both pointers, so this is one scenario run twice — a mouse at desktop
// metrics, then real touch events at phone metrics. The two runs printing the
// same thing is what is being checked.
//
// The only script here with a right answer, which is why it says ok or FAIL and
// exits non-zero. Whether 500ms is the right wait is still a human's call.
// Wants an admin account, since a viewer's grid has no picking in it.
import { chromium } from 'playwright';
import { login, phone } from './harness.mjs';

const base = process.env.BASE ?? 'http://localhost:5190';

let failures = 0;
function ok(label, pass) {
	if (!pass) failures += 1;
	console.log(`  ${pass ? 'ok  ' : 'FAIL'}  ${label}`);
}

const centre = async (locator) => {
	const box = await locator.boundingBox();
	return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
};

// The art takes the accent border the moment the press arms, which is the same
// mark it keeps once the grid is picking — so this reads true from half a
// second into the press until the poster is let go.
const armed = (page) =>
	page.evaluate(() => {
		const art = document.querySelector('button.poster span span');
		return !!art && art.className.includes('border-accent');
	});

const picked = async (page) => {
	const bar = page.locator('text=/\\d+ titles? selected/');
	if (!(await bar.count())) return 0;
	return Number(((await bar.first().textContent()) ?? '').match(/\d+/)?.[0] ?? 0);
};

// A shut sheet stays in the DOM so it has something to slide, which leaves its
// presence saying nothing. `inert` is what tells the two apart.
const sheetUp = async (page) =>
	(await page.locator('[role="dialog"]:not([inert])').count()) > 0;

// The two pointers, given the same three verbs. Touch goes through CDP because
// Playwright's own touchscreen only taps, and a tap is the one gesture this
// script is not about.
function cursor(page) {
	return {
		async down(at) {
			await page.mouse.move(at.x, at.y);
			await page.mouse.down();
		},
		to: (at) => page.mouse.move(at.x, at.y, { steps: 8 }),
		up: () => page.mouse.up()
	};
}

function thumb(page, cdp) {
	const send = (type, touchPoints) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints });
	let last = { x: 0, y: 0 };
	return {
		down(at) {
			last = at;
			return send('touchStart', [at]);
		},
		// In steps rather than one jump: a card that has taken the press follows
		// the finger, and it is the travel that carries it off the poster.
		async to(at) {
			for (let step = 1; step <= 8; step += 1) {
				await send('touchMove', [
					{
						x: last.x + ((at.x - last.x) * step) / 8,
						y: last.y + ((at.y - last.y) * step) / 8
					}
				]);
			}
			last = at;
		},
		up: () => send('touchEnd', [])
	};
}

async function scenario(page, hand) {
	const posters = page.locator('button.poster');
	await posters.first().waitFor({ timeout: 20000 });
	const first = posters.first();
	const second = posters.nth(1);

	// Held in place and let go: picked, and the sheet it would otherwise have
	// opened stays down.
	let at = await centre(first);
	await hand.down(at);
	await page.waitForTimeout(250);
	ok('a quarter second in, nothing has armed', !(await armed(page)));
	await page.waitForTimeout(450);
	ok('the tick is up before the press is let go', await armed(page));
	await hand.up(at);
	await page.waitForTimeout(300);
	ok('letting go picked the title', (await picked(page)) === 1);
	ok('and did not also open the sheet', !(await sheetUp(page)));

	await page.keyboard.press('Escape');
	await page.waitForTimeout(400);
	ok('Escape put the picker away', (await picked(page)) === 0);

	// The short version of the same gesture still opens the title.
	at = await centre(first);
	await hand.down(at);
	await page.waitForTimeout(80);
	await hand.up(at);
	await page.waitForTimeout(700);
	ok('a tap opens the sheet', await sheetUp(page));
	await page.keyboard.press('Escape');
	await page.waitForTimeout(500);

	// Carried off the poster: neither. Sliding away is how both a thumb and a
	// cursor let go of a press they have changed their mind about. Drifting
	// about inside the poster is not that and must not count as it, which is
	// why the travel here clears the artwork outright rather than stopping a
	// few pixels past the slop.
	at = await centre(second);
	await hand.down(at);
	await page.waitForTimeout(200);
	const away = { x: at.x, y: at.y + 220 };
	await hand.to(away);
	await page.waitForTimeout(500);
	await hand.up(away);
	await page.waitForTimeout(300);
	ok('a press dragged off the poster picked nothing', (await picked(page)) === 0);
	ok('and opened nothing', !(await sheetUp(page)));

	// Carried off and brought home again. Sliding away abandons a press, so
	// coming back has to un-abandon it: the mark says where the release will
	// land, and a poster the pointer is on again is one it will land on. A thumb
	// has always done this, since the browser hands the whole gesture to the
	// element the touch started on; a cursor is not held that way unless the
	// card asks, and until it did the two pointers disagreed here.
	at = await centre(first);
	await hand.down(at);
	await page.waitForTimeout(700);
	ok('held, the tick is up', await armed(page));
	const off = { x: at.x, y: at.y + 220 };
	await hand.to(off);
	await page.waitForTimeout(250);
	ok('carried off the poster, the tick goes', !(await armed(page)));
	await hand.to(at);
	await page.waitForTimeout(250);
	ok('brought back onto it, the tick returns', await armed(page));
	await hand.up(at);
	await page.waitForTimeout(300);
	ok('and letting go there picked the title', (await picked(page)) === 1);
	ok('without opening the sheet', !(await sheetUp(page)));

	await page.keyboard.press('Escape');
	await page.waitForTimeout(400);
}

async function grid(context) {
	const page = await context.newPage();
	await page.goto(base + '/library', { waitUntil: 'networkidle' });
	await login(page, base, '/library', { timeout: 15000 });
	const role = await page.evaluate(() =>
		fetch('/api/auth/me')
			.then((answer) => answer.json())
			.then((me) => me.role)
	);
	if (role !== 'admin') {
		console.log(`the review account is a ${role}; picking is an admin's, so there is none to check`);
		process.exit(1);
	}
	return page;
}

const browser = await chromium.launch();

console.log('a cursor, at 1600x1000');
const desk = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const deskPage = await grid(desk);
await scenario(deskPage, cursor(deskPage));
await desk.close();

console.log('a thumb, at 412x915');
const hand = await phone(browser, { scale: 1 });
const handPage = await grid(hand);
await scenario(handPage, thumb(handPage, await hand.newCDPSession(handPage)));
await hand.close();

await browser.close();
console.log(failures ? `${failures} failed` : 'both pointers agree');
process.exit(failures ? 1 : 0);
