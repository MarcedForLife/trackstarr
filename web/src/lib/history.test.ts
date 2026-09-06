import { beforeEach, expect, test, vi } from 'vitest';
import type { EventPage } from '$lib/events';
import { History } from '$lib/history.svelte';
import type { Card } from '$lib/library';

// The poller is a timer and a stream subscription, neither of which says
// anything about what a look does with its answer. Held here so a test can make
// the look itself, and read back what the class would have refused.
const harness = vi.hoisted(() => ({
	watches: [] as { ask: () => Promise<void>; ready?: () => boolean }[],
	marks: 0
}));

vi.mock('$lib/poll', () => ({
	poll: (watch: { ask: () => Promise<void>; ready?: () => boolean }) => {
		harness.watches.push(watch);
		return {
			prod: () => {},
			now: () => {},
			mark: () => (harness.marks += 1),
			stop: () => {}
		};
	}
}));

vi.mock('$lib/events', async (original) => ({
	...(await original<typeof import('$lib/events')>()),
	getEvents: vi.fn()
}));

// The class registers its poller's stop during component init, and there is no
// component here.
vi.mock('svelte', async (original) => ({
	...(await original<typeof import('svelte')>()),
	onDestroy: () => {}
}));

const { getEvents } = await import('$lib/events');

/** A line, named by the second it was written in: what `key` reads. */
function line(at: string, event = 'fixed', path = '/films/one.mkv') {
	return { ts: at, event, version: '1', path, title: 'film-1' };
}

function page(events: ReturnType<typeof line>[], over: Partial<EventPage> = {}): EventPage {
	return { events, titles: { 'film-1': { id: 'film-1' } as Card }, next: null, ...over };
}

/** What the next read answers with. */
const answers = (...pages: EventPage[]) => {
	const reads = vi.mocked(getEvents);
	reads.mockReset();
	for (const one of pages) reads.mockResolvedValueOnce(one);
	// Anything past the ones named: the same window, read again.
	reads.mockResolvedValue(pages[pages.length - 1]);
};

/** Make the look the poller would have made. */
const look = () => harness.watches[harness.watches.length - 1].ask();

/** Whether the poller would look at all as things stand. */
const ready = () => harness.watches[harness.watches.length - 1].ready?.() ?? true;

let fresh: number;

function holding(loaded: EventPage): History {
	return new History(loaded, { onfresh: () => (fresh += 1) });
}

beforeEach(() => {
	vi.clearAllMocks();
	harness.watches = [];
	harness.marks = 0;
	fresh = 0;
});

test('the page the loader handed over is what the list opens on', () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')], { next: 4096 }));
	expect(history.entries).toHaveLength(1);
	expect(history.cursor).toBe(4096);
	expect(history.titles['film-1'].id).toBe('film-1');
	expect(history.bounded).toBe(false);
});

test('a page further back is appended, and its titles merged into the ones held', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')], { next: 4096 }));
	answers(
		page([line('2026-09-04T04:00:00Z')], {
			titles: { 'film-2': { id: 'film-2' } as Card },
			next: null
		})
	);

	await history.more();
	expect(history.entries.map((entry) => entry.ts)).toEqual([
		'2026-09-05T04:00:00Z',
		'2026-09-04T04:00:00Z'
	]);
	// The rows already on screen still name their titles.
	expect(Object.keys(history.titles).sort()).toEqual(['film-1', 'film-2']);
	expect(history.cursor).toBeNull();
	expect(history.busy).toBe(false);
});

test('nothing is asked for past the oldest line', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')], { next: null }));
	await history.more();
	expect(getEvents).not.toHaveBeenCalled();
});

