import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Snapshot } from '$lib/activity.svelte';
import { Recheck } from '$lib/recheck.svelte';
import type { Event, EventPage } from '$lib/events';
import type { Activity, Run } from '$lib/runs';
import type { Poller, Watch } from '$lib/poll';
import { told } from '$lib/stream';

//: The module's own window: how long a run answered for but not yet in any
//: snapshot is given before a snapshot without it reads as it having ended.
const WARMING_MS = 5000;

// The poller under the snapshot is a timer and a stream subscription, neither of
// which says anything about what the class does with an answer. Held here so a
// test can make the look itself, one at a time and in its own order.
const harness = vi.hoisted(() => ({
	watches: [] as { ask: () => Promise<void> }[],
	prod: vi.fn(),
	realPolling: false,
	pollers: [] as Poller[]
}));

vi.mock('$lib/poll', async (original) => {
	const { poll } = await original<typeof import('$lib/poll')>();
	return {
		poll: (watch: Watch) => {
			if (harness.realPolling) {
				const poller = poll(watch);
				harness.pollers.push(poller);
				return poller;
			}
			harness.watches.push(watch);
			return { prod: harness.prod, now: () => {}, mark: () => {}, stop: () => {} };
		}
	};
});

vi.mock('$lib/stream', () => ({
	told: vi.fn((fallback: number) => fallback),
	subscribe: () => () => {}
}));

vi.mock('$lib/runs', () => ({ getActivity: vi.fn(), stopRun: vi.fn() }));

vi.mock('$lib/events', async (original) => ({
	...(await original<typeof import('$lib/events')>()),
	getEvents: vi.fn()
}));

vi.mock('$lib/library', async (original) => ({
	...(await original<typeof import('$lib/library')>()),
	runTitles: vi.fn()
}));

// The classes register a stop during component init, and there is no component
// here.
vi.mock('svelte', async (original) => ({
	...(await original<typeof import('svelte')>()),
	onDestroy: () => {}
}));

const { getActivity, stopRun } = await import('$lib/runs');
const { getEvents } = await import('$lib/events');
const { runTitles } = await import('$lib/library');

