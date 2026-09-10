// The demo's service, held in memory for the life of the tab: the library, the
// settings, the holds, the history and who is signed in. Built the first time
// anything asks, around the moment it was asked, so the history always reads
// as recent. The runs and their simulation are in ./runs.

import type { Account } from '$lib/api';
import type { Event } from '$lib/events';
import type { Hold } from '$lib/holds';
import type {
	Card,
	LibraryFile,
	Modified,
	Shelf,
	Summary,
	TitleDetail,
	TitleLink,
	TitleServer,
	Track,
	Verdict
} from '$lib/library';
import { ORDER, type Sort } from '$lib/order.svelte';
import type { Outcome } from '$lib/retag';
import type { DoneFile, Run } from '$lib/runs';
import type { SettingsSnapshot, SettingValue } from '$lib/settings';
import { catalogue, type FileSpec, type TitleSpec } from './catalogue';
import { chronicle } from './history';
import { added, judge, type Settings } from './judge';
import { DEFAULTS, ENV_PINNED, problems, RULE_SETTINGS, SECRETS, snapshot } from './settings';
import { publish } from './stream';
import { DAY_MS, digest, HOUR_MS, lastAt, MINUTE_MS, slug, stamp, VERSION } from './util';

/** One file as the world knows it: the wire shape, plus what a probe would find
 * and which title holds it. */
export type File = LibraryFile & {
	ext: string;
	/** Every track the file carries. `tracks` is what has been probed, which is
	 * nothing for a file no sweep has opened. */
	source: Track[];
	/** The running time, which `seconds` reports once probed. */
	runtime: number;
	/** When its verdict was reached, in epoch seconds; 0 for none. */
	judged: number;
	/** Still held by a download client, so every rewrite is deferred. */
	hardlinked?: boolean;
	title: Title;
};

export type Title = {
	spec: TitleSpec;
	files: File[];
	/** Epoch seconds. */
	added: number;
};

/** A run as the pages read it, without the stamp getActivity adds on arrival,
 * plus what the simulation needs to carry it on. */
export type SimRun = Omit<Run, 'seen' | 'recent'> & {
	recent: DoneFile[];
	/** Files the run has found work for and not reached, in order. */
	queue: { path: string; skipped: boolean }[];
	/** When the walk finishes, or null once it has. */
	walkUntil: number | null;
	/** What a verdict line names as where it came from. */
	source: 'sweep' | 'recheck' | 'webhook';
	/** The titles a re-check was pointed at. */
	titles: Title[];
	/** Files never reached, once stopped. */
	stopped: number;
	/** How many verdicts the walk took from the cache. */
	cached: number;
};

/** One history line and its place in the file, which is what a page cursor
 * names. Newer lines have higher sequence numbers. */
export type Line = { seq: number; entry: Event };

export type World = {
	titles: Title[];
	byId: Map<string, Title>;
	byPath: Map<string, File>;
	settings: Settings;
	/** Which credentials are set, since a value is never echoed. */
	secretsSet: Set<string>;
	lines: Line[];
	nextSeq: number;
	holds: Hold[];
	runs: SimRun[];
	/** Whether ./runs has put the opening runs in place. */
	seeded: boolean;
	paused: boolean;
	pausedBy: string;
	pausedAt: string;
	/** Whether the verdicts were reached under the rules in force now. */
	current: boolean;
	swept: number;
	account: Account | null;
	/** When the title scores were last fetched, in epoch seconds. */
	ratingsFetched: number;
	upSince: string;
	/** What each file's worker logged, by `run|path`. */
	logs: Map<string, string[]>;
	/** When the world was built. */
	born: number;
};

// The route to a title's own page in its *arr, followed by the slug.
const ARR_ROUTES = { radarr: '/movie/', sonarr: '/series/' };

// How long before the demo opened its one rewrite of the day landed.
const REWRITTEN_IN_RUN_AGO_MS = 14 * MINUTE_MS;

/** What the tracks take on disk over the running time. */
export function bytesOf(tracks: Track[], seconds: number): number {
	const rate = tracks.reduce((sum, track) => sum + (track.bitrate ?? 0), 0);
	// Plus the container's own share.
	return Math.round((rate / 8) * seconds * 1.004);
}

