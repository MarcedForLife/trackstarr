import type { SettingsChanges } from '$lib/settings';
import { arrChanges } from './settings';
// Everything the demo's service knows, held in memory for the life of the tab:
// the library, the settings, the pauses, the history and who is signed in.
// Built the first time anything asks, around the moment it was asked, so the
// history always reads as recent. The runs and their simulation are in ./runs.

import { instanceName } from '$lib/connections';
import { fileGroups } from '$lib/variants';
import { newestFirst } from '$lib/seasons';
import type { Account } from '$lib/api';
import type { Event } from '$lib/events';
import type { Pause } from '$lib/pauses';
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
import { DEFAULTS, ENV_PINNED, problems, RULE_SETTINGS, isSecret, snapshot } from './settings';
import { publish } from './stream';
import { DAY_MS, digest, lastAt, MINUTE_MS, slug, stamp, VERSION } from './util';

/** One file as the state knows it: the wire shape, plus what a probe would find
 * and which title holds it. */
export type File = LibraryFile & {
	ext: string;
	/** Every track the file carries. `tracks` is what has been probed, which is
	 * nothing for a file no sweep has opened. */
	contents: Track[];
	/** The running time, which `seconds` reports once probed. */
	runtime: number;
	/** The instance whose folder holds it; none under a folder no *arr claims. */
	instance_id?: string;
	/** When its verdict was reached, in epoch seconds; 0 for none. */
	judged: number;
	/** Still held by a download client, so every rewrite is deferred. */
	hardlinked?: boolean;
	/** Why the last rewrite of this file broke. Held here because a verdict is
	 * derived from the tracks and an outcome is not. */
	failure?: string;
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
	/** Stable identity for import runs, resolved only when a response is built. */
	instance_id: string;
	/** Files the run has found work for and not reached, in order. */
	queue: { path: string; skipped: boolean; discovery?: boolean }[];
	/** When the walk finishes, or null once it has. */
	walkUntil: number | null;
	/** What a verdict line names as where it came from. */
	source: 'sweep' | 'recheck' | 'webhook';
	/** The titles a re-check was pointed at. */
	titles: Title[];
	/** Exact targets for a file re-check; omitted for title walks. */
	files?: File[];
	/** Files never reached, once stopped. */
	stopped: number;
	/** How many verdicts the walk took from the cache. */
	cached: number;
};

/** One history line and its place in the file, which is what a page cursor
 * names. Newer lines have higher sequence numbers. */
export type Line = { seq: number; entry: Event };

