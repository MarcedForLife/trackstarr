// Three weeks of history for the demo's library, written around the moment the
// demo opened: nightly sweeps, the imports that were rewritten as they landed,
// the settings changes that made today's pending files pending, and the run
// that is going as the page loads. Every line is one the service records.

import type { Event } from '$lib/events';
import { downmixes, judge } from './judge';
import { configOf } from './settings';
import type { File, World } from './world';
import {
	DAY_MS,
	dayAt,
	digest,
	HOUR_MS,
	IMPORT_STARTED_AGO_MS,
	jitter,
	lastAt,
	MINUTE_MS,
	runId,
	stamp,
	SWEEP_STARTED_AGO_MS,
	VERSION
} from './util';

// How the story goes, in days before today.
const STARTED = 21;
const REWRITE_IMPORTS = 18;
const LANGUAGES_NARROWED = 4;

// Which languages the settings kept until they were narrowed.
const WIDE_LANGUAGES = ['original', 'eng', 'fre:keep', 'ger:keep', 'spa:keep'];

// Titles the story names, by id.
const SINTEL = 'arr:radarr:2';
const TEARS = 'arr:radarr:3';
const ELEPHANTS = 'arr:radarr:4';
const COFFEE = 'arr:radarr:9';
const METROPOLIS = 'arr:radarr:21';
const CALIGARI = 'arr:radarr:22';
const CHARADE = 'arr:radarr:25';
const SHERLOCK_JR = 'arr:radarr:27';
const SAFETY_LAST = 'arr:radarr:35';
const HOLMES = 'arr:sonarr:40';
const TALES = 'arr:sonarr:43';

// The files a pending verdict was first reached on, and by which change.
const PENDING_FROM_THE_START = [TEARS, METROPOLIS, ELEPHANTS];
const PENDING_SINCE_NARROWED = [SINTEL, CHARADE, CALIGARI];

type Story = {
	world: World;
	now: number;
	lines: Event[];
	config: Record<string, unknown>;
	configId: string;
	wideConfig: Record<string, unknown>;
	wideConfigId: string;
	narrowedAt: number;
	// The nightly sweep's clock, most recent first.
	sweeps: number[];
	by: string;
};

/** What a verdict line carries about the file: the plan and its rules. A
 * rewritten file is judged again off the tracks it had, for the plan it had. */
function plan(
	story: Story,
	file: File
): Pick<Event, 'reasons' | 'rules' | 'incidental' | 'incidental_rules' | 'downmixed'> {
	const had = file.planned.length
		? { why: file.why, planned: file.planned }
		: judge(
				{ tracks: file.modified?.was ?? [], ext: file.ext },
				file.title.spec.lang,
				story.world.settings
			);
	return {
		reasons: had.why.reasons ?? [],
		rules: had.why.rules ?? [],
		incidental: had.why.incidental ?? [],
		incidental_rules: had.why.incidental_rules ?? [],
		downmixed: downmixes(had.planned)
	};
}

function verdict(
	story: Story,
	kind: string,
	file: File,
	run: string,
	source: string,
	at: number,
	extra: Partial<Event> = {}
): void {
	story.lines.push({
		ts: stamp(at),
		event: kind,
		version: VERSION,
		run,
		source,
		config_id: at < story.narrowedAt ? story.wideConfigId : story.configId,
		path: file.path,
		...plan(story, file),
		title: file.title.spec.id,
		...extra
	});
}

function rewriteSeconds(file: File): number {
	return Math.round((file.seconds / (7 + jitter(file.path) * 4)) * 10) / 10;
}

/** A rewrite as the import that delivered the file made it. */
function imported(story: Story, file: File, run: string): void {
	const at = Date.parse(file.modified!.at);
	verdict(story, 'modified', file, run, 'webhook', at, {
		seconds: rewriteSeconds(file),
		waited: Math.round(jitter(file.name) * 30 * 10) / 10,
		duration: file.seconds,
		bytes_before: file.modified!.bytes_before,
		bytes_after: file.modified!.bytes_after
	});
}

function webhook(story: Story, arr: string, files: File[], run: string, at: number): void {
	story.lines.push({
		ts: stamp(at),
		event: 'webhook',
		version: VERSION,
		run,
		arr,
		files: files.length,
		paths: files.map((file) => file.path),
		title: files[0].title.spec.id
	});
}