test('lines that landed since the last look go on the front, and nothing else moves', async () => {
	const held = line('2026-09-05T04:00:00Z');
	const history = holding(page([held], { next: 4096 }));
	answers(page([line('2026-09-05T04:05:00Z'), held]));

	await look();
	expect(history.entries.map((entry) => entry.ts)).toEqual([
		'2026-09-05T04:05:00Z',
		'2026-09-05T04:00:00Z'
	]);
	// Still the same list, so the reader's place in it and any open panel are
	// where they left them.
	expect(fresh).toBe(0);
	// And what was already loaded is still where the cursor left it.
	expect(history.cursor).toBe(4096);
});

test('a catch-up that recognises none of what it holds starts the list again', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')], { next: 4096 }));
	// More has happened than one window covers, so the loaded list no longer
	// joins up with the file: splicing over the gap would invent a history.
	answers(page([line('2026-09-05T09:00:00Z')], { next: 8192 }));

	await look();
	expect(history.entries.map((entry) => entry.ts)).toEqual(['2026-09-05T09:00:00Z']);
	expect(history.cursor).toBe(8192);
	expect(fresh).toBe(1);
});

test('a look that cannot be made leaves the lines on screen alone', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')]));
	vi.mocked(getEvents).mockRejectedValue(new Error('the service went'));

	await look();
	expect(history.entries).toHaveLength(1);
	// The lines already on screen are still true, so nothing is said about it.
	expect(history.failure).toBe('');
});

test('a read from the top replaces the list and says so', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')], { next: 4096 }));
	answers(page([line('2026-09-05T03:00:00Z')], { next: null }));

	await history.refresh();
	expect(history.entries.map((entry) => entry.ts)).toEqual(['2026-09-05T03:00:00Z']);
	expect(history.cursor).toBeNull();
	expect(fresh).toBe(1);
	// The newest lines came with it, so the catch-up has nothing to add yet.
	expect(harness.marks).toBe(1);
});

test('a read from the top that fails says so, where a catch-up does not', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')]));
	vi.mocked(getEvents).mockRejectedValue(new Error('the service went'));

	await history.refresh();
	expect(history.failure).toBe('Could not reach the service.');
	expect(history.entries).toHaveLength(1);
});

test('a window paged back through is the one the list was read under', async () => {
	const history = holding(page([line('2026-09-05T04:00:00Z')], { next: 4096 }));
	answers(page([line('2026-09-05T03:00:00Z')], { next: 4096 }));
	await history.look('1h');

	const read = vi.mocked(getEvents).mock.calls[0][3];
	await history.more();
	// A preset works out a moment from the clock, so asking again a minute later
	// would ask for a different cutoff — and the line on the boundary would fall
	// between the page above it and the page below.
	expect(vi.mocked(getEvents).mock.calls[1][3]).toBe(read);
	expect(vi.mocked(getEvents).mock.calls[1][2]).toBe(4096);
});

test('a range picked by hand opens on a week that exists, and keeps it', async () => {
	const history = holding(page([]));
	answers(page([]));

	await history.look('custom');
	expect(history.from).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
	expect(history.to).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
	expect(history.bounded).toBe(true);

	// Coming back to a range finds the one last asked for rather than the
	// default over the top of it.
	const asked = history.from;
	await history.look('all');
	await history.look('custom');
	expect(history.from).toBe(asked);
});

test('a range with both ends cleared is the whole history, not a stretch of it', async () => {
	const history = holding(page([]));
	answers(page([]));
	await history.look('custom');

	history.from = '';
	history.to = '';
	// So the line that stands where the list would be does not call every event
	// there has ever been "that range".
	expect(history.bounded).toBe(false);
});

test('nothing is looked for while a read is under way or the range has ended', async () => {
	const history = holding(page([]));
	expect(ready()).toBe(true);

	// Load more is a press the catch-up must never fight for the list.
	history.busy = true;
	expect(ready()).toBe(false);
	history.busy = false;

	answers(page([]));
	await history.look('custom');
	history.to = '2026-09-01T00:00';
	// Nothing new can ever land inside a range that ended, so re-reading a
	// settled window every ten seconds buys nothing.
	expect(ready()).toBe(false);
});
