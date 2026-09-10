// The demo's runs: a sweep or re-check walks the library, probes what it has
// not seen, rewrites what the rules say and closes with a summary, a second at
// a time on a timer. What the pages read is the same snapshot the service
// serves; what changes between snapshots is published to the stream, so the
// pages refetch exactly as they would against a service.

import type { Activity, ActiveFile, DoneFile, Run } from '$lib/runs';
import { nextRuns } from './cron';
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
	heldTitle,
	holdsNow,
	probe,
	record,
	rejudge,
	rewrite,
	type File,
	type SimRun,
	type Title,
	type World
} from './world';

// One file a run has picked up, with what the simulation needs to carry it on.
type Active = ActiveFile & { since: number; readyAt: number };

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

function configId(world: World): string {
	return digest(JSON.stringify(configOf(world.settings, VERSION)));
}

function isActive(file: ActiveFile): file is Active {
	return 'since' in file;
}

function log(world: World, run: string, path: string, line: string): void {
	const key = `${run}|${path}`;
	const lines = world.logs.get(key) ?? [];
	lines.push(`${new Date().toISOString().slice(11, 19)} ${line}`);
	world.logs.set(key, lines);
}

export function runLog(world: World, run: string, path: string): string[] {
	return world.logs.get(`${run}|${path}`) ?? [];
}

function newRun(
	world: World,
	kind: SimRun['kind'],
	started: number,
	dryRun: boolean,
	label = ''
): SimRun {
	return {
		id: runId(started),
		kind,
		started: stamp(started),
		seconds: 0,
		dry_run: dryRun,
		label,
		total: 0,
		done: 0,
		counts: {},
		stopping: false,
		active: [],
		recent: [],
		queue: [],
		walkUntil: null,
		source: kind === 'import' ? 'webhook' : kind,
		titles: [],
		stopped: 0,
		cached: 0
	};
}

function pickUp(world: World, run: SimRun, file: File, now: number): Active {
	const active: Active = {
		path: file.path,
		seconds: 0,
		stage: 'working',
		duration: 0,
		done: 0,
		speed: 0,
		skipped: false,
		since: now,
		readyAt: now + WORKING_MS
	};
	run.active.push(active);
	log(world, run.id, file.path, `probing ${file.name}`);
	return active;
}

/** The runs going as the demo opens: a sweep somebody started by hand, part
 * way through the pending files, and an import waiting on its rewrite slot. */