function settingsChange(story: Story, at: number, changed: Event['changed']): void {
	story.lines.push({ ts: stamp(at), event: 'settings', version: VERSION, changed, by: story.by });
}

/** What a file's verdict was on a past night, where it differs from today's. */
function statusOn(story: Story, file: File, night: number): string | null {
	if (file.status === 'unchecked') return null;
	if (file.modified) return Date.parse(file.modified.at) < night ? 'conform' : 'pending';
	if (PENDING_SINCE_NARROWED.includes(file.title.spec.id) && night < story.narrowedAt)
		return 'conform';
	return file.status;
}

/** One night's sweep summary, and the pending verdicts it reached for the first
 * time. */
function nightly(story: Story, night: number, previous: number | null): void {
	const files = story.world.titles
		.flatMap((title) => title.files)
		.filter((file) => file.title.added * 1000 < night);
	const counts: Record<string, number> = {};
	let bytes = 0;
	let judged = 0;
	for (const file of files) {
		const status = statusOn(story, file, night);
		if (!status) continue;
		counts[status] = (counts[status] ?? 0) + 1;
		bytes += file.bytes;
		judged += 1;
	}
	const wide = night < story.narrowedAt;
	// Everything is re-probed the night after the rules change; otherwise only
	// what arrived since the last sweep.
	const fresh =
		previous === null || (story.narrowedAt > previous && story.narrowedAt < night)
			? judged
			: files.filter((file) => file.title.added * 1000 > previous).length;
	const run = runId(night);
	story.lines.push({
		ts: stamp(night + (120 + judged * 0.9 + fresh * 6) * 1000),
		event: 'sweep',
		version: VERSION,
		run,
		dry_run: true,
		files: judged,
		library_bytes: bytes,
		config: wide ? story.wideConfig : story.config,
		config_id: wide ? story.wideConfigId : story.configId,
		cached: judged - fresh,
		counts,
		seconds: Math.round((120 + judged * 0.9 + fresh * 6) * 10) / 10
	});
	const firstNight = previous === null;
	const afterNarrowing =
		previous !== null && story.narrowedAt > previous && story.narrowedAt < night;
	const newlyPending = firstNight
		? PENDING_FROM_THE_START
		: afterNarrowing
			? PENDING_SINCE_NARROWED
			: [];
	newlyPending.forEach((id, at) => {
		const title = story.world.byId.get(id)!;
		const file =
			title.files.find((each) => each.status === 'pending' || each.modified) ?? title.files[0];
		verdict(story, 'pending', file, run, 'sweep', night + (30 + at * 25) * 1000);
	});
	if (firstNight) {
		const holmes = story.world.byId.get(HOLMES)!.files.find((file) => file.status === 'pending')!;
		verdict(story, 'pending', holmes, run, 'sweep', night + 140 * 1000);
	}
}

