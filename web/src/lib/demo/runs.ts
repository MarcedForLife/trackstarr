// The demo's runs: a sweep or re-check walks the library, probes what it has
// not seen, rewrites what the rules say and closes with a summary, a second at
// a time on a timer. What the pages read is the same snapshot the service
// serves; what changes between snapshots is published to the stream, so the
// pages refetch exactly as they would against a service.

import {
	queueKey,
	type FileCovers,
	type FilePlans,
	type QueueItem,
	type QueuePage
} from '$lib/queue';
import type { Activity, ActiveFile, DoneFile, Run } from '$lib/runs';
import { nextRun } from './cron';
import { added, changesOf } from './judge';
import { configOf } from './settings';
import { publish } from './stream';
import {
	digest,
	IMPORT_STARTED_AGO_MS,
	jitter,
	runId,
	stamp,
	SWEEP_STARTED_AGO_MS,
	VERSION
} from './util';
import {
	addVariants,
	addLargeSeries,
	bytesOf,
	pausedTitle,
	pausesNow,
	probe,
	record,
	rejudge,
	reset,
	rewrite,
	currentState,
	type File,
	type SimRun,
	type Title,
	type State,
	sourceName
} from './state';

// One file a run has picked up, with what the simulation needs to carry it on.
type Active = ActiveFile & { since: number; readyAt: number; discovery?: boolean };

const TICK_MS = 1000;
// The probe and staging before an encode starts, and how long a walk takes.
const WORKING_MS = 2000;
const WALK_MS = 4000;
const RECHECK_WALK_MS = 800;
// The realtime multiples an encode runs at: a stream copy and one AAC encode,
// disk-bound.
const SPEED_LOW = 6;
const SPEED_SPREAD = 5;
// How much of the queue and the finished list a snapshot carries.
const UPCOMING = 8;
const RECENT = 24;

/** What a rewrite of this file would run at, the same every time it is asked. */
function speedOf(file: File): number {
	return Math.round((SPEED_LOW + jitter(file.path) * SPEED_SPREAD) * 10) / 10;
}

function configId(state: State): string {
	return digest(JSON.stringify(configOf(state.settings, VERSION)));
}

function isActive(file: ActiveFile): file is Active {
	return 'since' in file;
}

// The console's format, which is what the service keeps per file. A clock, the
// level in a seven-wide column, then the message.
function log(state: State, run: string, path: string, line: string, level = 'INFO'): void {
	const key = `${run}|${path}`;
	const lines = state.logs.get(key) ?? [];
	lines.push(`${new Date().toISOString().slice(11, 19)} ${level.padEnd(7)} ${line}`);
	state.logs.set(key, lines);
}

export function runLog(state: State, run: string, path: string): string[] {
	return state.logs.get(`${run}|${path}`) ?? [];
}

function newRun(
	state: State,
	type: SimRun['type'],
	started: number,
	dryRun: boolean,
	label = '',
	instance_id = ''
): SimRun {
	return {
		id: runId(started),
		type,
		started: stamp(started),
		seconds: 0,
		dry_run: dryRun,
		label,
		instance_id,
		total: 0,
		done: 0,
		counts: {},
		stopping: false,
		active: [],
		recent: [],
		queue: [],
		walkUntil: null,
		source: type === 'import' ? 'webhook' : type,
		titles: [],
		stopped: 0,
		cached: 0
	};
}

function pickUp(state: State, run: SimRun, file: File, now: number, discovery = false): Active {
	const active: Active = {
		path: file.path,
		seconds: 0,
		stage: 'working',
		discovery,
		duration: 0,
		done: 0,
		speed: 0,
		skipped: false,
		since: now,
		readyAt: now + WORKING_MS
	};
	run.active.push(active);
	log(state, run.id, file.path, `probing ${file.path}`);
	return active;
}

/** The runs going as the demo opens: a sweep somebody started by hand, part
 * way through the pending files, and an import waiting on its rewrite slot. */
export function seed(state: State): void {
	const now = state.born;
	const sweep = newRun(state, 'sweep', now - SWEEP_STARTED_AGO_MS, false);
	const files = state.titles
		.flatMap((title) => title.files)
		.filter((file) => file.tracks.length || file.status === 'unsupported');
	const pending = files.filter(
		(file) =>
			file.status === 'pending' && !file.hardlinked && !pausedTitle(state, file.title, file.path)
	);
	const [first, ...rest] = pending;
	for (const file of files) {
		if (pending.includes(file)) continue;
		const status = doneRow(state, file, now)?.status ?? file.status;
		sweep.counts[status] = (sweep.counts[status] ?? 0) + 1;
	}
	sweep.total = files.length;
	sweep.cached = files.length;
	sweep.queue = rest.map((file) => ({ path: file.path, skipped: false }));
	sweep.done = files.length - pending.length;
	const encoding: Active = {
		path: first.path,
		seconds: 58,
		stage: 'encoding',
		duration: first.seconds,
		done: first.seconds * 0.38,
		speed: speedOf(first),
		skipped: false,
		since: now - 58_000,
		readyAt: now - 56_000
	};
	sweep.active.push(encoding);
	log(state, sweep.id, first.path, `probing ${first.path}`);
	log(state, sweep.id, first.path, `plan: ${(first.why.reasons ?? []).join(' · ')}`);
	log(state, sweep.id, first.path, ffmpegLine(first));
	for (const file of files) {
		const done = doneRow(state, file, now);
		if (done) {
			sweep.recent.unshift(done);
			log(state, sweep.id, file.path, `probing ${file.path}`);
			log(
				state,
				sweep.id,
				file.path,
				done.status === 'modified' ? `rewrote ${file.path}` : `${done.status}: ${done.detail}`
			);
		}
	}
	state.runs.push(sweep);

	const importRun = newRun(state, 'import', now - IMPORT_STARTED_AGO_MS, false, '', 'radarr');
	const delivered = state.byId.get('arr:radarr:9')!.files[0];
	importRun.total = 1;
	state.runs.push(importRun);
	// This delivery arrived minutes ago: its independent check has already
	// finished even though the sweep still occupies the rewrite slot.
	queued(state);
	judged(
		state,
		importRun,
		pickUp(state, importRun, delivered, now),
		now,
		{ runs: false, progress: false, library: false, events: false },
		true
	);
}