function run(over: Partial<Run> = {}): Run {
	return {
		id: 'r1',
		type: 'recheck',
		started: '2026-09-05T04:00:00+12:00',
		seconds: 12,
		dry_run: true,
		label: 'one title',
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

function summaryOf(over: Partial<Event> = {}): Event {
	return {
		ts: '2026-09-05T04:01:00+12:00',
		event: 'recheck',
		version: '1',
		run: 'r1',
		files: 4,
		counts: { modified: 1 },
		...over
	};
}

/** What the next look finds. */
function snapshot(runs: Run[]) {
	vi.mocked(getActivity).mockResolvedValue(activity(runs));
}

/** Let the readers finish what a landing set off, which the snapshot does not
 * wait for. */
const settle = () => vi.advanceTimersByTimeAsync(0);

/** Make the look the poller would have made, and let this class read it. */
const look = async () => {
	await harness.watches[harness.watches.length - 1].ask();
	await settle();
};

let written: number;
let done: number;

/** A Recheck watching a snapshot seeded with these runs, counting what it tells
 * the page. */
function watching(runs: Run[] = []): Recheck {
	return new Recheck(new Snapshot(activity(runs)), {
		asking: () => false,
		onwritten: () => (written += 1),
		ondone: () => (done += 1)
	});
}

beforeEach(() => {
	vi.useFakeTimers();
	vi.clearAllMocks();
	harness.watches = [];
	harness.realPolling = false;
	vi.mocked(told).mockImplementation((fallback) => fallback);
	written = 0;
	done = 0;
	snapshot([]);
	vi.mocked(getEvents).mockResolvedValue({ events: [summaryOf()], next: null });
});

afterEach(() => {
	for (const poller of harness.pollers.splice(0)) poller.stop();
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

test.each(['report', 'apply'] as const)(
	'a fast sheet %s run releases its controls with a live event stream',
	async (mode) => {
		harness.realPolling = true;
		vi.stubGlobal('document', {
			visibilityState: 'visible',
			addEventListener: () => {},
			removeEventListener: () => {}
		});
		vi.mocked(told).mockReturnValue(120000);
		vi.mocked(runTitles).mockResolvedValue({ run: 'r1', titles: 1 });
		const recheck = watching();
		await recheck.runOne('title-1', mode);
		expect(recheck.runner.busy).toBe(mode);

		// The whole run finished before the first snapshot. Its last stream
		// notification is covered by this look, so no more messages are coming.
		await vi.advanceTimersByTimeAsync(2000);
		expect(getActivity).toHaveBeenCalledOnce();
		expect(recheck.runner.starting).toBe(true);
		await vi.advanceTimersByTimeAsync(4000);
		expect(recheck.runner.busy).toBe('');
		expect(recheck.runner.starting).toBe(false);
		expect(recheck.runner.run).toBeNull();
		expect(written).toBe(1);
		expect(done).toBe(1);

		// Once settled, return to the stream's ordinary fallback pace.
		vi.mocked(getActivity).mockClear();
		await vi.advanceTimersByTimeAsync(30000);
		expect(getActivity).not.toHaveBeenCalled();
	}
);

test('a run already going when the page loads is adopted', async () => {
	// A run is the service's, not a tab's, and the verdicts it is about to write
	// are this grid's.
	const recheck = watching([run()]);
	expect(recheck.walking).toBe(true);
	expect(recheck.warming).toBe(true);
	expect(recheck.otherRun).toBeNull();

	snapshot([run()]);
	await look();
	expect(recheck.running?.id).toBe('r1');
	expect(recheck.warming).toBe(false);
	expect(recheck.offline).toBe('');
});

test('one found mid-selection is adopted by the look that finds it', async () => {
	const recheck = watching();
	expect(recheck.walking).toBe(false);

	snapshot([run({ id: 'somebody-else' })]);
	await look();
	expect(recheck.running?.id).toBe('somebody-else');
	expect(recheck.otherRun).toBeNull();
});

test('a run answered for is not ended by a snapshot too early to hold it', async () => {
	vi.mocked(runTitles).mockResolvedValue({ run: 'r1', titles: 1 });
	const recheck = watching();
	await recheck.start(['title-1'], 'report');
	expect(recheck.warming).toBe(true);
	expect(recheck.busy).toBe('report');

	// A start answers with the run id while the thread still has two *arr
	// libraries to list, so an empty snapshot means nothing yet.
	await look();
	expect(recheck.warming).toBe(true);
	expect(written).toBe(0);

	vi.advanceTimersByTime(WARMING_MS + 1);
	await look();
	expect(recheck.warming).toBe(false);
	expect(recheck.busy).toBe('');
	expect(written).toBe(1);
});

test('a run that leaves the snapshot is read back from the history', async () => {
	snapshot([run({ dry_run: false, label: 'four titles' })]);
	const recheck = watching([run()]);
	await look();

	snapshot([]);
	await look();
	expect(recheck.running).toBeNull();
	// The posters are the answer and the receipt is the commentary, so both.
	expect(written).toBe(1);
	expect(done).toBe(1);
	expect(recheck.summary?.run).toBe('r1');
	// The label and the mode come off the last snapshot, since a re-check forced
	// back to report-only must not be reported as one that rewrote.
	expect(recheck.line('the selected titles')).toBe('Processed four titles · 4 files · 1 rewritten');
});

test('a run that ended between two polls is still described', async () => {
	// Nothing here ever saw it in a snapshot, so the noun is the caller's.
	const recheck = watching([run()]);
	vi.advanceTimersByTime(WARMING_MS + 1);
	snapshot([]);
	await look();
	expect(recheck.line('this title')).toBe('Planned this title · 4 files · 1 rewritten');
});

test('a summary the history cannot be reached for leaves the posters to say it', async () => {
	vi.mocked(getEvents).mockRejectedValue(new Error('the service went'));
	watching([run()]);
	vi.advanceTimersByTime(WARMING_MS + 1);
	snapshot([]);
	await look();
	expect(written).toBe(1);
	expect(done).toBe(0);
});

test('a receipt does not land on a run adopted while the history was read', async () => {
	// Two snapshots can be in the air at once, since a button looks as well as
	// the poll.
	snapshot([run()]);
	const recheck = watching([run()]);
	await look();
	expect(recheck.running?.id).toBe('r1');

	// The history the ended run's summary comes from, held until the test hands
	// it over.
	let hand: (page: EventPage) => void = () => {};
	vi.mocked(getEvents).mockReturnValueOnce(new Promise((resolve) => (hand = resolve)));
	snapshot([]);
	await look();

	// A run adopted while the history was still out.
	snapshot([run({ id: 'r2' })]);
	await look();
	expect(recheck.running?.id).toBe('r2');

	hand({ events: [summaryOf()], next: null });
	await settle();
	// The run on the bar is not the one the summary is about.
	expect(recheck.summary).toBeNull();
	expect(done).toBe(0);
});

test('a sweep next door ending is verdicts rewritten, and nothing of this page', async () => {
	const recheck = watching([run({ id: 'sweep-1', type: 'sweep', label: '' })]);
	expect(recheck.otherRun?.id).toBe('sweep-1');
	expect(recheck.refuses).toBe('');

	snapshot([]);
	await look();
	expect(recheck.otherRun).toBeNull();
	expect(written).toBe(1);
	expect(done).toBe(0);
	expect(recheck.summary).toBeNull();
	expect(recheck.refuses).toBe('');
});

test('a sheet that is dismissed takes its own receipt with it, but not a live run', async () => {
	vi.mocked(runTitles).mockResolvedValue({ run: 'r1', titles: 1 });
	const recheck = watching();
	await recheck.runOne('title-1', 'report');
	snapshot([run()]);
	await look();

	// Still going: it carries on in the bar behind the sheet.
	recheck.sheetShut();
	expect(recheck.runner.run?.id).toBe('r1');

	snapshot([]);
	await look();
	expect(recheck.summary).not.toBeNull();
	// Finished, and the sheet has already shown the result: a bar popping up to
	// say it again the moment the sheet is dismissed is one screen too many.
	recheck.sheetShut();
	expect(recheck.summary).toBeNull();
	expect(recheck.fromSheet).toBe(false);
});

test('a service that cannot be reached is a condition, not a refusal', async () => {
	const recheck = watching([run()]);
	vi.mocked(getActivity).mockRejectedValue(new Error('the network went'));
	await look();
	expect(recheck.offline).toBe('Could not reach the service.');
	// What is on screen stays up under it: it is the last thing anyone knew.
	expect(recheck.walking).toBe(true);
	expect(recheck.refusal).toBe('');
});

test.each(['report', 'apply'] as const)(
	'keeps the title %s press busy until its run ends',
	async (mode) => {
		vi.mocked(runTitles).mockResolvedValue({ run: 'r1', titles: 1 });
		const recheck = watching();
		await recheck.runOne('title-1', mode);
		expect(recheck.runner.starting).toBe(true);
		expect(recheck.runner.busy).toBe(mode);
		snapshot([run({ dry_run: mode === 'report' })]);
		await look();
		expect(recheck.runner.starting).toBe(false);
		expect(recheck.runner.run?.id).toBe('r1');
		expect(recheck.runner.busy).toBe(mode);
		snapshot([]);
		await look();
		expect(recheck.runner.run).toBeNull();
		expect(recheck.runner.busy).toBe('');
	}
);

test('a stop stays pending until a snapshot shows the run stopping', async () => {
	vi.mocked(runTitles).mockResolvedValue({ run: 'r1', titles: 1 });
	vi.mocked(stopRun).mockResolvedValue({});
	const recheck = watching();
	await recheck.start(['title-1'], 'report');
	snapshot([run()]);
	await look();
	await recheck.stop();
	expect(recheck.stopping).toBe(true);
	snapshot([run({ stopping: true })]);
	await look();
	expect(recheck.stopping).toBe(false);
});

test('stopping a sheet run requests a fresh snapshot and releases its controls when ended', async () => {
	vi.mocked(runTitles).mockResolvedValue({ run: 'r1', titles: 1 });
	const recheck = watching();
	await recheck.runOne('coffee', 'report');
	snapshot([run()]);
	await look();
	harness.prod.mockClear();
	await recheck.stop();
	expect(harness.prod).toHaveBeenCalledOnce();
	snapshot([]);
	await look();
	expect(recheck.runner.run).toBeNull();
	expect(recheck.runner.starting).toBe(false);
	expect(recheck.runner.busy).toBe('');
});
