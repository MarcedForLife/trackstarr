import { expect, test, vi } from 'vitest';

// The service and the poller, held here so a test can decide when an answer
// lands and make the look the stream would have prompted.
const harness = vi.hoisted(() => ({
	asked: [] as { path: string; settle: (detail: unknown) => void; refuse: () => void }[],
	looks: [] as (() => Promise<void>)[]
}));

vi.mock('$lib/api', () => ({
	refusalText: () => 'The service refused that.',
	request: (url: string) =>
		new Promise((settle, refuse) => {
			const path = new URLSearchParams(url.split('?')[1]).get('path') ?? '';
			harness.asked.push({ path, settle, refuse: () => refuse(new Error('no')) });
		})
}));

vi.mock('$lib/poll', () => ({
	poll: (watch: { ask: () => Promise<void> }) => {
		harness.looks.push(watch.ask);
		return {
			prod: () => {},
			now: () => {},
			mark: () => {},
			stop: () => harness.looks.splice(harness.looks.indexOf(watch.ask), 1)
		};
	}
}));

// The cache is the module, so every test gets its own copy of it.
async function plans() {
	vi.resetModules();
	harness.asked.length = 0;
	harness.looks.length = 0;
	return import('$lib/plans.svelte');
}

const paths = () => harness.asked.map((call) => call.path);
const flush = () => new Promise((done) => setTimeout(done, 0));

function answer(at: number) {
	harness.asked[at].settle({ current: true, file: null });
	return flush();
}

test('however many rows want a file, it is read once', async () => {
	const { warm } = await plans();
	const one = warm('/media/a.mkv');
	const two = warm('/media/a.mkv');
	expect(paths()).toEqual(['/media/a.mkv']);
	await answer(0);
	one();
	two();
	expect(paths()).toEqual(['/media/a.mkv']);
});

test('one read at a time, and an opened panel goes to the front', async () => {
	const { planOf, warm } = await plans();
	warm('/media/a.mkv');
	warm('/media/b.mkv');
	warm('/media/c.mkv');
	expect(paths()).toEqual(['/media/a.mkv']);
	// The reader opened the last row while the first was still in flight.
	expect(planOf('/media/c.mkv').loading).toBe(true);
	await answer(0);
	expect(paths()).toEqual(['/media/a.mkv', '/media/c.mkv']);
});

test('an answer outlives its row, so opening again draws at once', async () => {
	const { planOf, warm } = await plans();
	const drop = warm('/media/a.mkv');
	await answer(0);
	drop();
	const plan = planOf('/media/a.mkv');
	expect([plan.loading, plan.detail?.current]).toEqual([false, true]);
	expect(paths()).toHaveLength(1);
});

test('a rewritten verdict is read again, for the rows still on the page', async () => {
	const { warm } = await plans();
	warm('/media/a.mkv');
	const gone = warm('/media/b.mkv');
	await answer(0);
	await answer(1);
	gone();
	const pass = harness.looks[0]();
	await answer(2);
	await pass;
	expect(paths()).toEqual(['/media/a.mkv', '/media/b.mkv', '/media/a.mkv']);
});

test('a pass waits for its own answers before the next can start', async () => {
	const { warm } = await plans();
	warm('/media/a.mkv');
	warm('/media/b.mkv');
	await answer(0);
	await answer(1);
	let done = false;
	void harness.looks[0]().then(() => (done = true));
	await answer(2);
	expect(done).toBe(false);
	await answer(3);
	expect(done).toBe(true);
});

test('the watcher is given up once no row wants anything', async () => {
	const { warm } = await plans();
	const drop = warm('/media/a.mkv');
	expect(harness.looks).toHaveLength(1);
	drop();
	expect(harness.looks).toHaveLength(0);
});

test('a refusal is shown, and Retry asks again', async () => {
	const { again, planOf } = await plans();
	const plan = planOf('/media/a.mkv');
	harness.asked[0].refuse();
	await flush();
	expect([plan.loading, plan.error]).toEqual([false, 'The service refused that.']);
	again('/media/a.mkv');
	expect([plan.loading, paths()]).toEqual([true, ['/media/a.mkv', '/media/a.mkv']]);
});