/** The row the running sweep already has for a file it finished with, from
 * what the catalogue says happened to it. */
function doneRow(state: State, file: File, now: number): DoneFile | null {
	if (file.modified && Date.parse(file.modified.at) > now - SWEEP_STARTED_AGO_MS) {
		return {
			path: file.path,
			status: 'modified',
			seconds: Math.round((file.seconds / speedOf(file)) * 10) / 10,
			detail: rewriteDetail(state, file)
		};
	}
	if (file.hardlinked)
		return { path: file.path, status: 'deferred', seconds: 0.4, detail: HARDLINKED };
	return null;
}

const HARDLINKED = 'a download client still has this hard-linked';

/** What a rewrite did, for a row: the generated track it added, as the plan
 * said. */
function rewriteDetail(state: State, file: File): string {
	const made = file.modified?.added?.length ?? 0;
	void state;
	return made ? `add ${made === 1 ? '2.0 downmix' : `${made} downmixes`}` : 'rewritten';
}

function ffmpegLine(file: File): string {
	const maps = file.planned
		.map((track) => (track.src === undefined ? '' : `-map 0:${track.src}`))
		.filter(Boolean);
	const generated = file.planned.find((track) => track.flags?.includes('generated'));
	const encode = generated
		? ` -c:a:${file.planned.filter((t) => t.kind === 'audio').indexOf(generated)} ${generated.codec} -ac ${generated.channels} -b:a ${Math.round((generated.bitrate ?? 0) / 1000)}k`
		: '';
	return `ffmpeg -hide_banner -nostdin -y -i "${file.path}" ${maps.join(' ')} -c copy${encode} "/work/${file.name}"`;
}

// Queue order belongs to this simulated service instance, just like its runs.
// `drawn` is what the last page held, since the simulation has to notice its
// own changes where the service counts them as it makes them.
type Ordering = {
	ranks: Map<string, number>;
	next: number;
	front: number;
	epoch: string;
	revision: number;
	drawn: string;
	token: number;
	undo: { token: string; ranks: Map<string, number> } | null;
};
const orderings = new WeakMap<State, Ordering>();
function ordering(state: State): Ordering {
	let order = orderings.get(state);
	if (!order) {
		order = {
			ranks: new Map(),
			next: 0,
			front: 0,
			epoch: digest(String(Date.now())),
			revision: 0,
			drawn: '',
			token: 0,
			undo: null
		};
		orderings.set(state, order);
	}
	return order;
}
export function queued(state: State): QueueItem[] {
	const order = ordering(state);
	const items = state.runs
		.filter((run) => !run.stopping)
		.flatMap((run) =>
			[
				...run.active.filter((file) => file.stage === 'waiting'),
				...run.queue.filter((file) => !file.skipped)
			].map((item) => {
				const file = state.byPath.get(item.path);
				return {
					run: run.id,
					path: item.path,
					expected:
						file?.status === 'pending' && !run.dry_run
							? Math.round(file.seconds / speedOf(file))
							: 0
				};
			})
		);
	for (const item of items) {
		const key = queueKey(item);
		if (!order.ranks.has(key)) order.ranks.set(key, ++order.next);
	}
	const checking = new Set(
		state.runs.flatMap((run) =>
			run.queue
				.filter((file) => file.discovery)
				.map((file) => queueKey({ run: run.id, path: file.path }))
		)
	);
	return items
		.sort(
			(a, b) =>
				Number(checking.has(queueKey(b))) - Number(checking.has(queueKey(a))) ||
				order.ranks.get(queueKey(a))! - order.ranks.get(queueKey(b))!
		)
		.map((item, at) => ({ ...item, position: at + 1 }));
}
function coversFor(state: State, paths: string[]): FileCovers {
	return Object.fromEntries(
		paths.flatMap((path) => {
			const title = (
				state.byPath.get(path)?.title ??
				state.titles.find((title) => title.spec.sources.some((held) => held.folder === path))
			)?.spec;
			return title ? [[path, { id: title.id, name: title.name }]] : [];
		})
	);
}
/** What a rewrite would do to each file, for the rows that show it before
 * anything opens them. A file no sweep has judged is absent. */