function fileOf(title: Title, spec: FileSpec): File {
	const opened = spec.history?.kind !== 'unchecked';
	return {
		path: `${title.spec.folder}/${spec.name}${spec.ext}`,
		name: spec.name.slice(spec.name.lastIndexOf('/') + 1) + spec.ext,
		ext: spec.ext,
		status: 'unchecked',
		bytes: bytesOf(spec.tracks, spec.seconds),
		seconds: opened ? spec.seconds : 0,
		lang: title.spec.lang ?? null,
		tracks: opened ? spec.tracks : [],
		planned: [],
		why: {},
		source: spec.tracks,
		runtime: spec.seconds,
		judged: 0,
		hardlinked: spec.history?.kind === 'deferred' || undefined,
		title
	};
}

/** Judge a file against the settings and keep the answer on it. */
export function rejudge(world: World, file: File, at: number): void {
	const verdict = judge(file, file.title.spec.lang, world.settings);
	file.status = verdict.status;
	file.planned = verdict.planned;
	file.why = verdict.why;
	file.judged = file.status === 'unchecked' ? 0 : Math.floor(at / 1000);
}

/** Open the file, as a sweep does the first time it reaches one. */
export function probe(file: File): void {
	file.tracks = file.source;
	file.seconds = file.runtime;
}

/** What a rewrite leaves behind, applied to the file: the plan becomes the
 * tracks, and the record of the change stays on it. */
export function rewrite(world: World, file: File, at: number): Modified {
	const before = file.tracks;
	const dropped = before
		.map((track) => track.index)
		.filter((index) => !file.planned.some((track) => track.src === index));
	const after: Track[] = file.planned.map(({ src: _src, ...track }) => {
		void _src;
		return track;
	});
	const made: Modified = {
		at: stamp(at),
		bytes_before: file.bytes,
		bytes_after: bytesOf(after, file.runtime),
		was: before,
		dropped,
		added: after.filter((track) => track.flags?.includes('generated')).map((track) => track.index)
	};
	file.source = after;
	file.tracks = after;
	file.bytes = made.bytes_after!;
	file.modified = made;
	rejudge(world, file, at);
	return made;
}

/** The record a file rewritten before the demo opened carries. */
function rewrittenBefore(file: File, at: number): void {
	const made = file.tracks.filter((track) => track.flags?.includes('generated'));
	const was = file.tracks
		.filter((track) => !track.flags?.includes('generated'))
		.map((track, position) => ({ ...track, index: position }));
	file.modified = {
		at: stamp(at),
		bytes_before: bytesOf(was, file.runtime),
		bytes_after: file.bytes,
		was,
		added: made.map((track) => track.index)
	};
	file.judged = Math.floor(at / 1000);
}

function titleOf(world: World, spec: TitleSpec, now: number, sweptAt: number): Title {
	const title: Title = {
		spec,
		files: [],
		added: Math.floor((now - spec.addedDays * DAY_MS) / 1000)
	};
	title.files = spec.files.map((fileSpec) => fileOf(title, fileSpec));
	spec.files.forEach((fileSpec, at) => {
		const file = title.files[at];
		rejudge(world, file, sweptAt + at * 41_000);
		const history = fileSpec.history;
		if (history?.kind === 'rewritten') {
			const when = history.daysAgo
				? now - history.daysAgo * DAY_MS + at * 200_000
				: now - REWRITTEN_IN_RUN_AGO_MS;
			rewrittenBefore(file, when);
		}
		world.byPath.set(file.path, file);
	});
	return title;
}

function build(now: number): World {
	const world: World = {
		titles: [],
		byId: new Map(),
		byPath: new Map(),
		settings: {
			...DEFAULTS,
			TZ: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
		},
		secretsSet: new Set(['RADARR_API_KEY', 'SONARR_API_KEY', 'PLEX_TOKEN']),
		lines: [],
		nextSeq: 1,
		holds: [],
		runs: [],
		seeded: false,
		paused: false,
		pausedBy: '',
		pausedAt: '',
		current: true,
		swept: 21,
		account: { name: 'demo', role: 'admin', must_change: false },
		ratingsFetched: Math.floor(lastAt(now, 6, 0) / 1000),
		upSince: stamp(lastAt(now, 6, 40)),
		logs: new Map(),
		born: now
	};
	// Last night's sweep is when most verdicts were reached.
	const sweptAt = lastAt(now, 3, 0);
	for (const spec of catalogue()) {
		const title = titleOf(world, spec, now, sweptAt);
		world.titles.push(title);
		world.byId.set(spec.id, title);
	}
	const metropolis = world.byId.get('arr:radarr:21')!;
	world.holds.push({
		path: metropolis.spec.folder,
		seconds: null,
		until: null,
		by: 'demo',
		reason: 'rewatching it with the kids',
		at: stamp(now - 2 * HOUR_MS),
		title: metropolis.spec.id,
		name: metropolis.spec.name
	});
	// Oldest first off the chronicle, so the newest line gets the highest seq.
	for (const entry of [...chronicle(world, now)].reverse()) {
		world.lines.unshift({ seq: world.nextSeq++, entry });
	}
	return world;
}

