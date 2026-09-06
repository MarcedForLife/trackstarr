// What the service is doing this second, and the buttons that change it.
// $lib/events is what happened; this is what is happening.

import { request } from '$lib/api';
import { mark } from '$lib/clock.svelte';
import { duration, named, soon, titled } from '$lib/format';
import { pip, verdictHint, verdictLabel } from '$lib/library';
// Re-exported so a page reads this module's answers from one import.
export { duration, named, titled };

// What the thread holding a file is doing. A bar at zero means something
// different for a file queued behind a slot than for one encoding.
export type Stage = 'working' | 'waiting' | 'encoding';

// One file a thread has picked up. Once its encode starts: the running time,
// how much is written, and the speed as a multiple of realtime.
export type ActiveFile = {
	path: string;
	seconds: number;
	stage: Stage;
	duration: number;
	done: number;
	speed: number;
};

// One file a run has finished with, and what it came to.
export type DoneFile = {
	path: string;
	// Empty for the poll between the file's release and its verdict landing.
	status: string;
	seconds: number;
	// What a rewrite would do, or why one did not happen.
	detail: string;
};

export type Run = {
	// The run id the history's events carry.
	id: string;
	// A library walk, a delivery from an *arr (Radarr or Sonarr), or a re-check
	// of selected titles.
	kind: 'sweep' | 'import' | 'recheck';
	// A stamp with its offset, like every `ts` in the history.
	started: string;
	seconds: number;
	dry_run: boolean;
	// The *arr for an import; the title or count for a re-check; empty for a
	// sweep.
	label: string;
	total: number;
	done: number;
	counts: Record<string, number>;
	// Files found work for that no worker has picked up, and whether the walk is
	// still going, in which case the count is a floor. Absent from older builds.
	queued?: number;
	walking?: boolean;
	// Wall seconds of rewriting left, from the service's own measured speeds.
	// Null for a run with no rewriting in it.
	rewrite_seconds?: number | null;
	stopping: boolean;
	active: ActiveFile[];
	// Files it worked on, newest first. Cached verdicts are not here. Absent from
	// older builds.
	recent?: DoneFile[];
	// When this reading reached the browser, on the tab's monotonic clock. Every
	// number beside it was true then, so the page draws them against the age.
	seen: number;
};

export type Activity = {
	paused: boolean;
	paused_by: string;
	paused_at: string;
	// When the service came up. Runs vanishing across a change of this went with
	// the process, not to completion. Absent from older builds.
	up_since?: string;
	runs: Run[];
	// Every file still owed, from any run: queued deliveries, a sweep's unwalked
	// remainder, a re-check's folders.
	queue: number;
	// Files being worked on this second, across every run.
	working: number;
	// Of those, the ones encoding: what an abort would waste.
	rewrites: number;
	// Files a download client still hard-links, waiting for it to let go.
	parked: number;
	// Whether REWRITE_MODE lets this install rewrite. When false the page must not
	// offer a rewrite, since it would be silently downgraded.
	may_rewrite: boolean;
	// When the scheduled sweep next fires, or null with none scheduled.
	next_sweep: string | null;
};

/** Mark each run with when this snapshot arrived, so the page can age it. */
function stamped(activity: Activity): Activity {
	const at = mark();
	for (const run of activity.runs) run.seen = at;
	return activity;
}

export async function getActivity(fetcher: typeof fetch = fetch): Promise<Activity> {
	return stamped(await request<Activity>('/api/runs', undefined, fetcher));
}

function control(path: string, body: object = {}): Promise<unknown> {
	return request(`/api/runs/${path}`, { method: 'POST', body: JSON.stringify(body) });
}

// Pause and resume answer with the next snapshot, so the button's state comes
// from the service rather than a guess.
export const pause = () => control('pause').then((answer) => stamped(answer as Activity));
export const resume = () => control('resume').then((answer) => stamped(answer as Activity));

export const startSweep = (mode: 'report' | 'apply') =>
	control('start', { mode }) as Promise<{ run: string }>;

export const stopRun = (run: string) => control('stop', { run });

// Every run asked to stop and every rewrite under way killed.
export const stopEverything = () =>
	control('abort') as Promise<{ stopped: number; rewrites: number }>;