function plansFor(state: State, paths: string[]): FilePlans {
	return Object.fromEntries(
		paths.flatMap((path) => {
			const file = state.byPath.get(path);
			if (!file || !file.judged) return [];
			const drops = file.planned.length
				? file.tracks.filter((track) => !file.planned.some((kept) => kept.src === track.index))
						.length
				: 0;
			return [
				[
					path,
					{
						status: file.status,
						changes: (file.why.reasons?.length ?? 0) + (file.why.incidental?.length ?? 0),
						adds: added(file.planned),
						// The demo has no rebuilds: it never claims a drop for a
						// generated track the way the planner does.
						rebuilds: [],
						drops
					}
				]
			];
		})
	);
}
export function queuePage(state: State, query = '', offset = 0, limit = 50): QueuePage {
	const items = queued(state),
		matches = items.filter((item) => item.path.toLowerCase().includes(query.toLowerCase())),
		page = matches.slice(offset, offset + limit),
		paths = page.map((item) => item.path);
	return {
		total: items.length,
		matched: matches.length,
		offset,
		epoch: ordering(state).epoch,
		revision: revised(state, items),
		items: page,
		covers: coversFor(state, paths),
		plans: plansFor(state, paths),
		plans_current: state.current
	};
}

/** The version of what a page would draw. The service counts its own changes as
 * it makes them, and the simulation has only the rows, so it compares them. */
function revised(state: State, items: QueueItem[]): number {
	const order = ordering(state),
		drawn = items.map(queueKey).join('\n');
	if (drawn !== order.drawn) {
		order.drawn = drawn;
		order.revision++;
	}
	return order.revision;
}
export function reorder(state: State, items: QueueItem[]) {
	const order = ordering(state),
		keys = new Set(items.map(queueKey)),
		selected = queued(state).filter((item) => keys.has(queueKey(item)));
	if (!selected.length) return { moved: 0, undo: null };
	const token = String(++order.token);
	order.undo = { token, ranks: new Map(order.ranks) };
	order.front -= selected.length;
	selected.forEach((item, index) => order.ranks.set(queueKey(item), order.front + index));
	publish('runs');
	return { moved: selected.length, undo: token };
}
export function undoQueue(state: State, token: unknown): boolean {
	const order = ordering(state);
	if (!order.undo || order.undo.token !== token) return false;
	for (const [key, rank] of order.undo.ranks) if (order.ranks.has(key)) order.ranks.set(key, rank);
	order.undo = null;
	publish('runs');
	return true;
}

// The simulation.

let ticking: ReturnType<typeof setInterval> | null = null;
let lastTick = 0;

/** Keep the runs moving for as long as the tab lives. Idempotent. */
export function start(state: State): void {
	if (ticking) return;
	lastTick = Date.now();
	ticking = setInterval(() => tick(state), TICK_MS);
}

export function stopTicking(): void {
	if (ticking) clearInterval(ticking);
	ticking = null;
	lastTick = 0;
}

type Changed = { runs: boolean; progress: boolean; library: boolean; events: boolean };

/** How many rewrites may run at once. */
function slots(state: State): number {
	return Math.max(1, Number(state.settings.MAX_CONCURRENT_REWRITES) || 1);
}

/** The same, less the ones running. */
function freeSlots(state: State): number {
	const most = slots(state);
	const busy = state.runs.reduce(
		(count, run) =>
			count +
			run.active.filter((file) => file.stage !== 'waiting' && !(isActive(file) && file.discovery))
				.length,
		0
	);
	return most - busy;
}

export function tick(state: State, now = Date.now()): void {
	// The first tick moves nothing: there is no last one to measure from.
	const dt = lastTick ? Math.min(600, (now - lastTick) / 1000) : 0;
	lastTick = now;
	const changed: Changed = { runs: false, progress: false, library: false, events: false };
	const runs = [...state.runs];
	for (const run of runs) {
		if (run.walkUntil !== null && now >= run.walkUntil) {
			finishWalk(state, run);
			changed.runs = true;
		}
		for (const active of [...run.active]) {
			if (!isActive(active)) continue;
			if (active.stage === 'working' && now >= active.readyAt) {
				judged(state, run, active, now, changed, active.discovery);
			} else if (active.stage === 'encoding') {
				active.done = Math.min(active.duration, active.done + active.speed * dt);
				changed.progress = true;
				if (active.done >= active.duration) complete(state, run, active, now, changed);
			}
		}
	}
	for (const item of queued(state)) {
		if (state.paused) break;
		const run = state.runs.find((run) => run.id === item.run)!;
		const discovery = run.queue.some((file) => file.path === item.path && file.discovery);
		if (discovery) {
			const probing = state.runs.reduce(
				(count, other) =>
					count + other.active.filter((file) => isActive(file) && file.discovery).length,
				0
			);
			if (probing >= Math.max(1, Number(state.settings.PROBE_WORKERS) || 1)) continue;
		} else if (freeSlots(state) <= 0) continue;
		if (run.walkUntil !== null) continue;
		if (
			state.runs.some((other) =>
				other.active.some((file) => file.path === item.path && file.stage !== 'waiting')
			)
		)
			continue;
		const waiting = run.active.find((file) => file.path === item.path && file.stage === 'waiting');
		if (waiting && isActive(waiting)) {
			waiting.stage = 'working';
			waiting.since = now;
			waiting.readyAt = now + WORKING_MS;
		} else {
			run.queue = run.queue.filter((file) => file.path !== item.path);
			const file = state.byPath.get(item.path);
			if (!file) continue;
			pickUp(state, run, file, now, discovery);
		}
		changed.runs = true;
	}
	for (const run of runs) {
		const skipped = run.queue.filter((item) => item.skipped);
		run.done += skipped.length;
		run.queue = run.queue.filter((item) => !item.skipped);
		// The last probe has landed, so every plan is this ruleset's. Not for a
		// walk stopped part way, which left the rest unjudged.
		if (run.type === 'sweep' && !state.current && !run.stopping && !finding(run)) {
			state.current = true;
			changed.library = true;
		}
		if (run.walkUntil === null && !run.active.length && (!run.queue.length || run.stopping)) {
			close(state, run, now);
			changed.runs = true;
			changed.events = true;
		}
	}
	announce(changed);
}

