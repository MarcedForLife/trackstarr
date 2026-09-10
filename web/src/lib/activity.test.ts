import { beforeEach, expect, test, vi } from 'vitest';
import { pageSnapshot, Snapshot, type Landed } from '$lib/activity.svelte';
import type { Activity, Run } from '$lib/runs';

// The poller is a timer and a stream subscription, neither of which says
// anything about what the class does with an answer. Held here so a test can
// make the look itself, and read the pace the poll would have armed to.
const harness = vi.hoisted(() => ({
	watches: [] as { ask: () => Promise<void>; pace?: () => number }[],
	marks: 0,
	// The teardowns component init would have held, so a test can end a page.
	destroys: [] as (() => void)[]
}));

vi.mock('$lib/poll', () => ({
	poll: (watch: { ask: () => Promise<void>; pace?: () => number }) => {
		harness.watches.push(watch);
		return {
			prod: () => {},
			now: () => {},
			mark: () => (harness.marks += 1),
			stop: () => {}
		};
	}
}));

vi.mock('$lib/runs', () => ({ getActivity: vi.fn() }));

// With no stream open the real one answers with the caller's own pace, and
// nothing here has an EventSource to open.
vi.mock('$lib/stream', () => ({ told: (fallback: number) => fallback }));

// The class registers its poller's stop during component init, and there is no
// component here.
vi.mock('svelte', async (original) => ({
	...(await original<typeof import('svelte')>()),
	onDestroy: (leave: () => void) => harness.destroys.push(leave)
}));

const { getActivity } = await import('$lib/runs');

function run(over: Partial<Run> = {}): Run {
	return {
		id: 'r1',
		kind: 'sweep',
		started: '2026-09-08T04:00:00+12:00',
		seconds: 12,
		dry_run: true,
		label: '',
		total: 4,
		done: 1,
		counts: {},
		stopping: false,
		active: [],
		seen: 0,
		...over
	};
}

function activity(runs: Run[] = [], over: Partial<Activity> = {}): Activity {
	return {
		paused: false,
		paused_by: '',
		paused_at: '',
		runs,
		queue: 0,
		working: 0,
		rewrites: 0,
		parked: 0,
		may_rewrite: true,
		next_sweep: null,
		...over
	};
}

/** A reader that keeps every landing it was given. */
function reader(pace = 2000) {
	const seen: Landed[] = [];
	return { seen, pace: () => pace, saw: (landed: Landed) => void seen.push(landed) };
}

/** Make the look the poller would have made. */
const look = () => harness.watches[harness.watches.length - 1].ask();

/** The pace the poller would arm to now. */
const paced = () => harness.watches[harness.watches.length - 1].pace?.();

beforeEach(() => {
	vi.clearAllMocks();
	harness.watches = [];
	harness.marks = 0;
	harness.destroys = [];
	vi.mocked(getActivity).mockResolvedValue(activity());
});

test('one look answers every reader', async () => {
	// The point of the class: two readers woken by the same stream message cost
	// one request, not one each.
	const snapshot = new Snapshot(activity([run()]));
	const panel = reader();
	const recheck = reader();
	snapshot.watch(panel);
	snapshot.watch(recheck);

	vi.mocked(getActivity).mockResolvedValue(activity());
	await look();
	expect(getActivity).toHaveBeenCalledTimes(1);
	expect(snapshot.current.runs).toEqual([]);
	// Each is handed what changed, not just what is true now.
	for (const held of [panel, recheck]) {
		expect(held.seen).toHaveLength(1);
		expect(held.seen[0].before.runs[0].id).toBe('r1');
		expect(held.seen[0].now.runs).toEqual([]);
		expect(held.seen[0].missed).toBe(false);
	}
});

test('the pace is the soonest any reader wants', async () => {
	const snapshot = new Snapshot(activity());
	// Nobody watching yet: the class has its own idle pace rather than none.
	expect(paced()).toBeGreaterThan(0);

	const watching = snapshot.watch(reader(2000));
	snapshot.watch(reader(30000));
	expect(paced()).toBe(2000);
	// The poll was armed before either had a pace to ask for, so registering
	// paces it again rather than leaving a run's bar on the idle timer.
	expect(harness.marks).toBe(2);

	// A reader that has gone stops holding the poll to its pace.
	watching();
	expect(paced()).toBe(30000);
});

