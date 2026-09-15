import { request } from '$lib/api';
import type { Verdict } from '$lib/library';
import type { Pause } from '$lib/pauses';

export type FileCover = { id: string; name: string };
export type FileCovers = Record<string, FileCover>;

// What a rewrite would do to one file, as a card tallies it. `changes` counts
// every line the file's plan lists; the rest are the layouts behind them.
export type FileChanges = {
	status: Verdict;
	changes: number;
	adds: string[];
	rebuilds: string[];
	drops: number;
};
// A file with no stored verdict is absent, not empty: it has yet to be checked.
export type FilePlans = Record<string, FileChanges>;

export type QueueItem = { run: string; path: string; expected: number; position: number };
export type QueuePage = {
	covers?: FileCovers;
	plans?: FilePlans;
	// False where the plans were judged under rules that have since changed.
	// The work phase replans before it writes, so the tallies are the last
	// check and not what the queued rewrite is going to do.
	plans_current: boolean;
	items: QueueItem[];
	total: number;
	matched: number;
	offset: number;
	// Which queue this page describes. The service numbers what a page would
	// draw, and says whose numbering it is, since a restart starts it again.
	epoch: string;
	revision: number;
};
// A queued file as a command names it: the two fields that find the row again.
export type QueueTarget = Pick<QueueItem, 'run' | 'path'>;
export const queueKey = (item: QueueTarget) => JSON.stringify([item.run, item.path]);
export function getQueue(q = '', offset = 0): Promise<QueuePage> {
	return request(`/api/queue?${new URLSearchParams({ q, offset: String(offset) })}`);
}

// What the service hands back per request, and how many times a joined read
// starts over before it leaves the reader with what it last had.
const PAGE = 50;
const ATTEMPTS = 3;

/** Workers took every attempt at a coherent read out from under it. Not a
 * refusal. The service answered every page, and no two described one queue. */
export class QueueChanging extends Error {
	constructor() {
		super('The queue is changing quickly. Try again in a moment.');
	}
}

/** The first `count` rows as one answer, or null once `live` says a newer read
 * has replaced this one.
 *
 * Pages join only where they describe the same queue, so an answer never loses
 * a row a claim shifted past it or repeats one a promotion moved. Throws where
 * the queue keeps changing under the read. */
export async function readQueue(
	q: string,
	count: number,
	live: () => boolean
): Promise<QueuePage | null> {
	for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
		const page = await getQueue(q);
		if (!live()) return null;
		const rows = Math.min(count, page.matched);
		let changed = false;
		for (let at = PAGE; !changed && at < rows; at += PAGE) {
			const next = await getQueue(q, at);
			if (!live()) return null;
			changed = next.epoch !== page.epoch || next.revision !== page.revision;
			if (!changed) absorb(page, next);
		}
		if (!changed) return page;
	}
	throw new QueueChanging();
}

function absorb(page: QueuePage, next: QueuePage): void {
	page.items.push(...next.items);
	page.covers = { ...page.covers, ...next.covers };
	page.plans = { ...page.plans, ...next.plans };
	// The joined answer cannot say which of its pages a rule change caught, so
	// one stale page leaves all of it stale.
	page.plans_current &&= next.plans_current;
}
export type QueueAnswer = {
	moved?: number;
	changed?: number;
	undo?: string | null;
	restored?: boolean;
};
export function queueAction(
	action: 'top' | 'pause' | 'skip' | 'undo',
	items: QueueTarget[] = [],
	extra: { seconds?: number; token?: string } = {}
): Promise<QueueAnswer> {
	return request('/api/queue', {
		method: 'POST',
		body: JSON.stringify({ action, items, ...extra })
	});
}

export type TitleWork = {
	queued: QueueItem[];
	active: { run: string; path: string; stage: string; stopping: boolean; skipped?: boolean }[];
	pauses: Pause[];
};
export function getTitleWork(id: string): Promise<TitleWork> {
	return request(`/api/library/work?${new URLSearchParams({ id })}`);
}

/** What a title's work says about one of its files: the rows that name it, and
 * the word the row leads with in place of its verdict. */
export type FileState = {
	waiting: QueueItem[];
	running: TitleWork['active'];
	/** The pause on the file itself, or the one on the title above it. */
	paused: Pause | undefined;
	/** A worker has it, rather than holding a slot for it. */
	active: boolean;
	stopping: boolean;
	/** Empty where the file's own verdict is still the thing to say. A queued
	 * file leads with its place, since that is what a reorder moves. */
	label: string;
};

export function fileState(work: TitleWork | undefined, path: string): FileState {
	const waiting = work?.queued.filter((item) => item.path === path) ?? [];
	const running = work?.active.filter((item) => item.path === path) ?? [];
	const paused =
		work?.pauses.find((pause) => pause.path === path) ??
		work?.pauses.find((pause) => path.startsWith(`${pause.path}/`));
	const active = running.some((item) => item.stage !== 'waiting');
	const stopping = running.some((item) => item.stopping || item.skipped);
	const runs = waiting.length > 1 ? ` · ${waiting.length} runs` : '';
	const label = stopping
		? 'Stopping…'
		: running.length
			? active
				? 'Processing now'
				: 'Waiting for a worker'
			: paused
				? paused.path === path
					? 'Paused'
					: 'Title paused'
				: waiting.length
					? `Pending (#${Math.min(...waiting.map((item) => item.position))})${runs}`
					: '';
	return { waiting, running, paused, active, stopping, label };
}