/** Tell the pages what moved. Progress is the lighter word for runs, so a tick
 * that only advanced an encode does not make every page refetch. */
function announce(changed: Changed): void {
	if (changed.runs) publish('runs');
	else if (changed.progress) publish('progress');
	if (changed.library) publish('library');
	if (changed.events) publish('events');
}

/** The listing is over: what the walk can take from the cache, and what it
 * queues to open or rewrite. The probes run from the queue like any other
 * work, a few at a time, which is what the service does with them. */
function finishWalk(state: State, run: SimRun): void {
	const files =
		run.type === 'recheck'
			? (run.files ?? run.titles.flatMap((title) => title.files))
			: state.titles.flatMap((title) => title.files);
	const opening = (file: File) => run.type === 'recheck' || !state.current || !file.tracks.length;
	let probes = 0;
	for (const file of files) {
		if (opening(file)) {
			probes += 1;
			run.queue.push({ path: file.path, skipped: false, discovery: true });
		} else if (file.status === 'pending' && !run.dry_run) {
			run.queue.push({ path: file.path, skipped: false });
		} else {
			run.counts[file.status] = (run.counts[file.status] ?? 0) + 1;
			run.done += 1;
		}
	}
	run.total = files.length;
	run.cached = files.length - probes;
	run.walkUntil = null;
}

/** Whether a run is still finding work: listing, or probing what it listed.
 * An import is handed its files, so it never walks. */
function finding(run: SimRun): boolean {
	if (run.type === 'import') return false;
	return (
		run.walkUntil !== null ||
		run.queue.some((item) => item.discovery) ||
		run.active.some((file) => isActive(file) && file.discovery)
	);
}

/** A probe has finished: the file is judged, and either rewritten, reported or
 * left for the reason it cannot be rewritten. */
function judged(
	state: State,
	run: SimRun,
	active: Active,
	now: number,
	changed: Changed,
	discovery = false
): void {
	const file = state.byPath.get(active.path)!;
	const opened = !file.tracks.length;
	if (opened || run.type === 'recheck' || !state.current) probe(file);
	rejudge(state, file, now);
	changed.library = true;
	const pause = pausedTitle(state, file.title, file.path);
	if (file.status !== 'pending') {
		settle(
			state,
			run,
			active,
			file,
			file.status,
			(file.why.skip ?? file.why.reasons?.join(' · ')) || 'nothing to do',
			now,
			changed
		);
		return;
	}
	if (file.hardlinked) {
		defer(state, run, active, file, HARDLINKED, now, changed);
		return;
	}
	if (run.dry_run || pause) {
		const detail = pause
			? pause.until
				? `paused until ${pause.until}`
				: 'paused'
			: (file.why.reasons ?? []).join(' · ');
		settle(state, run, active, file, 'pending', detail, now, changed);
		return;
	}
	if (discovery) {
		remove(run, active);
		run.queue.push({ path: file.path, skipped: false });
		const order = ordering(state);
		if (run.type === 'import' && !order.undo)
			order.ranks.set(queueKey({ run: run.id, path: file.path }), --order.front);
		changed.runs = true;
		return;
	}
	active.stage = 'encoding';
	active.duration = file.seconds;
	active.done = 0;
	active.speed = speedOf(file);
	active.since = now;
	log(state, run.id, file.path, `plan: ${(file.why.reasons ?? []).join(' · ')}`);
	log(state, run.id, file.path, ffmpegLine(file));
	changed.runs = true;
}

function remove(run: SimRun, active: Active): void {
	run.active = run.active.filter((each) => each !== active);
}

