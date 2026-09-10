import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { poll, type Poller } from '$lib/poll';
import type { Kind } from '$lib/stream';

// The stream is the other half of the pace: a page told what changed looks
// sooner than its own timer would. What the module needs from it is one
// handler and a way out, so that is all this is.
const heard = vi.hoisted(() => ({
	tell: null as ((kind: Kind) => void) | null,
	dropped: 0
}));

vi.mock('$lib/stream', () => ({
	subscribe: (kinds: readonly Kind[], tell: (kind: Kind) => void) => {
		heard.tell = tell;
		return () => {
			heard.tell = null;
			heard.dropped += 1;
		};
	}
}));

// Node has no document, and every branch in the module turns on whether the
// tab is being watched and on the wake that fires when that changes.
const watchers = new Set<() => void>();
let visibility: 'visible' | 'hidden' = 'visible';

globalThis.document = {
	get visibilityState() {
		return visibility;
	},
	addEventListener: (type: string, handler: () => void) => {
		if (type === 'visibilitychange') watchers.add(handler);
	},
	removeEventListener: (type: string, handler: () => void) => {
		if (type === 'visibilitychange') watchers.delete(handler);
	}
} as unknown as Document;

function look(to: 'visible' | 'hidden') {
	visibility = to;
	for (const watcher of [...watchers]) watcher();
}

let looks: number;
let poller: Poller | null;

/** A look that answers at once, counted. */
function counting(): Promise<void> {
	looks += 1;
	return Promise.resolve();
}

beforeEach(() => {
	vi.useFakeTimers();
	watchers.clear();
	visibility = 'visible';
	heard.tell = null;
	heard.dropped = 0;
	looks = 0;
	poller = null;
});

afterEach(() => {
	poller?.stop();
	vi.useRealTimers();
});

test('looks on its own pace, and again a pace after that', async () => {
	poller = poll({ ask: counting, pace: () => 1000 });
	await vi.advanceTimersByTimeAsync(999);
	expect(looks).toBe(0);
	await vi.advanceTimersByTimeAsync(1);
	expect(looks).toBe(1);
	await vi.advanceTimersByTimeAsync(1000);
	expect(looks).toBe(2);
});

test('a prod that lands mid-look is owed, not dropped', async () => {
	let release = () => {};
	poller = poll({
		ask: () =>
			new Promise((resolve) => {
				looks += 1;
				release = () => resolve();
			}),
		pace: () => 1000
	});
	await vi.advanceTimersByTimeAsync(1000);
	expect(looks).toBe(1);

	// The look in flight re-arms the chain as it finishes and finds the prod
	// there; asking again on the spot would be two looks at once.
	poller.prod();
	expect(looks).toBe(1);
	release();
	await vi.advanceTimersByTimeAsync(1);
	expect(looks).toBe(2);
});

test('a prod waits out the gap, and now() does not', async () => {
	poller = poll({ ask: counting, gap: 5000 });
	// No pace at all: nothing happens until something asks for it.
	await vi.advanceTimersByTimeAsync(60000);
	expect(looks).toBe(0);

	poller.prod();
	await vi.advanceTimersByTimeAsync(1);
	expect(looks).toBe(1);

	poller.now();
	await vi.advanceTimersByTimeAsync(1);
	expect(looks).toBe(2);
});

test('a burst of messages is held to one look a gap', async () => {
	poller = poll({ ask: counting, gap: 1000, kinds: ['runs'] });
	heard.tell?.('runs');
	heard.tell?.('runs');
	heard.tell?.('runs');
	await vi.advanceTimersByTimeAsync(1000);
	expect(looks).toBe(1);
});

test('mark() pushes the next look a pace out from now', async () => {
	poller = poll({ ask: counting, pace: () => 1000 });
	await vi.advanceTimersByTimeAsync(600);
	// Somebody else has just read this, so the look 400ms away is spent.
	poller.mark();
	await vi.advanceTimersByTimeAsync(400);
	expect(looks).toBe(0);
	await vi.advanceTimersByTimeAsync(600);
	expect(looks).toBe(1);
});

test('a tab nobody is watching makes no look, and catches up when it is', async () => {
	look('hidden');
	poller = poll({ ask: counting, pace: () => 1000 });
	await vi.advanceTimersByTimeAsync(5000);
	expect(looks).toBe(0);

	look('visible');
	await vi.advanceTimersByTimeAsync(1);
	expect(looks).toBe(1);
});

test('a ready() that answers no keeps what the stream owed it', async () => {
	let ready = false;
	poller = poll({ ask: counting, ready: () => ready, pace: () => 1000, kinds: ['runs'] });
	heard.tell?.('runs');
	await vi.advanceTimersByTimeAsync(1);
	expect(looks).toBe(0);

	// Owed rather than spent on a pass that made no look: the page was not
	// ready, and what the stream said still has not been read.
	ready = true;
	await vi.advanceTimersByTimeAsync(1000);
	expect(looks).toBe(1);
});

test('stop() leaves no timer, no listener and no subscription', async () => {
	poller = poll({ ask: counting, pace: () => 1000, kinds: ['runs'] });
	expect(watchers.size).toBe(1);
	poller.stop();
	expect(vi.getTimerCount()).toBe(0);
	expect(watchers.size).toBe(0);
	expect(heard.dropped).toBe(1);

	// And nothing a stopped poller is told brings the chain back.
	poller.now();
	await vi.advanceTimersByTimeAsync(60000);
	expect(looks).toBe(0);
});