export type State = {
	titles: Title[];
	connectionTrouble: boolean;
	byId: Map<string, Title>;
	byPath: Map<string, File>;
	settings: Settings;
	/** Which credentials are set, since a value is never echoed. */
	secretsSet: Set<string>;
	lines: Line[];
	nextSeq: number;
	pauses: Pause[];
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
	/** When the state was built. */
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

function fileOf(title: Title, spec: FileSpec, held = title.spec.sources[0]): File {
	const opened = spec.history?.kind !== 'unchecked';
	return {
		path: `${held.folder}/${spec.name}${spec.ext}`,
		instance_id: held.instance_id,
		name: spec.name.slice(spec.name.lastIndexOf('/') + 1) + spec.ext,
		ext: spec.ext,
		status: 'unchecked',
		bytes: bytesOf(spec.tracks, spec.seconds),
		seconds: opened ? spec.seconds : 0,
		lang: title.spec.lang ?? null,
		tracks: opened ? spec.tracks : [],
		planned: [],
		why: {},
		contents: spec.tracks,
		runtime: spec.seconds,
		judged: 0,
		hardlinked: spec.history?.kind === 'deferred' || undefined,
		title
	};
}

/** Judge a file against the settings and keep the answer on it. */
export function rejudge(state: State, file: File, at: number): void {
	const verdict = judge(file, file.title.spec.lang, state.settings);
	file.planned = verdict.planned;
	file.why = verdict.why;
	// A break only stands while there is still the work it broke on: once the
	// rules leave nothing to do, the file is whatever it is now.
	if (file.failure && verdict.status !== 'pending') delete file.failure;
	file.status = file.failure ? 'failed' : verdict.status;
	file.judged = file.status === 'unchecked' ? 0 : Math.floor(at / 1000);
}

/** Open the file, as a sweep does the first time it reaches one. */
export function probe(file: File): void {
	file.tracks = file.contents;
	file.seconds = file.runtime;
}

/** What a rewrite leaves behind, applied to the file: the plan becomes the
 * tracks, and the record of the change stays on it. */
export function rewrite(state: State, file: File, at: number): Modified {
	const before = file.tracks;
	const dropped = before
		.map((track) => track.index)
		.filter((index) => !file.planned.some((track) => track.src === index));
	const after: Track[] = file.planned.map(({ src: _src, dv_removed: _dvRemoved, ...track }) => {
		void _src;
		void _dvRemoved;
		return track;
	});
	const made: Modified = {
		at: stamp(at),
		...(file.planned.some((track) => track.dv_removed) ? { dv_removed: true } : {}),
		bytes_before: file.bytes,
		bytes_after: bytesOf(after, file.runtime),
		was: before,
		dropped,
		added: after.filter((track) => track.flags?.includes('generated')).map((track) => track.index)
	};
	file.contents = after;
	file.tracks = after;
	file.bytes = made.bytes_after!;
	file.modified = made;
	delete file.failure;
	rejudge(state, file, at);
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

function titleOf(state: State, spec: TitleSpec, now: number, sweptAt: number): Title {
	const title: Title = {
		spec,
		files: [],
		added: Math.floor((now - spec.addedDays * DAY_MS) / 1000)
	};
	title.files = spec.files.map((fileSpec) => fileOf(title, fileSpec));
	spec.files.forEach((fileSpec, at) => {
		const file = title.files[at];
		rejudge(state, file, sweptAt + at * 41_000);
		const history = fileSpec.history;
		if (history?.kind === 'rewritten') {
			const when = history.daysAgo
				? now - history.daysAgo * DAY_MS + at * 200_000
				: now - REWRITTEN_IN_RUN_AGO_MS;
			rewrittenBefore(file, when);
		}
		state.byPath.set(file.path, file);
	});
	return title;
}

function build(now: number): State {
	const state: State = {
		titles: [],
		connectionTrouble: false,
		byId: new Map(),
		byPath: new Map(),
		settings: {
			...DEFAULTS,
			TZ: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
		},
		secretsSet: new Set(['RADARR_API_KEY', 'SONARR_API_KEY', 'PLEX_TOKEN']),
		lines: [],
		nextSeq: 1,
		pauses: [],
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
		const title = titleOf(state, spec, now, sweptAt);
		state.titles.push(title);
		state.byId.set(spec.id, title);
	}
	// Nothing held to open on, since a paused title is a rare thing to meet
	// first. The history still carries one, long since lapsed.
	// Oldest first off the chronicle, so the newest line gets the highest seq.
	for (const entry of [...chronicle(state, now)].reverse()) {
		state.lines.unshift({ seq: state.nextSeq++, entry });
	}
	return state;
}

let built: State | null = null;

export function currentState(): State {
	built ??= build(Date.now());
	return built;
}

/** Drop the state, so the next call builds a new one. */
export function reset(): void {
	built = null;
}

export function record(
	state: State,
	entry: Omit<Event, 'ts' | 'version'> & { ts?: string }
): Event {
	const line = { ts: stamp(Date.now()), version: VERSION, ...entry } as Event;
	state.lines.unshift({ seq: state.nextSeq++, entry: line });
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

/** How the pages name an *arr: its name setting, else its kind and the ID
 * its settings keys carry. Read as an answer is built, as the service does. */
export function sourceName(state: State, id: string): string {
	return instanceName(
		id,
		String(state.settings[`${id.toUpperCase().replace('-', '_')}_NAME`] ?? '')
	);
}

export function card(state: State, title: Title): Card {
	const { spec } = title;
	const counts: Record<string, number> = {};
	const adds = new Set<string>();
	let drops = 0;
	let modified = 0;
	let untagged = 0;
	let judged = 0;
	let bytes = 0;
	for (const file of title.files) {
		counts[file.status] = (counts[file.status] ?? 0) + 1;
		bytes += file.bytes;
		judged = Math.max(judged, file.judged);
		if (file.modified) modified += 1;
		if (file.tracks.some((track) => track.kind === 'audio' && !track.lang)) untagged += 1;
		for (const name of added(file.planned)) adds.add(name);
		if (file.planned.length) {
			drops += file.tracks.filter(
				(track) => !file.planned.some((planned) => planned.src === track.index)
			).length;
		}
	}
	const held = STATES.filter((verdict) => counts[verdict]);
	const worst = held[0] ?? (title.files.length ? 'unchecked' : 'missing');
	const verdict = ACTIONABLE.includes(worst) ? worst : held.length > 1 ? 'mixed' : worst;
	const made: Card = {
		id: spec.id,
		name: spec.name,
		kind: spec.kind,
		state: verdict,
		added: title.added
	};
	const primary = spec.sources[0].instance_id;
	if (primary?.includes('-')) {
		made.source = sourceName(state, primary);
	}
	if (spec.sources.length > 1) made.source_count = spec.sources.length;
	if (spec.year) made.year = spec.year;
	if (spec.lang) made.lang = spec.lang;
	if (spec.rating && state.settings.IMDB_RATINGS) made.rating = spec.rating;
	if (title.files.length) {
		made.files = title.files.length;
		made.bytes = bytes;
		made.counts = counts;
	}
	if (adds.size) made.adds = [...adds].sort();
	if (drops) made.drops = drops;
	if (modified) made.modified = modified;
	if (untagged) made.untagged = untagged;
	if (adds.size || drops) {
		made.weight = Math.round((adds.size + drops / title.files.length) * 100) / 100;
	}
	if (judged) made.processed = judged;
	return made;
}

/** Every card, worst first: the order the shelf arrives in. */
export function cards(state: State): Card[] {
	const rank = (made: Card) => {
		const worst = STATES.find((state) => made.counts?.[state]) ?? made.state;
		return STATES.indexOf(worst);
	};
	return state.titles
		.map((title) => card(state, title))
		.sort(
			(a, b) =>
				rank(a) - rank(b) || (b.weight ?? 0) - (a.weight ?? 0) || a.name.localeCompare(b.name)
		);
}

export function shelf(state: State): Shelf {
	return { titles: cards(state), complete: true, current: state.current, swept: state.swept };
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
		if (each.untagged) states.push('untagged');
		for (const state of states) counts[state] = (counts[state] ?? 0) + 1;
	}
	return counts;
}

// How much of the grid the overview's strip shows.
const STRIP = 12;

export function summary(state: State, sort: Sort): Summary {
	const all = cards(state);
	const order = ORDER[sort];
	// Ties go to the name, as the grid breaks them.
	const head = order ? [...all].sort((a, b) => order(a, b) || a.name.localeCompare(b.name)) : all;
	return {
		titles: all.length,
		counts: tally(all),
		head: head.slice(0, STRIP),
		complete: true,
		current: state.current,
		swept: state.swept
	};
}

/** The links built from what the title already carries, its *arr page and
 * its IMDb page, so no server need be asked. As in the service, they ride
 * along with the title and are in the links answer too. */
function known(state: State, title: Title): TitleLink[] {
	const { settings } = state;
	const { imdb, name } = title.spec;
	const found: TitleLink[] = [];
	// One button per instance holding it, each carrying its own name.
	for (const { instance_id } of title.spec.sources) {
		if (!instance_id) continue;
		const arr = instance_id.split('-')[0] as keyof typeof ARR_ROUTES;
		const prefix = instance_id.toUpperCase().replace('-', '_');
		const base = settings[`${prefix}_PUBLIC_URL`] || settings[`${prefix}_URL`];
		if (base) {
			found.push({
				server: arr,
				label: sourceName(state, instance_id),
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
function servers(state: State, title: Title): TitleServer[] {
	const listed: TitleServer[] = [];
	if (state.settings.PLEX_URL) listed.push({ server: 'plex', label: 'Plex' });
	if (state.settings.JELLYFIN_URL) listed.push({ server: 'jellyfin', label: 'Jellyfin' });
	return [...listed, ...known(state, title)];
}

export function wireFile(state: State, file: File): LibraryFile {
	const { path, name, status, bytes, seconds, lang, tracks, planned, why, modified } = file;
	return {
		path,
		name,
		...(file.instance_id ? { source: sourceName(state, file.instance_id) } : {}),
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

export function detail(state: State, title: Title, pages = 1): TitleDetail {
	const { spec } = title;
	const summary = card(state, title);
	const made: TitleDetail = {
		id: spec.id,
		name: spec.name,
		kind: spec.kind,
		state: summary.state,
		counts: summary.counts,
		folder: spec.sources[0].folder,
		current: state.current,
		files: fileGroups(title.files.map((file) => wireFile(state, file)).sort(newestFirst), spec.kind)
			.slice(0, 200 * pages)
			.flatMap((group) => group.files),
		total: title.files.length,
		folders: spec.sources.map(({ instance_id, folder }) => ({
			source: instance_id ? sourceName(state, instance_id) : '',
			folder
		})),
		servers: servers(state, title)
	};
	if (spec.year) made.year = spec.year;
	if (spec.lang) made.lang = spec.lang;
	return made;
}

/** Where a title opens in the media servers the settings name, then the
 * known links. The media servers do not exist, so the demo's settings put them
 * on localhost, where a click goes nowhere rather than to somebody's host. */
export function links(state: State, title: Title): TitleLink[] {
	const { settings } = state;
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
	return [...found, ...known(state, title)];
}

// Item pauses.

/** The pauses still standing, each with its clock read against now. */
export function pausesNow(state: State, now: number): Pause[] {
	state.pauses = state.pauses.filter((pause) => !pause.until || Date.parse(pause.until) > now);
	return state.pauses.map((pause) => ({
		...pause,
		seconds: pause.until ? Math.max(0, Math.round((Date.parse(pause.until) - now) / 1000)) : null
	}));
}

export function pausedTitle(state: State, title: Title, path?: string): Pause | undefined {
	return pausesNow(state, Date.now()).find(
		(pause) => pause.title_id === title.spec.id || pause.path === path
	);
}

export function placePauses(
	state: State,
	titles: (Title | string)[],
	seconds: number,
	reason: string,
	by: string,
	now = Date.now()
): void {
	for (const { folder, id, name } of pauseFolders(titles)) {
		state.pauses = state.pauses.filter((pause) => pause.path !== folder);
		state.pauses.push({
			path: folder,
			seconds: seconds || null,
			until: seconds ? stamp(now + seconds * 1000) : null,
			by,
			reason,
			at: stamp(now),
			title_id: id,
			title_name: name
		});
		record(state, {
			event: 'item_paused',
			path: folder,
			title: id,
			...(seconds ? { seconds } : {}),
			reason,
			by
		});
	}
	publish('runs');
	publish('events');
}

/** A file pauses on its own path; a title pauses on every folder holding it. */
function pauseFolders(targets: (Title | string)[]) {
	return targets.flatMap((target) =>
		typeof target === 'string'
			? [{ folder: target, id: '', name: '' }]
			: target.spec.sources.map(({ folder }) => ({
					folder,
					id: target.spec.id,
					name: target.spec.name
				}))
	);
}

export function resumePauses(state: State, titles: (Title | string)[], by: string): void {
	for (const { folder, id } of pauseFolders(titles)) {
		if (!state.pauses.some((pause) => pause.path === folder)) continue;
		state.pauses = state.pauses.filter((pause) => pause.path !== folder);
		record(state, { event: 'item_resumed', path: folder, title: id, by });
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
	state: State,
	targets: { path: string; index: number }[],
	edit: Edit,
	by: string,
	now = Date.now()
): Outcome[] {
	const outcomes: Outcome[] = [];
	for (const target of targets) {
		const file = state.byPath.get(String(target.path));
		if (!file) {
			outcomes.push({
				path: target.path,
				status: 'refused',
				detail: 'could not read the file: No such file or directory'
			});
			continue;
		}
		// The same order mkvtag.unwritable refuses in, and its words.
		if (file.ext !== MATROSKA) {
			outcomes.push({
				path: file.path,
				status: 'refused',
				detail: 'only Matroska (.mkv) files are edited in place; the remux rule converts others'
			});
			continue;
		}
		if (file.hardlinked) {
			outcomes.push({
				path: file.path,
				status: 'refused',
				detail:
					"hardlinked: an edit in place would change the download client's copy too; " +
					'a rewrite makes a new file instead'
			});
			continue;
		}
		const track = file.contents.find((each) => each.index === Number(target.index));
		if (!track) {
			outcomes.push({
				path: file.path,
				status: 'refused',
				detail: `stream ${target.index} is not an audio or subtitle track`
			});
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
			outcomes.push({
				path: file.path,
				status: 'unchanged',
				detail: 'already tagged that way',
				verdict: file.status
			});
			continue;
		}
		if (file.tracks.length) probe(file);
		rejudge(state, file, now);
		record(state, {
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

export function settingsSnapshot(state: State): SettingsSnapshot {
	return snapshot(state.settings, state.secretsSet);
}

/** Apply a save: the snapshot back, or the validator's problems. */
export function saveSettings(
	state: State,
	changes: SettingsChanges,
	now = Date.now()
): SettingsSnapshot | { problems: string[] } {
	let flat: Record<string, SettingValue | null>;
	try {
		flat = arrChanges(changes, settingsSnapshot(state).arr_instances);
	} catch (error) {
		return { problems: [(error as Error).message] };
	}
	const next: Settings = { ...state.settings };
	const moved: Record<string, { from: unknown; to: unknown }> = {};
	const secretsSet = new Set(state.secretsSet);
	for (const [name, value] of Object.entries(flat)) {
		if (ENV_PINNED.has(name)) continue;
		if (isSecret(name)) {
			// Empty leaves a credential alone; null clears it; anything else sets it.
			if (value === '') continue;
			const was = secretsSet.has(name);
			if (value === null) {
				secretsSet.delete(name);
				delete next[name];
			} else {
				secretsSet.add(name);
				next[name] = '';
			}
			if (was !== secretsSet.has(name))
				moved[name] = { from: was ? 'set' : '', to: was ? '' : 'set' };
			continue;
		}
		if (value === null && !(name in DEFAULTS)) {
			moved[name] = { from: next[name], to: null };
			delete next[name];
			continue;
		}
		const to = value ?? DEFAULTS[name];
		if (to === undefined || JSON.stringify(to) === JSON.stringify(next[name])) continue;
		moved[name] = { from: next[name], to };
		next[name] = to;
	}
	const found = problems(next);
	if (found.length) return { problems: found };
	state.settings = next;
	state.secretsSet = secretsSet;
	if (Object.keys(moved).length) {
		record(state, {
			ts: stamp(now),
			event: 'settings',
			changed: moved,
			by: state.account?.name ?? 'demo'
		});
		// A rule change drops every stored verdict; the next sweep judges afresh.
		if (Object.keys(moved).some((name) => RULE_SETTINGS.has(name))) state.current = false;
		publish('library');
		publish('runs');
		publish('events');
	}
	return settingsSnapshot(state);
}

/** Drop every stored verdict; how many went. */
export function clearVerdicts(state: State): number {
	let dropped = 0;
	for (const file of state.byPath.values()) {
		if (file.status === 'unchecked' && !file.tracks.length) continue;
		dropped += 1;
		file.tracks = [];
		file.seconds = 0;
		file.planned = [];
		file.why = {};
		file.status = 'unchecked';
		file.judged = 0;
	}
	state.current = true;
	publish('library');
	return dropped;
}

/** Optional board: colliding arr IDs, independent libraries and duplicate
 * files within a library. Nothing in the opening catalogue changes. */
export function addVariants(state: State, now: number): string[] {
	const names: string[] = [];
	for (const kind of ['movie', 'series']) {
		const original =
			state.titles.find((title) => title.spec.kind === kind && title.files.length > 1) ??
			state.titles.find((title) => title.spec.kind === kind && title.files.length)!;
		const { spec } = original;
		const instance = `${spec.sources[0].instance_id}-4k`;
		// The same title held by a 4K instance too: one poster, a second folder.
		const held = {
			instance_id: instance,
			folder: spec.sources[0].folder.replace('/media/', '/media/4k/')
		};
		spec.sources.push(held);
		const upgrades = spec.files.slice(0, kind === 'series' ? 4 : 1).map((file) => ({
			...file,
			name: `${file.name.replace(/(?:Bluray-)?1080p/gi, '').trim()} WEBDL-2160p HEVC`,
			ext: '.mkv',
			history: undefined,
			tracks: [
				{ index: 0, kind: 'video', codec: 'hevc', bitrate: 24_000_000 },
				{
					index: 1,
					kind: 'audio',
					codec: 'eac3',
					channels: 6,
					lang: spec.lang,
					bitrate: 768_000,
					flags: ['default']
				},
				{ index: 2, kind: 'subtitle', codec: 'subrip', lang: spec.lang, bitrate: 40 }
			]
		}));
		// A second release of the film / first episode in the same folder.
		const alternate = structuredClone(spec.files[0]);
		alternate.name = `${alternate.name.replace(/(?:Bluray-)?1080p/gi, '').trim()} WEBDL-1080p H264`;
		alternate.history = undefined;
		alternate.ext = '.mkv';
		alternate.tracks = [
			{ index: 0, kind: 'video', codec: 'h264', bitrate: 5_000_000 },
			{
				index: 1,
				kind: 'audio',
				codec: 'aac',
				channels: 2,
				lang: spec.lang,
				bitrate: 256_000,
				flags: ['default']
			},
			{ index: 2, kind: 'subtitle', codec: 'subrip', lang: spec.lang, bitrate: 40 }
		];
		const arrivals = [
			fileOf(original, alternate),
			...upgrades.map((upgrade) => fileOf(original, upgrade, held))
		];
		for (const file of arrivals) {
			rejudge(state, file, now);
			original.files.push(file);
			state.byPath.set(file.path, file);
		}
		const prefix = instance.toUpperCase().replace('-', '_');
		state.settings[`${prefix}_URL`] = `http://localhost:${kind === 'movie' ? 7879 : 8990}`;
		state.settings[`${prefix}_PUBLIC_URL`] = '';
		state.settings[`${prefix}_API_KEY`] = '';
		state.settings[`${prefix}_NAME`] = '';
		state.secretsSet.add(`${prefix}_API_KEY`);
		names.push(spec.name);
	}
	state.settings.MEDIA_DIRS = [
		...(state.settings.MEDIA_DIRS as string[]),
		'/data/media/4k/movies',
		'/data/media/4k/tv'
	];
	publish('library');
	return names;
}

/** A deliberately long, invented series for paging and season navigation. */
export function addLargeSeries(state: State, now: number): string[] {
	const title = state.titles.find((title) => title.spec.kind === 'series')!;
	for (const file of title.files) state.byPath.delete(file.path);
	const template = title.spec.files[0];
	title.spec.files = Array.from({ length: 1000 }, (_, index) => ({
		...structuredClone(template),
		name: `Season ${Math.floor(index / 25) + 1}/${title.spec.name}.S${String(Math.floor(index / 25) + 1).padStart(2, '0')}E${String((index % 25) + 1).padStart(2, '0')}.1080p`,
		history: undefined
	}));
	title.files = title.spec.files.map((spec) => {
		const file = fileOf(title, spec);
		rejudge(state, file, now);
		state.byPath.set(file.path, file);
		return file;
	});
	publish('library');
	return [title.spec.name];
}