function verdictLine(
	state: State,
	run: SimRun,
	file: File,
	kind: string,
	extra: Record<string, unknown>
): void {
	record(state, {
		event: kind,
		run: run.id,
		source: run.source,
		config_id: configId(state),
		path: file.path,
		reasons: file.why.reasons ?? [],
		rules: file.why.rules ?? [],
		incidental: file.why.incidental ?? [],
		incidental_rules: file.why.incidental_rules ?? [],
		title: file.title.spec.id,
		...extra
	});
}

/** A file the run is done with, at the verdict it reached without a rewrite. */
function settle(
	state: State,
	run: SimRun,
	active: Active,
	file: File,
	status: string,
	detail: string,
	now: number,
	changed: Changed
): void {
	const seconds = Math.round(((now - active.since) / 1000) * 10) / 10;
	run.counts[status] = (run.counts[status] ?? 0) + 1;
	run.done += 1;
	run.recent.unshift({ path: file.path, status, seconds, detail });
	log(state, run.id, file.path, `${status}: ${detail}`);
	if (status === 'pending') {
		verdictLine(state, run, file, 'pending', {});
		changed.events = true;
	}
	remove(run, active);
	changed.runs = true;
}

/** A rewrite that did not happen: the file is as it was and the next sweep
 * tries again. */
function defer(
	state: State,
	run: SimRun,
	active: Active,
	file: File,
	detail: string,
	now: number,
	changed: Changed
): void {
	const seconds = Math.round(((now - active.since) / 1000) * 10) / 10;
	run.counts.deferred = (run.counts.deferred ?? 0) + 1;
	run.done += 1;
	run.recent.unshift({ path: file.path, status: 'deferred', seconds, detail });
	log(state, run.id, file.path, `deferred ${file.path}: ${detail}`);
	verdictLine(state, run, file, 'deferred', { seconds, duration: file.seconds, detail });
	remove(run, active);
	changed.runs = true;
	changed.events = true;
}

/** The encode is done: the plan becomes the file. */
function complete(state: State, run: SimRun, active: Active, now: number, changed: Changed): void {
	const file = state.byPath.get(active.path)!;
	const seconds = Math.round(((now - active.since) / 1000) * 10) / 10;
	const reasons = file.why.reasons ?? [];
	const planned = file.planned;
	const before = { ...file.why };
	const made = rewrite(state, file, now);
	record(state, {
		event: 'modified',
		run: run.id,
		source: run.source,
		config_id: configId(state),
		reasons,
		rules: before.rules ?? [],
		incidental: before.incidental ?? [],
		incidental_rules: before.incidental_rules ?? [],
		...changesOf(planned, made.was ?? []),
		seconds,
		waited: 0,
		duration: file.seconds,
		path: file.path,
		bytes_before: made.bytes_before,
		bytes_after: made.bytes_after,
		title: file.title.spec.id
	});
	run.counts.modified = (run.counts.modified ?? 0) + 1;
	run.done += 1;
	run.recent.unshift({ path: file.path, status: 'modified', seconds, detail: reasons.join(' · ') });
	log(state, run.id, file.path, `encoded at ${active.speed}x in ${seconds}s`);
	log(state, run.id, file.path, 'duration and stream count verify. Published over the original');
	remove(run, active);
	changed.runs = true;
	changed.library = true;
	changed.events = true;
}

/** The run is over: its summary, and the library's word that it happened. */
function close(state: State, run: SimRun, now: number): void {
	run.stopped = run.queue.length;
	run.queue = [];
	const seconds = Math.round(((now - Date.parse(run.started)) / 1000) * 10) / 10;
	if (run.type === 'sweep') {
		state.swept += 1;
		record(state, {
			event: 'sweep',
			run: run.id,
			dry_run: run.dry_run,
			files: run.done,
			library_bytes: state.titles
				.flatMap((title) => title.files)
				.reduce((sum, file) => sum + file.bytes, 0),
			config: configOf(state.settings, VERSION),
			config_id: configId(state),
			cached: run.cached,
			counts: run.counts,
			seconds,
			...(run.stopped ? { stopped: run.stopped } : {})
		});
	} else if (run.type === 'recheck') {
		record(state, {
			event: 'recheck',
			run: run.id,
			dry_run: run.dry_run,
			...(run.files ? {} : { titles: run.titles.length }),
			files: run.done,
			config_id: configId(state),
			counts: run.counts,
			seconds,
			...(run.stopped ? { stopped: run.stopped } : {})
		});
	}
	state.runs = state.runs.filter((each) => each !== run);
}

// What the pages read.

export function mayRewrite(state: State): boolean {
	return state.settings.REWRITE_MODE === 'all';
}