/** A run's name: "Sweep", "Radarr import", "Re-check: <title or count>". */
export function source(run: Run): string {
	if (run.kind === 'import') {
		return `${run.label ? run.label[0].toUpperCase() + run.label.slice(1) : 'An'} import`;
	}
	if (run.kind === 'recheck') return `Re-check: ${run.label || 'the selected titles'}`;
	return 'Sweep';
}

// The verb per kind, biggest job first: a sweep with a delivery beside it is
// still "Sweeping".
const VERBS: [Run['kind'], string][] = [
	['sweep', 'Sweeping'],
	['recheck', 'Re-checking'],
	['import', 'Importing']
];

/** The overview's one-word heading: Paused, then Stopping, then the biggest
 * kind of work going. */
export function doing(activity: Activity): string {
	if (activity.paused) return 'Paused';
	const { runs } = activity;
	if (!runs.length) return 'Idle';
	if (runs.every((run) => run.stopping)) return 'Stopping';
	const going = new Set(runs.map((run) => run.kind));
	return VERBS.find(([kind]) => going.has(kind))?.[1] ?? 'Working';
}

/**
 * What a run is doing, in a few words.
 *
 * No total yet means still listing (or, for a delivery, still being handed
 * files), not "0 of 0". A one-file run, which most deliveries are, counts
 * nothing: the row under it says more than "0 of 1" would.
 */
export function progressLabel(run: Run): string {
	if (run.total > 1) return `${run.done.toLocaleString()} of ${run.total.toLocaleString()} files`;
	if (!run.total) {
		if (run.kind === 'import') return 'Queueing the delivery…';
		return run.kind === 'recheck' ? 'Listing the files…' : 'Walking the library…';
	}
	return run.active.length || run.done ? '' : 'Waiting for a free worker';
}

/** Whether the run's bar is worth drawing: a one-file run's has two positions. */
export function measured(run: Run): boolean {
	return run.total > 1;
}

/** How far along, 0 to 1. A run with no total yet reads as nothing done. */
export function fraction(run: Run): number {
	if (!run.total) return 0;
	return Math.min(1, run.done / run.total);
}

// Under this many files the count says it all; a percentage adds nothing.
const WORTH_A_PERCENTAGE = 100;

/** How far along as a number, since a 1px bar cannot be read. */
export function percent(run: Run): string {
	if (run.total < WORTH_A_PERCENTAGE) return '';
	return `${Math.floor(fraction(run) * 100)}%`;
}

// Before this much of a run there is no rate worth extrapolating: the first
// files are cache hits. Only the fallback rate needs it.
const ENOUGH_FILES = 40;
const ENOUGH_SECONDS = 30;

// Under this much left a finish time adds nothing to the countdown.
const WORTH_A_CLOCK = 5 * 60;

/** A moment this many seconds ahead, stamped the way `soon` reads them. */
function ahead(seconds: number): string {
	return new Date(Date.now() + seconds * 1000).toISOString();
}

/** "about 3h 40m left · done 6:15 am", hedged as the caller wants it. */
function until(seconds: number, hedge: string): string {
	const by = seconds >= WORTH_A_CLOCK ? ` · done ${soon(ahead(seconds))}` : '';
	return `${hedge} ${duration(seconds)} left${by}`;
}

/**
 * Roughly how much longer, plus a finish time once that is far enough off.
 *
 * An applying sweep's time is rewriting, which the service estimates itself
 * ("at least" while still walking). A reporting sweep is probes, and the rate
 * so far is all there is. Empty rather than a bad guess: too early, no total,
 * stopping, or paused.
 */
export function remaining(run: Run, paused = false, age = 0): string {
	if (paused || run.stopping) return '';
	if (run.rewrite_seconds) {
		// Counts down between snapshots. Nothing once it runs out: an encode past
		// the machine's own rate, which the next snapshot answers.
		const left = run.rewrite_seconds - age;
		if (left <= 0) return '';
		const found = run.queued ? `${run.queued.toLocaleString()} to rewrite · ` : '';
		return found + until(left, run.walking ? 'at least' : 'about');
	}
	const seconds = run.seconds + age;
	if (!run.total || run.done < ENOUGH_FILES || seconds < ENOUGH_SECONDS) return '';
	const left = run.total - run.done;
	if (left <= 0) return '';
	return until((seconds / run.done) * left, 'about');
}

