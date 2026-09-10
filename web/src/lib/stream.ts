// One EventSource per tab, shared by every page. A message names a kind and
// carries nothing else; the page refetches what it already fetches, which keeps
// the ETags working.
//
// The browser reconnects a dropped stream; one the server refused is retried
// here, slowly. The pages keep a slow poll as fallback, so a proxy that
// swallows the stream costs latency, never truth.

import { source as demoSource } from '$demo';
import type { Source } from '$lib/demo/stream';

// `runs` is the registry changing; `progress` is a run in it moving, which is
// the same fetch and far less urgent. A list so fixtures.test.ts can check it
// against the service's.
export const KINDS = ['runs', 'library', 'events', 'progress'] as const;
export type Kind = (typeof KINDS)[number];

type Listener = { kinds: readonly Kind[]; tell: (kind: Kind) => void };

const listeners = new Set<Listener>();

// An EventSource, or the demo's stand-in for one.
let source: Source | null = null;

// When something last arrived, a message or the service's 20s heartbeat. A
// proxy buffering the body leaves the socket OPEN with nothing through, so
// silence is the tell.
let heard = 0;

// Whether messages may have been missed, so the next open tells every page to
// look again.
let missed = false;

let reopen: ReturnType<typeof setTimeout>;
let retire: ReturnType<typeof setTimeout>;

// A stream silent this long reads as down: two heartbeats and change.
const QUIET_MS = 50000;

// How long after the server refuses a stream before asking again. Places come
// back as tabs close, not quickly.
const REOPEN_MS = 60000;

// How long a stream with no listeners is kept: enough to carry a navigation.
const RETIRE_MS = 10000;

// How long a page leaves between its own looks while the stream is up.
// Insurance against a stream that quietly died.
const TOLD_MS = 120000;

/** Whether the stream can be trusted to say when something changes. */
function live(): boolean {
	return source?.readyState === EventSource.OPEN && Date.now() - heard < QUIET_MS;
}

/** How long to wait before looking anyway: `fallback`, or a couple of minutes
 * while the stream is live. */
export function told(fallback: number): number {
	return live() ? TOLD_MS : fallback;
}

// Every kind to every listener, for a stream that just came back.
function lookAgain() {
	for (const listener of [...listeners]) {
		for (const kind of listener.kinds) listener.tell(kind);
	}
}

function open() {
	const stream: Source = demoSource?.() ?? new EventSource('/api/stream');
	source = stream;
	stream.onopen = () => {
		heard = Date.now();
		// A reconnection; the first open follows the pages' own loads.
		if (missed) {
			missed = false;
			lookAgain();
		}
	};
	stream.onmessage = (message) => {
		heard = Date.now();
		let kind: Kind | 'ping';
		try {
			kind = JSON.parse(message.data)?.kind;
		} catch {
			return;
		}
		// A ping only proves the stream is moving.
		if (kind === 'ping') return;
		for (const listener of [...listeners]) {
			if ((listener.kinds as readonly string[]).includes(kind)) listener.tell(kind as Kind);
		}
	};
	stream.onerror = () => {
		missed = true;
		// CLOSED is a refusal the browser will not retry; CONNECTING is a drop it
		// is already retrying.
		if (stream.readyState === EventSource.CLOSED) {
			source = null;
			clearTimeout(reopen);
			reopen = setTimeout(() => {
				if (listeners.size && !source) open();
			}, REOPEN_MS);
		}
	};
}

/** Give the stream up now, for a session that has gone. The next stream starts
 * by telling every page to look again. */
export function shut() {
	clearTimeout(reopen);
	clearTimeout(retire);
	source?.close();
	source = null;
	missed = true;
}

/** Be told whenever any of these kinds changes; returns the unsubscribe. Also
 * runs for every kind after a reconnection. Called on hidden tabs too. */
export function subscribe(kinds: readonly Kind[], tell: (kind: Kind) => void): () => void {
	const listener = { kinds, tell };
	listeners.add(listener);
	clearTimeout(retire);
	if (!source) open();
	return () => {
		listeners.delete(listener);
		if (listeners.size) return;
		clearTimeout(retire);
		retire = setTimeout(() => {
			clearTimeout(reopen);
			source?.close();
			source = null;
		}, RETIRE_MS);
	};
}