function wireRun(state: State, run: SimRun, now: number): Run {
	const remaining = run.active.reduce(
		(sum, file) => sum + (file.stage === 'encoding' ? (file.duration - file.done) / file.speed : 0),
		0
	);
	const queued = run.queue.reduce((sum, item) => {
		const file = state.byPath.get(item.path);
		return sum + (file?.status === 'pending' && !run.dry_run ? file.seconds / speedOf(file) : 0);
	}, 0);
	return {
		id: run.id,
		type: run.type,
		started: run.started,
		seconds: Math.round((now - Date.parse(run.started)) / 1000),
		dry_run: run.dry_run,
		label: run.instance_id ? sourceName(state, run.instance_id) : run.label,
		total: run.total,
		done: run.done,
		counts: run.counts,
		queued: run.queue.length,
		walking: finding(run),
		rewrite_seconds: run.dry_run ? null : Math.round(remaining + queued),
		stopping: run.stopping,
		active: run.active.map((file) => {
			const { since, readyAt: _readyAt, ...wire } = file as Active;
			void _readyAt;
			return { ...wire, seconds: Math.round((now - since) / 1000) };
		}),
		upcoming: run.queue.slice(0, UPCOMING).map((item) => {
			const file = state.byPath.get(item.path);
			const expected =
				file?.status === 'pending' && !run.dry_run ? Math.round(file.seconds / speedOf(file)) : 0;
			return { path: item.path, expected, skipped: item.skipped };
		}),
		recent: run.recent.slice(0, RECENT),
		seen: 0
	};
}

export function activity(
	state: State,
	now = Date.now()
): Omit<Activity, 'runs'> & { runs: Omit<Run, 'seen'>[] } {
	const runs = state.runs.map((run) => {
		const { seen: _seen, ...wire } = wireRun(state, run, now);
		void _seen;
		return wire;
	});
	const next = nextRun(String(state.settings.SWEEP_AT ?? ''), new Date(now));
	const active = state.runs.flatMap((run) => run.active);
	return {
		queue_preview: queued(state).slice(0, 3),
		covers: coversFor(
			state,
			[...active, ...queued(state).slice(0, 3), ...pausesNow(state, now)].map((item) => item.path)
		),
		plans: plansFor(
			state,
			[...active, ...queued(state).slice(0, 3), ...pausesNow(state, now)].map((item) => item.path)
		),
		plans_current: state.current,
		paused: state.paused,
		paused_by: state.pausedBy,
		paused_at: state.pausedAt,
		up_since: state.upSince,
		runs,
		queue:
			state.runs.reduce((sum, run) => sum + run.queue.length, 0) +
			active.filter((file) => file.stage === 'waiting').length,
		working: active.filter((file) => file.stage !== 'waiting').length,
		rewrites: active.filter((file) => file.stage === 'encoding').length,
		slots: slots(state),
		parked: state.titles
			.flatMap((title) => title.files)
			.filter((file) => file.hardlinked && file.status === 'pending').length,
		may_rewrite: mayRewrite(state),
		next_sweep: next ? next.toISOString() : null,
		pauses: pausesNow(state, now)
	};
}

// The buttons.

export type Refused = { status: number; body: Record<string, unknown> };

/** An answer that refused rather than returned. */
export const refused = (answer: Refused | object): answer is Refused =>
	'body' in answer && typeof (answer as Refused).status === 'number';

type Delivered = { status: string; run: string; titles: string[] };

function walking(state: State): SimRun | undefined {
	return state.runs.find((run) => run.type !== 'import');
}

/** Demo debug delivery: replace sample media with a 4K upgrade missing stereo. */
export function simulateImport(
	state: State,
	arr: 'radarr' | 'sonarr',
	now = Date.now()
): Refused | Delivered {
	const occupied = new Set(
		state.runs.flatMap((run) => [
			...(run.type === 'import' ? run.queue.map((file) => file.path) : []),
			...run.active.map((file) => file.path)
		])
	);
	const title = state.titles.find(
		(title) =>
			title.spec.sources[0].instance_id === arr &&
			title.files.length &&
			title.files.every((file) => !occupied.has(file.path) && !pausedTitle(state, title, file.path))
	);
	if (!title) return { status: 409, body: { status: 'all sample titles are busy or paused' } };
	const files = title.files.slice(0, arr === 'sonarr' ? 3 : 1);
	const run = newRun(state, 'import', now, state.settings.REWRITE_MODE === 'report', '', arr);
	// Multiple deliveries can share a millisecond in tests or a fast browser.
	run.id += `-${state.nextSeq}`;
	for (const file of files) {
		const spec = title.spec.files[title.files.indexOf(file)];
		const oldPath = file.path;
		const name = spec.name.replace(/(?:Bluray|WEB|HDTV)-\d+p/i, 'Bluray-2160p');
		file.ext = '.mkv';
		file.path = `${title.spec.sources[0].folder}/${name === spec.name ? `${name} Bluray-2160p` : name}${file.ext}`;
		file.name = file.path.slice(file.path.lastIndexOf('/') + 1);
		state.byPath.delete(oldPath);
		state.byPath.set(file.path, file);
		// A sweep may already have discovered the old release but not opened it.
		for (const other of state.runs) {
			for (const queued of other.queue) {
				if (queued.path === oldPath) queued.path = file.path;
			}
		}
		file.contents = [
			{ index: 0, kind: 'video', codec: 'hevc', bitrate: 35_000_000, title: '4K UHD' },
			{
				index: 1,
				kind: 'audio',
				codec: 'eac3',
				channels: 6,
				lang: title.spec.lang ?? 'eng',
				bitrate: 768_000,
				flags: ['default']
			}
		];
		file.bytes = bytesOf(file.contents, spec.seconds);
		file.tracks = [];
		file.planned = [];
		file.seconds = 0;
		file.status = 'unchecked';
		file.why = {};
		file.judged = 0;
		delete file.modified;
		delete file.hardlinked;
		run.queue.push({ path: file.path, skipped: false, discovery: true });
	}
	run.total = files.length;
	state.runs.push(run);
	record(state, {
		event: 'webhook',
		arr,
		run: run.id,
		files: files.length,
		paths: files.map((file) => file.path),
		title: title.spec.id
	});
	publish('runs');
	publish('library');
	publish('events');
	return { status: 'imported', run: run.id, titles: [title.spec.name] };
}