test("a control's own answer lands without a look", async () => {
	// Pause and resume answer with the snapshot, which is worth a request.
	const snapshot = new Snapshot(activity());
	const panel = reader();
	snapshot.watch(panel);

	snapshot.take(activity([], { paused: true }));
	expect(getActivity).not.toHaveBeenCalled();
	expect(snapshot.current.paused).toBe(true);
	expect(panel.seen).toHaveLength(1);
});

test('a look that failed is told to the next landing', async () => {
	const snapshot = new Snapshot(activity([run()]));
	const panel = reader();
	snapshot.watch(panel);

	vi.mocked(getActivity).mockRejectedValueOnce(new Error('the network went'));
	await look();
	expect(snapshot.offline).toBe('Could not reach the service.');
	// What is on screen stays up under it: it is the last thing anyone knew.
	expect(snapshot.current.runs[0].id).toBe('r1');
	expect(panel.seen).toHaveLength(0);

	vi.mocked(getActivity).mockResolvedValue(activity());
	await look();
	expect(snapshot.offline).toBe('');
	// A run missing after a look nobody got an answer to may have been cut off
	// rather than finished, and only the reader can say what to do about that.
	expect(panel.seen[0].missed).toBe(true);

	await look();
	expect(panel.seen[1].missed).toBe(false);
});

test("the page's reading is the nav's too, until the page goes", () => {
	const first = new Snapshot(activity());
	expect(pageSnapshot.current).toBe(first);

	// The router builds the next page before tearing the last one down, so the
	// leaving page must not take the arriving one's reading with it.
	const second = new Snapshot(activity());
	harness.destroys[0]();
	expect(pageSnapshot.current).toBe(second);

	// A page that reads nothing leaves the nav to ask for itself.
	harness.destroys[1]();
	expect(pageSnapshot.current).toBeNull();
});

test('a reader still working does not hold up the next look', async () => {
	const snapshot = new Snapshot(activity());
	// A reader reading an ended run's summary out of a history the service is
	// slow to give. Before, the whole page stopped looking until it answered.
	snapshot.watch({ pace: () => 2000, saw: () => new Promise<void>(() => {}) });
	const panel = reader();
	snapshot.watch(panel);

	vi.mocked(getActivity).mockResolvedValue(activity([run()]));
	await look();
	await look();
	expect(getActivity).toHaveBeenCalledTimes(2);
	expect(panel.seen).toHaveLength(2);
});

test('a reader that throws keeps the landing for the others', async () => {
	const snapshot = new Snapshot(activity());
	const panel = reader();
	snapshot.watch({
		pace: () => 2000,
		saw: () => {
			throw new Error('a bug on the page');
		}
	});
	snapshot.watch(panel);

	vi.mocked(getActivity).mockResolvedValue(activity([run()]));
	await look();
	expect(snapshot.current.runs[0].id).toBe('r1');
	expect(panel.seen).toHaveLength(1);
});

test('an answer overtaken by a newer one is dropped', async () => {
	const snapshot = new Snapshot(activity());
	const panel = reader();
	snapshot.watch(panel);

	// A button's look and the poll's can be in the air at once.
	let answer: (activity: Activity) => void = () => {};
	vi.mocked(getActivity).mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
	const slow = snapshot.look();

	vi.mocked(getActivity).mockResolvedValue(activity([run({ id: 'newer' })]));
	await snapshot.look();

	answer(activity([run({ id: 'older' })]));
	await slow;
	expect(snapshot.current.runs[0].id).toBe('newer');
	expect(panel.seen).toHaveLength(1);
});

test('a look overtaken before it failed says nothing about the connection', async () => {
	const snapshot = new Snapshot(activity());
	const panel = reader();
	snapshot.watch(panel);

	let refuse = () => {};
	vi.mocked(getActivity).mockReturnValueOnce(
		new Promise((resolve, reject) => (refuse = () => reject(new Error('the network went'))))
	);
	const failing = snapshot.look();

	// The newer look is still out when the older one gives up, and it covers the
	// same window.
	let answer: (activity: Activity) => void = () => {};
	vi.mocked(getActivity).mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
	const newer = snapshot.look();

	refuse();
	await failing;
	expect(snapshot.offline).toBe('');

	answer(activity([run()]));
	await newer;
	// Nothing was missed, so a run gone from the next snapshot really did end.
	expect(panel.seen[0].missed).toBe(false);
});
