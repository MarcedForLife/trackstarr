import { beforeEach, expect, test, vi } from 'vitest';
import { Recheck } from '$lib/recheck.svelte';
import type { Event } from '$lib/events';
import type { Card } from '$lib/library';
import type { Activity, Run } from '$lib/runs';

//: The module's own window: how long a run answered for but not yet in any
//: snapshot is given before a snapshot without it reads as it having ended.
const WARMING_MS = 5000;

// The poller is a timer and a stream subscription, neither of which says
// anything about what the class does with an answer. Held here so a test can
// make the look itself, one at a time and in its own order.
const harness = vi.hoisted(() => ({ watches: [] as { ask: () => Promise<void> }[] }));

vi.mock('$lib/poll', () => ({
	poll: (watch: { ask: () => Promise<void> }) => {
		harness.watches.push(watch);
		return { prod: () => {}, now: () => {}, mark: () => {}, stop: () => {} };
	}
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

// The class registers its poller's stop during component init, and there is no
// component here.
vi.mock('svelte', async (original) => ({
	...(await original<typeof import('svelte')>()),
	onDestroy: () => {}
}));

const { getActivity } = await import('$lib/runs');
const { getEvents } = await import('$lib/events');
const { runTitles } = await import('$lib/library');

function run(over: Partial<Run> = {}): Run {
	return {
		id: 'r1',
		kind: 'recheck',
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
		counts: { fixed: 1 },
		...over
	};
}

/** What the next look finds. */
function snapshot(runs: Run[]) {
	vi.mocked(getActivity).mockResolvedValue(activity(runs));
}

/** Make the look the poller would have made. */
const look = () => harness.watches[harness.watches.length - 1].ask();

let written: number;
let done: number;

/** A Recheck seeded with this snapshot, counting what it tells the page. */
function watching(runs: Run[] = []): Recheck {
	return new Recheck(activity(runs), {
		asking: () => false,
		onwritten: () => (written += 1),
		ondone: () => (done += 1)
	});
}

beforeEach(() => {
	vi.useFakeTimers();
	vi.clearAllMocks();
	harness.watches = [];
	written = 0;
	done = 0;
	snapshot([]);
	vi.mocked(getEvents).mockResolvedValue({ events: [summaryOf()], next: null });
});

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

	// A start answers with the run id while the thread still has two *arr
	// libraries to list, so an empty snapshot means nothing yet.
	await look();
	expect(recheck.warming).toBe(true);
	expect(written).toBe(0);

	vi.advanceTimersByTime(WARMING_MS + 1);
	await look();
	expect(recheck.warming).toBe(false);
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

test('a sweep next door ending is verdicts rewritten, and nothing of this page', async () => {
	const recheck = watching([run({ id: 'sweep-1', kind: 'sweep', label: '' })]);
	expect(recheck.otherRun?.id).toBe('sweep-1');
	expect(recheck.refuses).toBe('A sweep is running. Stop it first.');

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
	await recheck.runOne({ id: 'title-1' } as Card, 'report');
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