// Clears the board and resumes, then delivers a film and a set of episodes,
// the mix an install with both *arrs wired to it sees. One service with
// nothing free still poses a board, so only both refusing is a refusal.
function importsOnly(state: State, now: number): Refused | { status: string; titles: string[] } {
	abort(state, now);
	setPaused(state, false, 'demo', now);
	const titles: string[] = [];
	let refusal: Refused | undefined;
	for (const arr of ['radarr', 'sonarr'] as const) {
		const delivery = simulateImport(state, arr, now);
		if (refused(delivery)) refusal = delivery;
		else titles.push(...delivery.titles);
	}
	return titles.length ? { status: 'imported', titles } : (refusal as Refused);
}

// The board the demo opens on, rebuilt from the catalogue rather than rewound,
// which is what a reload does, so the library and the history come back with
// the runs. The account carries over, since posing a board is not a sign-out.
// Every scenario below starts here, so pressing one twice lands the same way.
function poseOpening(state: State): State {
	const account = state.account;
	stopTicking();
	reset();
	const fresh = currentState();
	fresh.account = account;
	seed(fresh);
	fresh.seeded = true;
	start(fresh);
	publish('runs');
	publish('library');
	publish('events');
	return fresh;
}

function fullBoard(state: State): { status: string; titles: string[] } {
	poseOpening(state);
	return { status: 'posed', titles: [] };
}

const BROKE = 'ffmpeg exited 1: Invalid data found when processing input';

// The one verdict the sample library never reaches on its own: an opening board
// carrying a broken rewrite looks unhealthy, so it lives behind a button. The
// encode in flight is the file that breaks, since that is where a real one does.
function failedRewrite(state: State, now: number): { status: string; titles: string[] } {
	const fresh = poseOpening(state);
	const changed: Changed = { runs: true, progress: false, library: true, events: true };
	const broken: string[] = [];
	for (const run of fresh.runs) {
		for (const active of [...run.active]) {
			if (!isActive(active) || active.stage !== 'encoding') continue;
			const file = fresh.byPath.get(active.path)!;
			file.failure = BROKE;
			rejudge(fresh, file, now);
			const seconds = Math.round(((now - active.since) / 1000) * 10) / 10;
			run.counts.failed = (run.counts.failed ?? 0) + 1;
			run.done += 1;
			run.recent.unshift({ path: file.path, status: 'failed', seconds, detail: BROKE });
			log(fresh, run.id, file.path, `failed: ${BROKE}`);
			verdictLine(fresh, run, file, 'failed', { seconds, duration: file.seconds, detail: BROKE });
			remove(run, active);
			broken.push(file.title.spec.name);
		}
	}
	announce(changed);
	return { status: 'posed', titles: broken };
}

// Everything held with work behind it. The files in flight go back to the front
// of their queue rather than finishing, so the board reads as stopped rather
// than as one last encode running under a pause.
function heldWork(state: State, now: number): { status: string; titles: string[] } {
	const fresh = poseOpening(state);
	for (const run of fresh.runs) {
		for (const active of [...run.active]) run.queue.unshift({ path: active.path, skipped: false });
		run.active = [];
	}
	setPaused(fresh, true, 'demo', now);
	return { status: 'posed', titles: [] };
}

/** The boards the debug panel can pose, by the name it posts. */
export const SCENARIOS: Record<
	string,
	(state: State, now: number) => Refused | { status: string; titles: string[] }
> = {
	full: fullBoard,
	'multiple-variants': (state, now) => {
		const fresh = poseOpening(state);
		abort(fresh, now);
		return { status: 'posed', titles: addVariants(fresh, now) };
	},
	'large-series': (state, now) => {
		const fresh = poseOpening(state);
		abort(fresh, now);
		return { status: 'posed', titles: addLargeSeries(fresh, now) };
	},
	'connection-trouble': (state, now) => {
		const fresh = poseOpening(state);
		abort(fresh, now);
		fresh.connectionTrouble = true;
		return { status: 'posed', titles: [] };
	},
	'imports-only': (state, now) => importsOnly(poseOpening(state), now),
	failed: failedRewrite,
	held: heldWork
};

export function startSweep(
	state: State,
	mode: string,
	now = Date.now()
): Refused | { status: string; run: string } {
	const going = walking(state);
	if (going) return { status: 409, body: { status: 'a sweep is already running', run: going.id } };
	const run = newRun(state, 'sweep', now, mode !== 'apply' || !mayRewrite(state));
	run.walkUntil = now + WALK_MS;
	run.total = state.titles.flatMap((title) => title.files).length;
	state.runs.push(run);
	publish('runs');
	return { status: 'started', run: run.id };
}

