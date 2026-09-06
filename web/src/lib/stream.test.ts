import { afterEach, beforeEach, expect, test, vi } from 'vitest';

//: What the pages pass as their own pace, and what $lib/stream answers with
//: instead while it can be trusted to say when something changes. Both are
//: the module's own numbers; the tests only need them to differ.
const OWN_PACE = 5000;
const TOLD_MS = 120000;
const QUIET_MS = 50000;
const RETIRE_MS = 10000;

// Enough of an EventSource to be opened, spoken through and refused. The real
// one is a socket; every branch in told() turns on readyState and on when
// something last arrived, which is all this carries.
class FakeStream {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSED = 2;

	readyState: number = FakeStream.CONNECTING;
	onopen: (() => void) | null = null;
	onmessage: ((message: { data: string }) => void) | null = null;
	onerror: (() => void) | null = null;

	constructor(readonly url: string) {
		opened.push(this);
	}

	close() {
		this.readyState = FakeStream.CLOSED;
	}

	/** The service accepting it, then the first thing to arrive on it. */
	accept() {
		this.readyState = FakeStream.OPEN;
		this.onopen?.();
	}

	/** The service turning it away, which the browser does not retry. */
	refuse() {
		this.readyState = FakeStream.CLOSED;
		this.onerror?.();
	}

	say(kind: string) {
		this.onmessage?.({ data: JSON.stringify({ kind }) });
	}
}

let opened: FakeStream[] = [];
let stream: typeof import('$lib/stream');

beforeEach(async () => {
	opened = [];
	vi.useFakeTimers();
	globalThis.EventSource = FakeStream as unknown as typeof EventSource;
	// The module holds one stream for the tab, so each test gets its own copy
	// rather than whatever the last one left open.
	vi.resetModules();
	stream = await import('$lib/stream');
});

afterEach(() => {
	stream.shut();
	vi.useRealTimers();
});

test('a page keeps its own pace while nothing is listening', () => {
	expect(stream.told(OWN_PACE)).toBe(OWN_PACE);
});

test('a page looks rarely while the stream is up to tell it', () => {
	stream.subscribe(['runs'], () => {});
	opened[0].accept();
	expect(stream.told(OWN_PACE)).toBe(TOLD_MS);
});

test('a stream that is open and silent is one to stop trusting', () => {
	stream.subscribe(['runs'], () => {});
	opened[0].accept();
	// A proxy that buffers the body leaves the socket open with nothing
	// through, so silence is the tell rather than readyState.
	vi.advanceTimersByTime(QUIET_MS);
	expect(stream.told(OWN_PACE)).toBe(OWN_PACE);
});

test("the service's heartbeat is enough to keep trusting it", () => {
	stream.subscribe(['runs'], () => {});
	opened[0].accept();
	vi.advanceTimersByTime(QUIET_MS - 1000);
	opened[0].say('ping');
	vi.advanceTimersByTime(QUIET_MS - 1000);
	expect(stream.told(OWN_PACE)).toBe(TOLD_MS);
});

test('a refused stream is one to poll around', () => {
	stream.subscribe(['runs'], () => {});
	opened[0].accept();
	opened[0].refuse();
	expect(stream.told(OWN_PACE)).toBe(OWN_PACE);
});

test('a stream given up on leaves the pages back on their own pace', () => {
	stream.subscribe(['runs'], () => {});
	opened[0].accept();
	stream.shut();
	expect(stream.told(OWN_PACE)).toBe(OWN_PACE);
});

test('only the kinds a page asked for reach it, and never a ping', () => {
	const told: string[] = [];
	stream.subscribe(['runs', 'progress'], (kind) => told.push(kind));
	opened[0].accept();
	opened[0].say('runs');
	opened[0].say('library');
	opened[0].say('ping');
	opened[0].say('progress');
	expect(told).toEqual(['runs', 'progress']);
});

test('the last page to leave gives the stream up, but not on the spot', () => {
	const leave = stream.subscribe(['runs'], () => {});
	opened[0].accept();
	leave();
	// A navigation tears one page's subscription down moments before the next
	// page's goes up, and reopening between the two costs a round trip.
	expect(stream.told(OWN_PACE)).toBe(TOLD_MS);
	vi.advanceTimersByTime(RETIRE_MS);
	expect(stream.told(OWN_PACE)).toBe(OWN_PACE);
});

test('a page arriving during a navigation keeps the stream that is up', () => {
	const leave = stream.subscribe(['runs'], () => {});
	opened[0].accept();
	leave();
	vi.advanceTimersByTime(RETIRE_MS - 1000);
	stream.subscribe(['library'], () => {});
	vi.advanceTimersByTime(RETIRE_MS);
	expect(opened).toHaveLength(1);
	expect(stream.told(OWN_PACE)).toBe(TOLD_MS);
});

test('a stream that comes back tells every page to look again', () => {
	const told: string[] = [];
	stream.subscribe(['runs', 'library'], (kind) => told.push(kind));
	opened[0].accept();
	opened[0].refuse();
	// The module waits out REOPEN_MS before asking again; anything could have
	// happened while it was down, so the pages cannot trust what they hold.
	vi.advanceTimersByTime(60000);
	opened[1].accept();
	expect(told).toEqual(['runs', 'library']);
});