// One line under a run: a file being worked on, or one finished with. One
// shape for both moments.
export type FileRow = {
	path: string;
	seconds: number;
	// Set while a thread still has it, which draws the bar.
	live: ActiveFile | null;
	// Set once released. Empty on a live row and for the poll before the
	// verdict lands.
	verdict: string;
	detail: string;
};

/** The files under a run: live first, then finished newest first. */
export function fileRows(run: Run): FileRow[] {
	return [
		...run.active.map((file) => ({
			path: file.path,
			seconds: file.seconds,
			live: file,
			verdict: '',
			detail: ''
		})),
		...(run.recent ?? []).map((done) => ({
			path: done.path,
			seconds: done.seconds,
			live: null,
			verdict: done.status,
			detail: done.detail
		}))
	];
}

/** What one file's worker logged. Its own request, since every tab polls the
 * snapshot. Empty is ordinary for a file far enough back. */
export async function getRunLog(run: string, path: string): Promise<string[]> {
	const query = new URLSearchParams({ run, path });
	const answer = await request<{ lines?: string[] }>(`/api/runs/log?${query}`);
	return answer.lines ?? [];
}

/** Seconds of the file written by now: ffmpeg's last reading carried forward
 * at its speed for `age` seconds, never past the end. */
function written(file: ActiveFile, age: number): number {
	if (file.stage !== 'encoding' || file.speed <= 0) return file.done;
	return Math.min(file.duration, file.done + file.speed * age);
}

/** How far into the file its rewrite has written, 0 to 1. */
export function fileFraction(file: ActiveFile, age = 0): number {
	if (!file.duration) return 0;
	return Math.min(1, written(file, age) / file.duration);
}

/**
 * Seconds this file has left, or null when nothing honest can be said: not an
 * encode, no duration, or no speed yet. From ffmpeg's speed rather than elapsed
 * time, since the file queued for a slot first.
 */
export function fileLeft(file: ActiveFile, age = 0): number | null {
	if (file.stage !== 'encoding' || !file.duration || file.speed <= 0) return null;
	const left = (file.duration - written(file, age)) / file.speed;
	return left > 0 ? left : null;
}

/** Whether this file has a bar to draw, which only an encode ever does. */
export function fileBar(file: ActiveFile): boolean {
	return file.stage === 'encoding' && file.duration > 0;
}

/** Beside the file's bar: how far through and how long left, or why there is
 * no bar. A slot wait gets a line rather than reading as nothing happening. */
export function fileStatus(file: ActiveFile, age = 0): string {
	if (file.stage === 'waiting') return 'Waiting for a free rewrite slot';
	if (!fileBar(file)) return '';
	const far = `${Math.floor(fileFraction(file, age) * 100)}%`;
	const left = fileLeft(file, age);
	return left === null ? far : `${far} · ${duration(left)} left`;
}

// Reading order: what changed, what needs a look, then the untouched majority.
// The same order the events page uses.
const VERDICTS = ['fixed', 'would-fix', 'failed', 'deferred', 'conform', 'skip', 'unsupported'];

// Carries the palette's one warning colour. Not `deferred`: most deferrals are
// a download client still holding a hardlink.
const TROUBLE = new Set(['failed']);

export type Verdict = {
	state: string;
	label: string;
	hint: string;
	count: number;
	dot: string;
	trouble: boolean;
};

/** A count of verdicts in reading order, with $lib/library's words and colours
 * so the run and its history summary agree. */
export function tally(counts: Record<string, number>): Verdict[] {
	return VERDICTS.filter((verdict) => counts[verdict]).map((verdict) => ({
		state: verdict,
		label: verdictLabel(verdict),
		hint: verdictHint(verdict),
		count: counts[verdict],
		dot: pip[verdict] ?? 'bg-faint',
		trouble: TROUBLE.has(verdict)
	}));
}

export function verdicts(run: Run): Verdict[] {
	return tally(run.counts);
}

/** The count on one line, for where a row is too much. */
export function phrase(verdict: Verdict): string {
	return `${verdict.count.toLocaleString()} ${verdict.label.toLowerCase()}`;
}