export function seed(world: World): void {
	const now = world.born;
	const sweep = newRun(world, 'sweep', now - SWEEP_STARTED_AGO_MS, false);
	const files = world.titles
		.flatMap((title) => title.files)
		.filter((file) => file.tracks.length || file.status === 'unsupported');
	const pending = files.filter(
		(file) => file.status === 'pending' && !file.hardlinked && !heldTitle(world, file.title)
	);
	const [first, ...rest] = pending;
	for (const file of files) {
		if (pending.includes(file)) continue;
		const status = doneRow(world, file, now)?.status ?? file.status;
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
	log(world, sweep.id, first.path, `probing ${first.name}`);
	log(world, sweep.id, first.path, `plan: ${(first.why.reasons ?? []).join('; ')}`);
	log(world, sweep.id, first.path, ffmpegLine(first));
	for (const file of files) {
		const done = doneRow(world, file, now);
		if (done) {
			sweep.recent.unshift(done);
			log(world, sweep.id, file.path, `probing ${file.name}`);
			log(
				world,
				sweep.id,
				file.path,
				done.status === 'modified'
					? `published over the original after ${done.seconds}s`
					: `${done.status}: ${done.detail}`
			);
		}
	}
	world.runs.push(sweep);

	const importRun = newRun(world, 'import', now - IMPORT_STARTED_AGO_MS, false, 'radarr');
	const delivered = world.byId.get('arr:radarr:9')!.files[0];
	importRun.total = 1;
	importRun.active.push({
		path: delivered.path,
		seconds: 0,
		stage: 'waiting',
		duration: 0,
		done: 0,
		speed: 0,
		skipped: false,
		since: now - IMPORT_STARTED_AGO_MS,
		readyAt: 0
	} as Active);
	world.runs.push(importRun);
}

/** The row the running sweep already has for a file it finished with, from
 * what the catalogue says happened to it. */
function doneRow(world: World, file: File, now: number): DoneFile | null {
	if (file.modified && Date.parse(file.modified.at) > now - SWEEP_STARTED_AGO_MS) {
		return {
			path: file.path,
			status: 'modified',
			seconds: Math.round((file.seconds / speedOf(file)) * 10) / 10,
			detail: rewriteDetail(world, file)
		};
	}
	if (file.hardlinked)
		return { path: file.path, status: 'deferred', seconds: 0.4, detail: HARDLINKED };
	return null;
}

const HARDLINKED = 'hard-linked 2 times; a download client still has it';

/** What a rewrite did, for a row: the generated track it added, as the plan
 * said. */
function rewriteDetail(world: World, file: File): string {
	const made = file.modified?.added?.length ?? 0;
	void world;
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

// The simulation.

let ticking: ReturnType<typeof setInterval> | null = null;
let lastTick = 0;

/** Keep the runs moving for as long as the tab lives. Idempotent. */
export function start(world: World): void {
	if (ticking) return;
	lastTick = Date.now();
	ticking = setInterval(() => tick(world), TICK_MS);
}

export function stopTicking(): void {
	if (ticking) clearInterval(ticking);
	ticking = null;
	lastTick = 0;
}

type Changed = { runs: boolean; progress: boolean; library: boolean; events: boolean };

/** How many rewrites may run at once, less the ones running. */
function freeSlots(world: World): number {
	const most = Math.max(1, Number(world.settings.MAX_CONCURRENT_REWRITES) || 1);
	const busy = world.runs.reduce(
		(count, run) => count + run.active.filter((file) => file.stage !== 'waiting').length,
		0
	);
	return most - busy;
}

export function tick(world: World, now = Date.now()): void {
	// The first tick moves nothing: there is no last one to measure from.
	const dt = lastTick ? Math.min(600, (now - lastTick) / 1000) : 0;
	lastTick = now;
	const changed: Changed = { runs: false, progress: false, library: false, events: false };
	// Imports first: a delivery waiting on a slot is owed it before the sweep's
	// next file.
	const runs = [...world.runs].sort(
		(a, b) => Number(b.kind === 'import') - Number(a.kind === 'import')
	);
	for (const run of runs) {
		if (run.walkUntil !== null && now >= run.walkUntil) {
			finishWalk(world, run);
			changed.runs = true;
		}
		for (const active of [...run.active]) {
			if (!isActive(active)) continue;
			if (active.stage === 'working' && now >= active.readyAt) {
				judged(world, run, active, now, changed);
			} else if (active.stage === 'encoding') {
				active.done = Math.min(active.duration, active.done + active.speed * dt);
				changed.progress = true;
				if (active.done >= active.duration) complete(world, run, active, now, changed);
			}
		}
		while (!world.paused && !run.stopping && freeSlots(world) > 0) {
			const waiting = run.active.find((file) => file.stage === 'waiting');
			if (waiting && isActive(waiting)) {
				waiting.stage = 'working';
				waiting.since = now;
				waiting.readyAt = now + WORKING_MS;
				changed.runs = true;
				continue;
			}
			if (run.active.length || run.walkUntil !== null) break;
			const next = run.queue.shift();
			if (!next) break;
			const file = world.byPath.get(next.path);
			if (!file) continue;
			if (next.skipped) {
				run.done += 1;
				run.recent.unshift({ path: file.path, status: '', seconds: 0, detail: 'skipped' });
				continue;
			}
			pickUp(world, run, file, now);
			changed.runs = true;
		}
		if (run.walkUntil === null && !run.active.length && (!run.queue.length || run.stopping)) {
			close(world, run, now);
			changed.runs = true;
			changed.events = true;
		}
	}
	if (changed.runs) publish('runs');
	else if (changed.progress) publish('progress');
	if (changed.library) publish('library');
	if (changed.events) publish('events');
}

/** The walk is over: what it found, what it can take from the cache, and what
 * it has to open or rewrite. */
function finishWalk(world: World, run: SimRun): void {
	const files =
		run.kind === 'recheck'
			? run.titles.flatMap((title) => title.files)
			: world.titles.flatMap((title) => title.files);
	const opening = (file: File) => run.kind === 'recheck' || !world.current || !file.tracks.length;
	let probes = 0;
	for (const file of files) {
		if (opening(file)) {
			probes += 1;
			run.queue.push({ path: file.path, skipped: false });
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
	if (run.kind === 'sweep') world.current = true;
}

/** A probe has finished: the file is judged, and either rewritten, reported or
 * left for the reason it cannot be rewritten. */
function judged(world: World, run: SimRun, active: Active, now: number, changed: Changed): void {
	const file = world.byPath.get(active.path)!;
	const opened = !file.tracks.length;
	if (opened || run.kind === 'recheck' || !world.current) probe(file);
	rejudge(world, file, now);
	changed.library = true;
	const hold = heldTitle(world, file.title);
	if (file.status !== 'pending') {
		settle(
			world,
			run,
			active,
			file,
			file.status,
			(file.why.skip ?? file.why.reasons?.join('; ')) || 'nothing to do',
			now,
			changed
		);
		return;
	}
	if (file.hardlinked) {
		defer(world, run, active, file, HARDLINKED, now, changed);
		return;
	}
	if (run.dry_run || hold) {
		const detail = hold
			? `held: ${hold.reason || 'until lifted'}`
			: (file.why.reasons ?? []).join('; ');
		settle(world, run, active, file, 'pending', detail, now, changed);
		return;
	}
	active.stage = 'encoding';
	active.duration = file.seconds;
	active.done = 0;
	active.speed = speedOf(file);
	active.since = now;
	log(world, run.id, file.path, `plan: ${(file.why.reasons ?? []).join('; ')}`);
	log(world, run.id, file.path, ffmpegLine(file));
	changed.runs = true;
}

function remove(run: SimRun, active: Active): void {
	run.active = run.active.filter((each) => each !== active);
}

function verdictLine(
	world: World,
	run: SimRun,
	file: File,
	kind: string,
	extra: Record<string, unknown>
): void {
	record(world, {
		event: kind,
		run: run.id,
		source: run.source,
		config_id: configId(world),
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
	world: World,
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
	log(world, run.id, file.path, `${status}: ${detail}`);
	if (status === 'pending') {
		verdictLine(world, run, file, 'pending', {});
		changed.events = true;
	}
	remove(run, active);
	changed.runs = true;
}

/** A rewrite that did not happen: the file is as it was and the next sweep
 * tries again. */
function defer(
	world: World,
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
	log(world, run.id, file.path, `deferred: ${detail}`);
	verdictLine(world, run, file, 'deferred', { seconds, duration: file.seconds, detail });
	remove(run, active);
	changed.runs = true;
	changed.events = true;
}

/** The encode is done: the plan becomes the file. */
function complete(world: World, run: SimRun, active: Active, now: number, changed: Changed): void {
	const file = world.byPath.get(active.path)!;
	const seconds = Math.round(((now - active.since) / 1000) * 10) / 10;
	const reasons = file.why.reasons ?? [];
	const planned = file.planned;
	const before = { ...file.why };
	const made = rewrite(world, file, now);
	record(world, {
		event: 'modified',
		run: run.id,
		source: run.source,
		config_id: configId(world),
		reasons,
		rules: before.rules ?? [],
		incidental: before.incidental ?? [],
		incidental_rules: before.incidental_rules ?? [],
		downmixed: planned
			.filter((track) => track.flags?.includes('generated'))
			.map((track) =>
				track.channels === 2 ? '2.0' : track.channels === 6 ? '5.1' : `${track.channels}ch`
			),
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
	run.recent.unshift({ path: file.path, status: 'modified', seconds, detail: reasons.join('; ') });
	log(world, run.id, file.path, `encoded at ${active.speed}x in ${seconds}s`);
	log(world, run.id, file.path, 'duration and stream count verify; published over the original');
	remove(run, active);
	changed.runs = true;
	changed.library = true;
	changed.events = true;
}

/** The run is over: its summary, and the library's word that it happened. */
function close(world: World, run: SimRun, now: number): void {
	run.stopped = run.queue.length;
	run.queue = [];
	const seconds = Math.round(((now - Date.parse(run.started)) / 1000) * 10) / 10;
	if (run.kind === 'sweep') {
		world.swept += 1;
		record(world, {
			event: 'sweep',
			run: run.id,
			dry_run: run.dry_run,
			files: run.total,
			library_bytes: world.titles
				.flatMap((title) => title.files)
				.reduce((sum, file) => sum + file.bytes, 0),
			config: configOf(world.settings, VERSION),
			config_id: configId(world),
			cached: run.cached,
			counts: run.counts,
			seconds,
			...(run.stopped ? { stopped: run.stopped } : {})
		});
	} else if (run.kind === 'recheck') {
		record(world, {
			event: 'recheck',
			run: run.id,
			dry_run: run.dry_run,
			titles: run.titles.length,
			files: run.total,
			config_id: configId(world),
			counts: run.counts,
			seconds,
			...(run.stopped ? { stopped: run.stopped } : {})
		});
	}
	world.runs = world.runs.filter((each) => each !== run);
}

// What the pages read.

export function mayRewrite(world: World): boolean {
	return world.settings.REWRITE_MODE === 'all';
}

function wireRun(world: World, run: SimRun, now: number): Run {
	const remaining = run.active.reduce(
		(sum, file) => sum + (file.stage === 'encoding' ? (file.duration - file.done) / file.speed : 0),
		0
	);
	const queued = run.queue.reduce((sum, item) => {
		const file = world.byPath.get(item.path);
		return sum + (file?.status === 'pending' && !run.dry_run ? file.seconds / speedOf(file) : 0);
	}, 0);
	return {
		id: run.id,
		kind: run.kind,
		started: run.started,
		seconds: Math.round((now - Date.parse(run.started)) / 1000),
		dry_run: run.dry_run,
		label: run.label,
		total: run.walkUntil === null ? run.total : Math.round(run.total * 0.4),
		done: run.done,
		counts: run.counts,
		queued: run.queue.length,
		walking: run.walkUntil !== null,
		rewrite_seconds: run.dry_run ? null : Math.round(remaining + queued),
		stopping: run.stopping,
		active: run.active.map((file) => {
			const { since, readyAt: _readyAt, ...wire } = file as Active;
			void _readyAt;
			return { ...wire, seconds: Math.round((now - since) / 1000) };
		}),
		upcoming: run.queue.slice(0, UPCOMING).map((item) => {
			const file = world.byPath.get(item.path);
			const expected =
				file?.status === 'pending' && !run.dry_run ? Math.round(file.seconds / speedOf(file)) : 0;
			return { path: item.path, expected, skipped: item.skipped };
		}),
		recent: run.recent.slice(0, RECENT),
		seen: 0
	};
}

export function activity(
	world: World,
	now = Date.now()
): Omit<Activity, 'runs'> & { runs: Omit<Run, 'seen'>[] } {
	const runs = world.runs.map((run) => {
		const { seen: _seen, ...wire } = wireRun(world, run, now);
		void _seen;
		return wire;
	});
	const next = nextRuns(String(world.settings.SWEEP_AT ?? ''), new Date(now), 1)?.[0];
	const active = world.runs.flatMap((run) => run.active);
	return {
		paused: world.paused,
		paused_by: world.pausedBy,
		paused_at: world.pausedAt,
		up_since: world.upSince,
		runs,
		queue:
			world.runs.reduce((sum, run) => sum + run.queue.length, 0) +
			active.filter((file) => file.stage === 'waiting').length,
		working: active.filter((file) => file.stage !== 'waiting').length,
		rewrites: active.filter((file) => file.stage === 'encoding').length,
		parked: world.titles
			.flatMap((title) => title.files)
			.filter((file) => file.hardlinked && file.status === 'pending').length,
		may_rewrite: mayRewrite(world),
		next_sweep: next ? next.toISOString() : null,
		holds: holdsNow(world, now)
	};
}

// The buttons.

export type Refused = { status: number; body: Record<string, unknown> };

function walking(world: World): SimRun | undefined {
	return world.runs.find((run) => run.kind !== 'import');
}

export function startSweep(
	world: World,
	mode: string,
	now = Date.now()
): Refused | { status: string; run: string } {
	const going = walking(world);
	if (going) return { status: 409, body: { status: 'a sweep is already running', run: going.id } };
	const run = newRun(world, 'sweep', now, mode !== 'apply' || !mayRewrite(world));
	run.walkUntil = now + WALK_MS;
	run.total = world.titles.flatMap((title) => title.files).length;
	world.runs.push(run);
	publish('runs');
	return { status: 'started', run: run.id };
}

export function recheck(
	world: World,
	titles: Title[],
	mode: string,
	now = Date.now()
): Refused | { status: string; run: string; titles: number } {
	const going = walking(world);
	if (going) return { status: 409, body: { status: 'a sweep is already running', run: going.id } };
	const label = titles.length === 1 ? titles[0].spec.name : `${titles.length} titles`;
	const run = newRun(world, 'recheck', now, mode !== 'apply' || !mayRewrite(world), label);
	run.titles = titles;
	run.walkUntil = now + RECHECK_WALK_MS;
	run.total = titles.flatMap((title) => title.files).length;
	world.runs.push(run);
	publish('runs');
	return { status: 'started', run: run.id, titles: titles.length };
}

export function stopRun(world: World, id: string): Refused | { status: string } {
	const run = world.runs.find((each) => each.id === id);
	if (!run) return { status: 404, body: { status: 'no such run is going' } };
	run.stopping = true;
	publish('runs');
	return { status: 'stopping' };
}

export function setPaused(world: World, on: boolean, by: string, now = Date.now()): void {
	world.paused = on;
	world.pausedBy = on ? by : '';
	world.pausedAt = on ? stamp(now) : '';
	record(world, { event: on ? 'paused' : 'resumed', by });
	publish('runs');
	publish('events');
}

export function abort(
	world: World,
	now = Date.now()
): { status: string; stopped: number; rewrites: number } {
	const changed: Changed = { runs: true, progress: false, library: false, events: true };
	let rewrites = 0;
	const stopped = world.runs.length;
	for (const run of [...world.runs]) {
		for (const active of [...run.active]) {
			if (!isActive(active)) continue;
			if (active.stage === 'encoding') rewrites += 1;
			const file = world.byPath.get(active.path)!;
			defer(world, run, active, file, 'the run stopped', now, changed);
		}
		close(world, run, now);
	}
	publish('runs');
	publish('events');
	return { status: 'stopping', stopped, rewrites };
}

export function skip(
	world: World,
	id: string,
	path: string,
	by: string,
	now = Date.now()
): Refused | { status: string; where: string; rewrites: number } {
	const run = world.runs.find((each) => each.id === id);
	if (!run) return { status: 404, body: { status: 'that run is not going to reach that file' } };
	const active = run.active.find((file) => file.path === path);
	const queued = run.queue.find((item) => item.path === path);
	if (!active && !queued)
		return { status: 404, body: { status: 'that run is not going to reach that file' } };
	const file = world.byPath.get(path)!;
	record(world, { event: 'skipped', run: run.id, path, by, title: file.title.spec.id });
	let rewrites = 0;
	if (active && isActive(active)) {
		rewrites = active.stage === 'encoding' ? 1 : 0;
		const changed: Changed = { runs: true, progress: false, library: false, events: true };
		defer(world, run, active, file, `skipped by ${by}`, now, changed);
	} else if (queued) {
		queued.skipped = true;
	}
	publish('runs');
	publish('events');
	return { status: 'skipped', where: active ? 'active' : 'queued', rewrites };
}