let built: World | null = null;

export function world(): World {
	built ??= build(Date.now());
	return built;
}

/** Start over, for a test. */
export function reset(): void {
	built = null;
}

export function record(
	world: World,
	entry: Omit<Event, 'ts' | 'version'> & { ts?: string }
): Event {
	const line = { ts: stamp(Date.now()), version: VERSION, ...entry } as Event;
	world.lines.unshift({ seq: world.nextSeq++, entry: line });
	return line;
}

// The library as the pages read it.

// Worst first, as the service ranks a card: the word it leads with while
// anything is outstanding.
const STATES: Verdict[] = [
	'failed',
	'pending',
	'skip',
	'unsupported',
	'conform',
	'unchecked',
	'missing'
];

const ACTIONABLE: Verdict[] = ['failed', 'pending'];

export function card(world: World, title: Title): Card {
	const { spec } = title;
	const counts: Record<string, number> = {};
	const adds = new Set<string>();
	let drops = 0;
	let modified = 0;
	let judged = 0;
	let bytes = 0;
	for (const file of title.files) {
		counts[file.status] = (counts[file.status] ?? 0) + 1;
		bytes += file.bytes;
		judged = Math.max(judged, file.judged);
		if (file.modified) modified += 1;
		for (const name of added(file.planned)) adds.add(name);
		if (file.planned.length) {
			drops += file.tracks.filter(
				(track) => !file.planned.some((planned) => planned.src === track.index)
			).length;
		}
	}
	const held = STATES.filter((state) => counts[state]);
	const worst = held[0] ?? (title.files.length ? 'unchecked' : 'missing');
	const state = ACTIONABLE.includes(worst) ? worst : held.length > 1 ? 'mixed' : worst;
	const made: Card = { id: spec.id, name: spec.name, kind: spec.kind, state, added: title.added };
	if (spec.year) made.year = spec.year;
	if (spec.lang) made.lang = spec.lang;
	if (spec.rating && world.settings.IMDB_RATINGS) made.rating = spec.rating;
	if (title.files.length) {
		made.files = title.files.length;
		made.bytes = bytes;
		made.counts = counts;
	}
	if (adds.size) made.adds = [...adds].sort();
	if (drops) made.drops = drops;
	if (modified) made.modified = modified;
	if (adds.size || drops) {
		made.weight = Math.round((adds.size + drops / title.files.length) * 100) / 100;
	}
	if (judged) made.processed = judged;
	return made;
}

/** Every card, worst first: the order the shelf arrives in. */
export function cards(world: World): Card[] {
	const rank = (made: Card) => {
		const worst = STATES.find((state) => made.counts?.[state]) ?? made.state;
		return STATES.indexOf(worst);
	};
	return world.titles
		.map((title) => card(world, title))
		.sort(
			(a, b) =>
				rank(a) - rank(b) || (b.weight ?? 0) - (a.weight ?? 0) || a.name.localeCompare(b.name)
		);
}

export function shelf(world: World): Shelf {
	return { titles: cards(world), complete: true, current: world.current, swept: world.swept };
}

/** How many titles hold a file in each state, counted by membership as the
 * grid's chips count. */
function tally(made: Card[]): Record<string, number> {
	const counts: Record<string, number> = {};
	for (const each of made) {
		const states = each.counts
			? Object.keys(each.counts).filter((state) => each.counts![state])
			: [each.state];
		if (each.modified) states.push('modified');
		for (const state of states) counts[state] = (counts[state] ?? 0) + 1;
	}
	return counts;
}

// How much of the grid the overview's strip shows.
const STRIP = 12;

export function summary(world: World, sort: Sort): Summary {
	const all = cards(world);
	const order = ORDER[sort];
	// Ties go to the name, as the grid breaks them.
	const head = order ? [...all].sort((a, b) => order(a, b) || a.name.localeCompare(b.name)) : all;
	return {
		titles: all.length,
		counts: tally(all),
		head: head.slice(0, STRIP),
		complete: true,
		current: world.current,
		swept: world.swept
	};
}

/** The links built from what the title already carries, its *arr page and
 * its IMDb page, so no server need be asked. As in the service, they ride
 * along with the title and are in the links answer too. */