export function recheck(
	state: State,
	titles: Title[],
	mode: string,
	now = Date.now(),
	files?: File[]
): Refused | { status: string; run: string; titles: number } {
	if (!['report', 'apply'].includes(mode))
		return { status: 400, body: { status: 'unknown run mode' } };
	if (state.paused)
		return { status: 409, body: { status: 'processing is paused. Resume it first' } };
	const label = files
		? files.length === 1
			? files[0].name
			: `${files.length} files`
		: titles.length === 1
			? titles[0].spec.name
			: `${titles.length} titles`;
	const run = newRun(state, 'recheck', now, mode !== 'apply' || !mayRewrite(state), label);
	run.titles = titles;
	run.files = files;
	run.walkUntil = now + RECHECK_WALK_MS;
	run.total = (files ?? titles.flatMap((title) => title.files)).length;
	state.runs.push(run);
	publish('runs');
	return { status: 'started', run: run.id, titles: titles.length };
}

export function stopRun(state: State, id: string): Refused | { status: string } {
	const run = state.runs.find((each) => each.id === id);
	if (!run) return { status: 404, body: { status: 'no such run is going' } };
	run.stopping = true;
	publish('runs');
	return { status: 'stopping' };
}

export function setPaused(state: State, on: boolean, by: string, now = Date.now()): void {
	const began = state.pausedAt;
	state.paused = on;
	state.pausedBy = on ? by : '';
	state.pausedAt = on ? stamp(now) : '';
	record(
		state,
		on ? { event: 'paused', by } : { event: 'resumed', paused_at: began || undefined, by }
	);
	publish('runs');
	publish('events');
}

export function abort(
	state: State,
	now = Date.now()
): { status: string; stopped: number; rewrites: number } {
	const changed: Changed = { runs: true, progress: false, library: false, events: true };
	let rewrites = 0;
	const stopped = state.runs.length;
	for (const run of [...state.runs]) {
		for (const active of [...run.active]) {
			if (!isActive(active)) continue;
			if (active.stage === 'encoding') rewrites += 1;
			const file = state.byPath.get(active.path)!;
			defer(state, run, active, file, 'the run stopped', now, changed);
		}
		close(state, run, now);
	}
	publish('runs');
	publish('events');
	return { status: 'stopping', stopped, rewrites };
}

export function skip(
	state: State,
	id: string,
	path: string,
	by: string,
	now = Date.now()
): Refused | { status: string; where: string; rewrites: number } {
	const run = state.runs.find((each) => each.id === id);
	if (!run) return { status: 404, body: { status: 'that run is not going to reach that file' } };
	const active = run.active.find((file) => file.path === path);
	const queued = run.queue.find((item) => item.path === path);
	if (!active && !queued)
		return { status: 404, body: { status: 'that run is not going to reach that file' } };
	const file = state.byPath.get(path)!;
	let rewrites = 0;
	if (active) {
		rewrites = active.stage === 'encoding' ? 1 : 0;
		const seconds = isActive(active) ? Math.round(((now - active.since) / 1000) * 10) / 10 : 0;
		skipLine(state, run, file, by, 'active', stoppedWhere(active), seconds);
		if (isActive(active)) {
			// On the run's tally, as the service books it, with no verdict line.
			run.counts.deferred = (run.counts.deferred ?? 0) + 1;
			run.done += 1;
			run.recent.unshift({ path, status: 'deferred', seconds, detail: 'skipped for this run' });
			log(state, run.id, path, `deferred ${path}: skipped for this run`);
			remove(run, active);
		}
	} else if (queued) {
		queued.skipped = true;
		skipLine(state, run, file, by, 'waiting', 'taken off the run before a worker reached it');
	}
	publish('runs');
	publish('events');
	return { status: 'skipped', where: active ? 'active' : 'queued', rewrites };
}

/** Where the worker was with the file, in the service's words. */
function stoppedWhere(active: ActiveFile): string {
	if (active.stage === 'waiting') return 'stopped while it waited for a rewrite slot';
	if (active.stage === 'encoding' && active.duration) {
		const percent = Math.round((100 * active.done) / active.duration);
		return `stopped ${percent}% into the rewrite, nothing written`;
	}
	return 'stopped while it was being checked';
}

/** The one line a skip leaves. Empty plan fields are left off, as the service
 * leaves them. */
function skipLine(
	state: State,
	run: SimRun,
	file: File,
	by: string,
	where: string,
	detail: string,
	seconds?: number
): void {
	const plan: Record<string, unknown> = {
		reasons: file.why.reasons,
		incidental: file.why.incidental,
		rules: file.why.rules,
		incidental_rules: file.why.incidental_rules,
		...changesOf(file.planned, file.tracks)
	};
	const told = Object.fromEntries(
		Object.entries(plan).filter(([, value]) => (Array.isArray(value) ? value.length : value))
	);
	record(state, {
		event: 'skipped',
		run: run.id,
		path: file.path,
		by,
		where,
		detail,
		...(seconds ? { seconds } : {}),
		...told,
		title: file.title.spec.id
	});
}