export function chronicle(world: World, now: number): Event[] {
	const version = VERSION;
	const wideSettings = { ...world.settings, LANGUAGES: WIDE_LANGUAGES };
	const config = configOf(world.settings, version);
	const wideConfig = configOf(wideSettings, version);
	const story: Story = {
		world,
		now,
		lines: [],
		config,
		configId: digest(JSON.stringify(config)),
		wideConfig,
		wideConfigId: digest(JSON.stringify(wideConfig)),
		narrowedAt: dayAt(now, LANGUAGES_NARROWED, 18, 20),
		sweeps: Array.from({ length: STARTED }, (_, at) => lastAt(now, 3, 0) - at * DAY_MS),
		by: world.account?.name ?? 'demo'
	};
	const file = (id: string, at = 0) => world.byId.get(id)!.files[at];

	// The service came up, and swept for the first time that night.
	story.lines.push({
		ts: stamp(dayAt(now, STARTED, 9, 12)),
		event: 'config',
		version,
		config: wideConfig,
		config_id: story.wideConfigId
	});
	const nights = [...story.sweeps].reverse();
	nights.forEach((night, at) => nightly(story, night, at ? nights[at - 1] : null));

	settingsChange(story, dayAt(now, REWRITE_IMPORTS, 10, 40), {
		REWRITE_MODE: { from: 'report', to: 'imports' }
	});

	// Two imports the service rewrote as they landed.
	const tales = world.byId.get(TALES)!.files.filter((episode) => episode.modified);
	const talesAt = Date.parse(tales[0].modified!.at) - 6 * MINUTE_MS;
	const talesRun = runId(talesAt);
	webhook(story, 'sonarr', tales, talesRun, talesAt);
	for (const episode of tales) imported(story, episode, talesRun);
	const safety = file(SAFETY_LAST);
	const safetyRun = runId(Date.parse(safety.modified!.at) - 4 * MINUTE_MS);
	webhook(story, 'radarr', [safety], safetyRun, Date.parse(safety.modified!.at) - 4 * MINUTE_MS);
	imported(story, safety, safetyRun);

	// A hold for an evening, a tag put right, and the change that made three
	// films pending.
	const charade = world.byId.get(CHARADE)!;
	story.lines.push({
		ts: stamp(dayAt(now, 7, 19, 30)),
		event: 'held',
		version,
		path: charade.spec.folder,
		title: CHARADE,
		seconds: 8 * 3600,
		reason: 'watching it',
		by: story.by
	});
	story.lines.push({
		ts: stamp(dayAt(now, 6, 21, 0)),
		event: 'lifted',
		version,
		path: charade.spec.folder,
		title: CHARADE,
		by: story.by
	});
	story.lines.push({
		ts: stamp(dayAt(now, 6, 9, 5)),
		event: 'retagged',
		version,
		path: file(METROPOLIS).path,
		index: 2,
		kind: 'audio',
		changed: { lang: { from: 'und', to: 'eng' } },
		by: story.by,
		title: METROPOLIS
	});
	settingsChange(story, story.narrowedAt, {
		LANGUAGES: { from: WIDE_LANGUAGES, to: world.settings.LANGUAGES }
	});

	story.lines.push({ ts: stamp(dayAt(now, 2, 8, 0)), event: 'paused', version, by: story.by });
	story.lines.push({ ts: stamp(dayAt(now, 2, 8, 41)), event: 'resumed', version, by: story.by });

	// A re-check of one film, to see what the narrowing would do to it.
	const recheckAt = dayAt(now, 1, 17, 30);
	const recheckRun = runId(recheckAt);
	verdict(story, 'pending', file(SINTEL), recheckRun, 'recheck', recheckAt + 20 * 1000);
	story.lines.push({
		ts: stamp(recheckAt + 23 * 1000),
		event: 'recheck',
		version,
		run: recheckRun,
		dry_run: true,
		titles: 1,
		files: 1,
		config_id: story.configId,
		counts: { pending: 1 },
		seconds: 3.2
	});

	// Today: a hold, the mode that lets the sweep rewrite, and the sweep itself.
	const metropolis = world.byId.get(METROPOLIS)!;
	story.lines.push({
		ts: world.holds[0]?.at ?? stamp(now - 2 * HOUR_MS),
		event: 'held',
		version,
		path: metropolis.spec.folder,
		title: METROPOLIS,
		reason: world.holds[0]?.reason ?? '',
		by: story.by
	});
	settingsChange(story, now - 110 * MINUTE_MS, { REWRITE_MODE: { from: 'imports', to: 'all' } });

	const sweepStarted = now - SWEEP_STARTED_AGO_MS;
	const sweepRun = runId(sweepStarted);
	const elephants = file(ELEPHANTS);
	verdict(story, 'modified', elephants, sweepRun, 'sweep', Date.parse(elephants.modified!.at), {
		seconds: rewriteSeconds(elephants),
		waited: 0.8,
		duration: elephants.seconds,
		bytes_before: elephants.modified!.bytes_before,
		bytes_after: elephants.modified!.bytes_after
	});
	const sherlockJr = file(SHERLOCK_JR);
	verdict(story, 'deferred', sherlockJr, sweepRun, 'sweep', now - 9 * MINUTE_MS, {
		seconds: 0.4,
		duration: sherlockJr.seconds,
		detail: 'hard-linked 2 times. A download client still has it'
	});

	const importStarted = now - IMPORT_STARTED_AGO_MS;
	webhook(story, 'radarr', [file(COFFEE)], runId(importStarted), importStarted);

	return story.lines.sort((a, b) => a.ts.localeCompare(b.ts)).reverse();
}