function known(world: World, title: Title): TitleLink[] {
	const { settings } = world;
	const { arr, imdb, name } = title.spec;
	const found: TitleLink[] = [];
	if (arr) {
		const base =
			arr === 'radarr'
				? settings.RADARR_PUBLIC_URL || settings.RADARR_URL
				: settings.SONARR_PUBLIC_URL || settings.SONARR_URL;
		if (base) {
			found.push({
				server: arr,
				label: arr === 'radarr' ? 'Radarr' : 'Sonarr',
				url: `${base}${ARR_ROUTES[arr]}${slug(name)}`
			});
		}
	}
	if (imdb) {
		found.push({ server: 'imdb', label: 'IMDb', url: `https://www.imdb.com/title/${imdb}/` });
	}
	return found;
}

/** Every service the sheet can offer for the title: the media servers the
 * settings name, still to be asked, and the known links filled in. */
function servers(world: World, title: Title): TitleServer[] {
	const listed: TitleServer[] = [];
	if (world.settings.PLEX_URL) listed.push({ server: 'plex', label: 'Plex' });
	if (world.settings.JELLYFIN_URL) listed.push({ server: 'jellyfin', label: 'Jellyfin' });
	return [...listed, ...known(world, title)];
}

function wireFile(file: File): LibraryFile {
	const { path, name, status, bytes, seconds, lang, tracks, planned, why, modified } = file;
	return {
		path,
		name,
		status,
		bytes,
		seconds,
		lang,
		tracks,
		planned,
		why,
		...(modified ? { modified } : {})
	};
}

export function detail(world: World, title: Title): TitleDetail {
	const { spec } = title;
	const made: TitleDetail = {
		id: spec.id,
		name: spec.name,
		kind: spec.kind,
		folder: spec.folder,
		current: world.current,
		files: title.files.map(wireFile),
		total: title.files.length,
		servers: servers(world, title)
	};
	if (spec.year) made.year = spec.year;
	if (spec.lang) made.lang = spec.lang;
	return made;
}

/** Where a title opens in the media servers the settings name, then the
 * known links. The media servers do not exist, so the demo's settings put them
 * on localhost, where a click goes nowhere rather than to somebody's host. */
export function links(world: World, title: Title): TitleLink[] {
	const { settings } = world;
	const found: TitleLink[] = [];
	const item = 10_000 + parseInt(digest(title.spec.id, 4), 16);
	const plex = settings.PLEX_PUBLIC_URL || settings.PLEX_URL;
	if (plex) {
		found.push({
			server: 'plex',
			label: 'Plex',
			url: `${plex}/web/index.html#!/server/demo/details?key=%2Flibrary%2Fmetadata%2F${item}`
		});
	}
	const jellyfin = settings.JELLYFIN_PUBLIC_URL || settings.JELLYFIN_URL;
	if (jellyfin) {
		found.push({
			server: 'jellyfin',
			label: 'Jellyfin',
			url: `${jellyfin}/web/index.html#!/details?id=${digest(title.spec.id, 32)}`
		});
	}
	return [...found, ...known(world, title)];
}

// Holds.

/** The holds still standing, each with its clock read against now. */
export function holdsNow(world: World, now: number): Hold[] {
	world.holds = world.holds.filter((hold) => !hold.until || Date.parse(hold.until) > now);
	return world.holds.map((hold) => ({
		...hold,
		seconds: hold.until ? Math.max(0, Math.round((Date.parse(hold.until) - now) / 1000)) : null
	}));
}

export function heldTitle(world: World, title: Title): Hold | undefined {
	return holdsNow(world, Date.now()).find((hold) => hold.title === title.spec.id);
}

export function placeHolds(
	world: World,
	titles: Title[],
	seconds: number,
	reason: string,
	by: string,
	now = Date.now()
): void {
	for (const title of titles) {
		world.holds = world.holds.filter((hold) => hold.title !== title.spec.id);
		world.holds.push({
			path: title.spec.folder,
			seconds: seconds || null,
			until: seconds ? stamp(now + seconds * 1000) : null,
			by,
			reason,
			at: stamp(now),
			title: title.spec.id,
			name: title.spec.name
		});
		record(world, {
			event: 'held',
			path: title.spec.folder,
			title: title.spec.id,
			...(seconds ? { seconds } : {}),
			reason,
			by
		});
	}
	publish('runs');
	publish('events');
}

export function liftHolds(world: World, titles: Title[], by: string): void {
	for (const title of titles) {
		if (!world.holds.some((hold) => hold.title === title.spec.id)) continue;
		world.holds = world.holds.filter((hold) => hold.title !== title.spec.id);
		record(world, { event: 'lifted', path: title.spec.folder, title: title.spec.id, by });
	}
	publish('runs');
	publish('events');
}

// Tags edited in place.

