// The saved plan behind a file row, read before anybody opens the row: a panel
// that gains height after it opened flashes the row's own fill through the
// blind. Rows ask for theirs while they are on the page, one read at a time.

import { refusalText, request } from '$lib/api';
import type { LibraryFile } from '$lib/library';
import { poll, type Poller } from '$lib/poll';

/** `file` is null for a path no sweep has reached. */
export type FileDetail = { current: boolean; file: LibraryFile | null };

/** One file's answer, or the wait for it. */
export class Plan {
	/** Nothing read yet. A re-read keeps the last answer up instead. */
	loading = $state(true);
	error = $state('');
	detail = $state<FileDetail | null>(null);
	/** Rows on the page showing or about to show this. */
	wanted = 0;
	/** Whether a read is queued or in flight. */
	asked = false;
}

// Answers kept for rows that have gone. A queue page is 50 rows.
const KEEP = 120;

// The least time between two passes. A walk publishes every couple of seconds,
// and a pass per message would keep fifty rows reading for as long as it ran.
const REFRESH_MS = 15000;

// Only the entries are drawn, and each is its own reactive object.
// eslint-disable-next-line svelte/prefer-svelte-reactivity
const held = new Map<string, Plan>();
// Waiting their turn, oldest first. One read at a time: fifty rows asking at
// once would hold the service until it had answered them all.
const asking: string[] = [];
// The pass in flight, so the watcher can wait for the answers it asked for.
let draining: Promise<void> | null = null;
let wanting = 0;
let watcher: Poller | null = null;

function entry(path: string): Plan {
	let found = held.get(path);
	if (!found) {
		found = new Plan();
		held.set(path, found);
	}
	return found;
}

// Oldest first, keeping everything a row is still showing.
function trim() {
	for (const [path, found] of held) {
		if (held.size <= KEEP) return;
		if (!found.wanted) held.delete(path);
	}
}

async function read(path: string) {
	const found = held.get(path);
	if (!found) return;
	try {
		found.detail = await request<FileDetail>(`/api/library/file?${new URLSearchParams({ path })}`);
		found.error = '';
	} catch (failure) {
		found.error = refusalText(failure);
	} finally {
		found.loading = false;
		found.asked = false;
	}
}

function drain(): Promise<void> {
	return (draining ??= pass());
}

/** Everything waiting, oldest first. */
async function pass() {
	try {
		for (let path = asking.shift(); path; path = asking.shift()) await read(path);
	} finally {
		draining = null;
		trim();
	}
}

/** `first` is an open panel, which goes ahead of the rows only warming. */
function queue(path: string, first: boolean) {
	const found = entry(path);
	if (found.asked) {
		const at = first ? asking.indexOf(path) : -1;
		if (at > 0) asking.unshift(...asking.splice(at, 1));
		return;
	}
	found.asked = true;
	if (first) asking.unshift(path);
	else asking.push(path);
	void drain();
}

// A verdict has been rewritten: the rows on the page read again, the rest are
// dropped rather than fetched for nobody.
async function refresh() {
	for (const [path, found] of [...held]) {
		if (found.wanted) queue(path, false);
		else held.delete(path);
	}
	await drain();
}

/** For a panel being drawn now: asked for at once if nothing is held. */
export function planOf(path: string): Plan {
	const found = entry(path);
	if (!found.detail && !found.error) queue(path, true);
	return found;
}

/** Have this file's plan in by the time its row is opened. Call from an
 * $effect and return the result. */
export function warm(path: string): () => void {
	const found = entry(path);
	found.wanted += 1;
	wanting += 1;
	if (!found.detail && !found.error) queue(path, false);
	watcher ??= poll({ ask: refresh, gap: REFRESH_MS, kinds: ['library'] });
	return () => {
		found.wanted -= 1;
		wanting -= 1;
		if (wanting) return;
		watcher?.stop();
		watcher = null;
	};
}

/** Ask again after a refusal. */
export function again(path: string) {
	const found = entry(path);
	found.loading = true;
	found.error = '';
	queue(path, true);
}