export type Edit = { lang?: string; flags?: Record<string, boolean> };

// What no edit can reach.
const MATROSKA = '.mkv';
const UNTAGGED = 'und';

/** Set a track's language and flags in each file named, as mkvpropedit would,
 * and judge the file again. */
export function retag(
	world: World,
	targets: { path: string; index: number }[],
	edit: Edit,
	by: string,
	now = Date.now()
): Outcome[] {
	const outcomes: Outcome[] = [];
	for (const target of targets) {
		const file = world.byPath.get(String(target.path));
		if (!file) {
			outcomes.push({ path: target.path, status: 'refused', detail: 'no such file' });
			continue;
		}
		if (file.ext !== MATROSKA) {
			outcomes.push({
				path: file.path,
				status: 'refused',
				detail: 'only Matroska tracks can be edited in place'
			});
			continue;
		}
		const track = file.source.find((each) => each.index === Number(target.index));
		if (!track) {
			outcomes.push({ path: file.path, status: 'refused', detail: `no stream ${target.index}` });
			continue;
		}
		const changed: Record<string, { from: unknown; to: unknown }> = {};
		if (edit.lang !== undefined && edit.lang !== (track.lang ?? UNTAGGED)) {
			changed.lang = { from: track.lang ?? UNTAGGED, to: edit.lang };
			track.lang = edit.lang === UNTAGGED ? undefined : edit.lang;
		}
		for (const [flag, on] of Object.entries(edit.flags ?? {})) {
			const had = track.flags?.includes(flag) ?? false;
			if (had === on) continue;
			changed[flag] = { from: had, to: on };
			track.flags = on
				? [...(track.flags ?? []), flag]
				: (track.flags ?? []).filter((each) => each !== flag);
		}
		if (!Object.keys(changed).length) {
			outcomes.push({ path: file.path, status: 'unchanged', verdict: file.status });
			continue;
		}
		if (file.tracks.length) probe(file);
		rejudge(world, file, now);
		record(world, {
			event: 'retagged',
			path: file.path,
			index: track.index,
			kind: track.kind,
			changed,
			by,
			title: file.title.spec.id
		});
		outcomes.push({ path: file.path, status: 'retagged', verdict: file.status });
	}
	publish('library');
	publish('events');
	return outcomes;
}

// Settings.

export function settingsSnapshot(world: World): SettingsSnapshot {
	return snapshot(world.settings, world.secretsSet);
}

/** Apply a save: the snapshot back, or the validator's problems. */
export function saveSettings(
	world: World,
	changes: Record<string, SettingValue | null>,
	now = Date.now()
): SettingsSnapshot | { problems: string[] } {
	const next: Settings = { ...world.settings };
	const moved: Record<string, { from: unknown; to: unknown }> = {};
	const secretsSet = new Set(world.secretsSet);
	for (const [name, value] of Object.entries(changes)) {
		if (ENV_PINNED.has(name)) continue;
		if (SECRETS.includes(name)) {
			// Empty leaves a credential alone; null clears it; anything else sets it.
			if (value === '') continue;
			const was = secretsSet.has(name);
			if (value === null) secretsSet.delete(name);
			else secretsSet.add(name);
			if (was !== secretsSet.has(name))
				moved[name] = { from: was ? 'set' : '', to: was ? '' : 'set' };
			continue;
		}
		const to = value ?? DEFAULTS[name];
		if (to === undefined || JSON.stringify(to) === JSON.stringify(next[name])) continue;
		moved[name] = { from: next[name], to };
		next[name] = to;
	}
	const found = problems(next);
	if (found.length) return { problems: found };
	world.settings = next;
	world.secretsSet = secretsSet;
	if (Object.keys(moved).length) {
		record(world, {
			ts: stamp(now),
			event: 'settings',
			changed: moved,
			by: world.account?.name ?? 'demo'
		});
		// A rule change drops every stored verdict; the next sweep judges afresh.
		if (Object.keys(moved).some((name) => RULE_SETTINGS.has(name))) world.current = false;
		publish('library');
		publish('runs');
		publish('events');
	}
	return settingsSnapshot(world);
}

/** Drop every stored verdict; how many went. */
export function clearVerdicts(world: World): number {
	let dropped = 0;
	for (const file of world.byPath.values()) {
		if (file.status === 'unchecked' && !file.tracks.length) continue;
		dropped += 1;
		file.tracks = [];
		file.seconds = 0;
		file.planned = [];
		file.why = {};
		file.status = 'unchecked';
		file.judged = 0;
	}
	world.current = true;
	publish('library');
	return dropped;
}
