// The history API. events.jsonl is append-only, so a page is addressed by byte
// offset: a line never moves.

import { request } from '$lib/api';
import type { GlyphName } from '$lib/components/Glyph.svelte';
import {
	basename,
	capitalized,
	dated,
	duration,
	DV_REMOVED,
	named,
	ruleLabel,
	size,
	shortTime,
	stamp,
	titled
} from '$lib/format';
// The library's verdict vocabulary, so the history and the library agree on
// words and colours.
import { isVerdict, judged, pip, verdictLabel, verdictText, type Card } from '$lib/library';
import { phrase, tally } from '$lib/runs';
import { words } from '$lib/search';

// Every field any event carries; `event` says which to expect.
export type Event = {
	ts: string;
	event: string;
	version: string;
	run?: string;
	source?: string;
	config_id?: string;
	path?: string;
	// Only on a remux, which publishes under a new extension.
	from_path?: string;
	detail?: string;
	reasons?: string[];
	rules?: string[];
	incidental?: string[];
	incidental_rules?: string[];
	// What the rewrite came to, in the fields a card and a queue row carry.
	adds?: string[];
	rebuilds?: string[];
	drops?: number;
	// Set where mkvpropedit wrote a tag and no rewrite ran at all.
	in_place?: boolean;
	bytes_before?: number;
	bytes_after?: number;
	seconds?: number;
	// A rewrite's slot wait and its file's running time, for the service's
	// estimates. Nothing on a page reads them.
	waited?: number;
	duration?: number;
	// A sweep summary or a webhook delivery.
	files?: number;
	// How many titles a re-check was pointed at; their files are in `files`.
	titles?: number;
	// Only on a stopped sweep or re-check: how many files it never reached.
	stopped?: number;
	dry_run?: boolean;
	library_bytes?: number;
	cached?: number;
	counts?: Record<string, number>;
	arr?: string;
	arr_label?: string;
	// The files a delivery queued, by name.
	paths?: string[];
	// The library title this line is about, as an id into the page's `titles`.
	// Absent on service-level lines and on a delivery spanning two titles.
	title?: string;
	// The settings in force, in full, so an old `config_id` still resolves.
	config?: Record<string, unknown>;
	// A settings save, or a track edited in place: only the names that moved,
	// both sides of each. A setting set for the first time moves from the empty
	// value, not from nothing.
	changed?: Record<string, { from: unknown; to: unknown }>;
	// The track an edit was made to: its stream index in the file, and its kind.
	index?: number;
	kind?: string;
	by?: string;
	// When a resumed pause began. Absent on older lines.
	paused_at?: string;
	// Where a skipped file was: `active` with a worker, `waiting` in line.
	// Absent on older lines.
	where?: string;
};

// `next` is the cursor for the next page, null once the oldest event is handed
// over. `titles` holds the grid's own cards for every title the lines name, so
// a poster here raises the library's sheet.
export type EventPage = {
	events: Event[];
	titles?: Record<string, Card>;
	next: number | null;
};

/**
 * The stretch of history a page is drawn from; `{}` is the whole file. ISO
 * 8601 moments rather than minutes, so the browser decides what "an hour ago"
 * means in its own zone.
 */
export type Span = { since?: string; until?: string };

/**
 * How many lines back the last run's summary might lie: a run writes it as it
 * closes, under everything it recorded on the way. Every reader must agree on
 * this or a summary goes unfound.
 */
export const LOOKBACK = 60;

export async function getEvents(
	fetcher: typeof fetch = fetch,
	limit = 100,
	before: number | null = null,
	span: Span = {}
): Promise<EventPage> {
	const query = new URLSearchParams({ limit: String(limit) });
	if (before !== null) query.set('before', String(before));
	if (span.since) query.set('since', span.since);
	if (span.until) query.set('until', span.until);
	const page = await request<EventPage>(`/api/events?${query}`, undefined, fetcher);
	judged(Object.values(page.titles ?? {}));
	return page;
}

const MINUTE_MS = 60_000;

/** The value that means the two ends were picked by hand. */
export const CUSTOM = 'custom';

/**
 * How far back the feed offers to look. `said` names the stretch in a sentence
 * for the empty-list line. Nothing under an hour: five columns is what a phone
 * fits beside the range button, and a hand-picked range is a different kind of
 * answer (between when and when) so it is not a sixth.
 */
export const SPANS: { value: string; label: string; minutes: number; said: string }[] = [
	{ value: '1h', label: '1h', minutes: 60, said: 'the last hour' },
	{ value: '24h', label: '24h', minutes: 60 * 24, said: 'the last day' },
	{ value: '7d', label: '7d', minutes: 60 * 24 * 7, said: 'the last week' },
	{ value: '30d', label: '30d', minutes: 60 * 24 * 30, said: 'the last month' },
	{ value: 'all', label: 'All', minutes: 0, said: 'the history' }
];

/** How a window is named in a sentence, a picked range included. */
export function said(span: string): string {
	return SPANS.find((option) => option.value === span)?.said ?? 'that range';
}

/** A preset as the moments it means, from the clock now. Called at each fetch,
 * so "the last hour" stays the last hour in a tab left open. */
export function preset(value: string): Span {
	const minutes = SPANS.find((option) => option.value === value)?.minutes ?? 0;
	return minutes ? { since: new Date(Date.now() - minutes * MINUTE_MS).toISOString() } : {};
}

/**
 * A `<input type="datetime-local">` value as a moment. `new Date` reads its
 * offset-less value in the browser's zone, which is what the typist meant.
 * `end` stretches to the close of the minute, since the field has no seconds.
 */
export function moment(value: string, end = false): string | undefined {
	if (!value) return undefined;
	const at = new Date(value);
	if (isNaN(at.getTime())) return undefined;
	return new Date(at.getTime() + (end ? 59_999 : 0)).toISOString();
}

/** The same moment back in a datetime-local field, for seeding one. */
export function field(at: Date): string {
	const pad = (part: number) => String(part).padStart(2, '0');
	const day = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
	return `${day}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

// What a line says about an event, shared by the overview and the history.

/**
 * A stable key for one line in a list that grows at both ends. Position was
 * the key, and an open panel ended up belonging to a different event.
 */
export function key(entry: Event): string {
	return `${entry.ts}|${entry.event}|${entry.run ?? ''}|${entry.path ?? ''}`;
}

export function count(value: number | undefined, noun: string): string {
	const number = value ?? 0;
	return `${number.toLocaleString()} ${noun}${number === 1 ? '' : 's'}`;
}

/** A settings value as a person reads it. Settings are flat. */
export function spell(value: unknown): string {
	if (value === undefined || value === null) return 'unset';
	if (Array.isArray(value)) return value.length ? value.join(', ') : 'nothing';
	if (typeof value === 'boolean') return value ? 'on' : 'off';
	return String(value) || 'nothing';
}

/**
 * A save as lines, named by variable rather than page label, since that is what
 * the settings and compose files call it. "to" rather than an arrow: a typed
 * arrow is in neither font, and it is what a screen reader says anyway.
 */
export function moved(entry: Event): string[] {
	return Object.entries(entry.changed ?? {}).map(
		([field, move]) => `${field}: ${spell(move.from)} to ${spell(move.to)}`
	);
}

/**
 * What happened, in the few words a single line has room for.
 *
 * `title` is the library's own name for the title the line is about, where the
 * page has the card for it. A pause is placed on a whole title, whose folder is
 * named for the *arr rather than for a reader.
 */
export function headline(entry: Event, title = ''): string {
	// What to call the file where the page has no card for its title: a film is
	// told from another rewrite of itself by its year and release words.
	const release = () => {
		const { name, detail } = named(entry.path);
		return detail ? `${name} ${detail}` : name;
	};
	switch (entry.event) {
		// One file reaching one verdict, in the library's word for it. The
		// series name, not the file: this line truncates and a release name is
		// mostly tags. The episode is `marker` below.
		case 'modified':
		case 'pending':
		case 'failed':
		case 'deferred':
			return `${verdictLabel(entry.event)}: ${title || release()}`;
		case 'sweep': {
			// Match Plan and Process without implying every checked file changed.
			// Older events without a mode keep the neutral run name.
			const action = entry.dry_run === undefined ? 'Sweep' : entry.dry_run ? 'Plan' : 'Process';
			return entry.stopped
				? `${action} stopped after ${count(entry.files, 'file')}`
				: `${action} complete · ${count(entry.files, 'file')}`;
		}
		case 'recheck':
			// File runs omit the title count because no whole title was selected.
			return entry.stopped
				? `Re-check stopped after ${count(entry.files, 'file')}`
				: `Re-checked ${entry.titles === undefined ? count(entry.files, 'file') : count(entry.titles, 'title')}`;
		case 'paused':
			return 'Processing paused';
		case 'resumed':
			return 'Processing resumed';
		case 'held': // Events written before pause terminology.
		case 'item_paused':
			return `Paused ${title || release()}`;
		case 'lifted':
		case 'item_resumed':
			return `Resumed ${title || release()}`;
		case 'skipped':
			return `${outcome(entry).word}: ${title || release()}`;
		case 'retagged':
			return `Edited tags: ${title || release()}`;
		case 'webhook': {
			// Counted even for one, since a release file name truncates at any
			// width. The name goes on the second line.
			const who = entry.arr_label || 'An import';
			return `${who} queued ${count(entry.files, 'file')}`;
		}
		case 'config':
			return 'Rules applied';
		case 'settings':
			return `Saved ${count(moved(entry).length, 'setting')}`;
		// An event this build has no words for is still worth a line.
		default:
			return entry.event;
	}
}

/** The track an edit was made to, as a person names it: "Audio stream 1". */
function stream(entry: Event): string {
	const kind = entry.kind || 'track';
	return `${kind[0].toUpperCase()}${kind.slice(1)} stream ${entry.index ?? '?'}`;
}

/** A track edit as lines, one per tag moved. The language reads as its codes
 * both sides, since that is what the file carries; a flag as where it ended,
 * under the name the editor gives it. */
export function retagged(entry: Event): string[] {
	return Object.entries(entry.changed ?? {}).map(([field, move]) =>
		field === 'lang'
			? `language ${spell(move.from)} to ${spell(move.to)}`
			: `${field === 'sdh' ? 'SDH' : field} ${spell(move.to)}`
	);
}

/** The episode a headline was about, kept out of the truncation: eight
 * rewrites of one series differ only in this. */
export function marker(entry: Event): string {
	return entry.path ? named(entry.path).episode : '';
}

/** The year and release words off the file's name, which is what a queue row
 * puts under a title. */
export function release(entry: Event): string {
	return entry.path ? named(entry.path).detail : '';
}

// Events about one file's rewrite.
const FILE_EVENTS = new Set(['modified', 'pending', 'failed', 'deferred', 'skipped']);
const TITLED = new Set([
	...FILE_EVENTS,
	'held',
	'item_paused',
	'lifted',
	'item_resumed',
	'retagged'
]);

// How many of a run's verdicts its line names before counting the rest.
const NAMED_VERDICTS = 2;

/** An event split into the parts its row draws. */
export type EventParts = {
	title: string;
	aside: string;
	meta: string[];
	// The outcome word, in its colour.
	status?: { word: string; text: string };
	// Short items after the status that never truncate, such as verdict counts or
	// a pause's length.
	facts: { text: string; tone: string }[];
	// Why, where the outcome needs it.
	reason: string;
	// About one file, whose release words truncate first.
	file: boolean;
};

/** `title` is the library's name for the title, where the page has its card. */
export function parts(entry: Event, title = ''): EventParts {
	// A webhook for one file is about that file, for several about the delivery.
	const single = entry.event === 'webhook' && entry.paths?.length === 1 ? entry.paths[0] : '';
	const path = single || (TITLED.has(entry.event) ? entry.path : '');
	if (path) {
		const file = named(path);
		const { year, words } = dated(file.detail);
		const base = {
			title: title || file.name,
			aside: file.episode || year,
			meta: words ? [words, ...notes(entry)] : opening(notes(entry)),
			facts: [],
			reason: '',
			file: true
		};
		switch (entry.event) {
			case 'held': // Events written before pause terminology.
			case 'item_paused':
			case 'lifted':
			case 'item_resumed':
				return {
					...base,
					status: PAUSES.has(entry.event)
						? { word: 'Paused', text: 'text-accent' }
						: { word: 'Resumed', text: 'text-dim' },
					facts: pauseFacts(entry).map((text) => ({ text, tone: '' }))
				};
			case 'retagged':
				return {
					...base,
					status: { word: 'Edited tags', text: 'text-dim' },
					reason: [stream(entry), ...retagged(entry)].join(' · ')
				};
			case 'webhook':
				return {
					...base,
					status: { word: `Queued by ${entry.arr_label || 'an import'}`, text: 'text-dim' }
				};
			default:
				return { ...base, status: outcome(entry), reason: detail(entry) };
		}
	}
	// A run or the service. A finished run's count moves under the headline, a
	// stopped run's stays in it.
	const said = headline(entry, title);
	const [lead, counted] = said.split(' · ');
	const rows =
		entry.event === 'sweep' || entry.event === 'recheck' ? tally(entry.counts ?? {}) : [];
	const shown = rows.slice(0, NAMED_VERDICTS).map((row) => ({
		text: phrase(row),
		tone:
			(row.state === 'pending' || row.trouble) && isVerdict(row.state) ? verdictText[row.state] : ''
	}));
	const rest = rows.slice(NAMED_VERDICTS).reduce((sum, row) => sum + row.count, 0);
	const facts = rest ? [...shown, { text: `${rest.toLocaleString()} other`, tone: '' }] : shown;
	return {
		title: lead,
		aside: '',
		meta: under(entry, counted),
		facts,
		reason: '',
		file: false
	};
}

/** The line under a run's or the service's headline. */
function under(entry: Event, counted = ''): string[] {
	// The service's pause knows no end or length, only when it began.
	if (entry.event === 'paused') return opening([shortTime(new Date(entry.ts), new Date())]);
	if (PAUSES.has(entry.event) || RESUMES.has(entry.event)) return opening(pauseFacts(entry));
	const first =
		entry.event === 'sweep'
			? counted
			: entry.event === 'recheck'
				? entry.titles === undefined
					? ''
					: count(entry.files, 'file')
				: detail(entry);
	return first ? [first, ...notes(entry)] : opening(notes(entry));
}

/** Capitalizes the first item, which leads its line. */
function opening(items: string[]): string[] {
	const [first, ...rest] = items;
	return first ? [capitalized(first), ...rest] : [];
}

/** How long ago, as a person says it rather than as a clock does. */
export function ago(ts: string, now = Date.now()): string {
	const seconds = Math.max(0, (now - new Date(ts).getTime()) / 1000);
	if (seconds < 90) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	return `${Math.round(hours / 24)}d ago`;
}

/** The verdicts a sweep summary carries, in the order runs put them in. */
export function verdicts(entry: Event): string[] {
	return tally(entry.counts ?? {}).map(phrase);
}

// How many changes a line names before counting them: what a phone fits.
const NAMED_CHANGES = 2;

/** Which rules fired, by their own names with the underscores unpicked. */
function firedRules(entry: Event): string[] {
	return [...(entry.rules ?? []), ...(entry.incidental_rules ?? [])].map((rule) =>
		ruleLabel(rule).replace(/_/g, ' ')
	);
}

/** The second line under a headline: why, or what, in a few more words. */
export function detail(entry: Event): string {
	switch (entry.event) {
		case 'modified':
		case 'pending': {
			const changes = [...(entry.reasons ?? []), ...(entry.incidental ?? [])];
			if (changes.length <= NAMED_CHANGES) return changes.join(', ');
			// Past that, how many and under which rules. Naming the first two would
			// make an accident of stream order read as the point of the rewrite.
			const rules = firedRules(entry);
			const many = count(changes.length, 'change');
			return rules.length ? `${many} · ${rules.join(', ')}` : many;
		}
		case 'failed':
		case 'deferred':
			return entry.detail ?? '';
		case 'sweep':
			return verdicts(entry).join(' · ');
		case 'recheck':
			return [
				...(entry.titles === undefined ? [] : [count(entry.files, 'file')]),
				...verdicts(entry)
			].join(' · ');
		case 'paused':
		case 'resumed':
		case 'held': // Events written before pause terminology.
		case 'item_paused':
		case 'lifted':
		case 'item_resumed':
			return pauseFacts(entry).join(' · ');
		case 'skipped':
			// Older lines have no words for where the file was.
			return entry.detail ?? 'skipped for this run';
		case 'retagged':
			return [stream(entry), ...retagged(entry)].join(' · ');
		case 'webhook': {
			// What the headline counted, by name. Whole, since this line wraps and
			// two episodes of one series otherwise read as one name twice.
			const queued = entry.paths ?? [];
			if (!queued.length) return '';
			const shown = queued.slice(0, 2).map(titled);
			const rest = queued.length - shown.length;
			return rest ? `${shown.join(' · ')} + ${rest} more` : shown.join(' · ');
		}
		case 'config':
			return 'Active rules for subsequent processing';
		case 'settings': {
			// The first two; the rest are one tap away.
			const lines = moved(entry);
			const shown = lines.slice(0, 2);
			const rest = lines.length - shown.length;
			return rest ? `${shown.join(' · ')} + ${rest} more` : shown.join(' · ');
		}
		default:
			return '';
	}
}

function extension(path: string | undefined): string {
	const base = basename(path);
	return base.slice(base.lastIndexOf('.'));
}

/** What the rewrite wrote, as the library's own chips read it. */
export function layouts(entry: Event): { adds?: string[]; rebuilds?: string[] } {
	return { adds: entry.adds, rebuilds: entry.rebuilds };
}

// Events placing a pause on a title or the service, and events lifting one.
const PAUSES = new Set(['held', 'item_paused', 'paused']);
const RESUMES = new Set(['lifted', 'item_resumed', 'resumed']);
// Only a title's pause may have an end, so only it says until resumed.
const OPEN_ENDED = new Set(['held', 'item_paused']);

/** A pause's start, end and length, as far as its event knows. */
function span(entry: Event): { from?: Date; to?: Date; seconds?: number } {
	const at = new Date(entry.ts);
	if (PAUSES.has(entry.event)) {
		const seconds = entry.seconds || undefined;
		return { from: at, to: seconds ? new Date(at.getTime() + seconds * 1000) : undefined, seconds };
	}
	const from = entry.paused_at ? new Date(entry.paused_at) : undefined;
	if (!from || isNaN(from.getTime())) return { to: at };
	return { from, to: at, seconds: Math.max(0, (at.getTime() - from.getTime()) / 1000) };
}

/** How long a pause ran and its other end, for the row's status line. */
function pauseFacts(entry: Event): string[] {
	const { from, to, seconds = 0 } = span(entry);
	if (!from || !to) return OPEN_ENDED.has(entry.event) ? ['until resumed'] : [];
	return PAUSES.has(entry.event)
		? [`for ${duration(seconds)}`, `until ${shortTime(to, from)}`]
		: [`after ${duration(seconds)}`, `paused ${shortTime(from, to)}`];
}

/** What the rewrite moved, as chips beside the layouts: the tracks it took
 * away, the container it published under, and what it cost in bytes. */
export function measures(entry: Event): string[] {
	const out: string[] = [];
	const stripped = [...(entry.rules ?? []), ...(entry.incidental_rules ?? [])].includes('dv_strip');
	if (stripped && entry.event === 'modified') out.push(DV_REMOVED);
	if (stripped && entry.event === 'pending') out.push('Remove Dolby Vision');
	if (entry.drops) out.push(`−${entry.drops}`);
	if (entry.from_path) out.push(`${extension(entry.from_path)} to ${extension(entry.path)}`);
	if (entry.bytes_before !== undefined && entry.bytes_after !== undefined) {
		const delta = entry.bytes_after - entry.bytes_before;
		out.push(`${delta < 0 ? '−' : '+'}${size(delta)}`);
	}
	return out;
}

/** How long it took and under what terms, for the dim end of the line. */
export function notes(entry: Event): string[] {
	const out: string[] = [];
	// A pause says its length in words, and the same span twice reads as a
	// stutter. Under a second there is nothing worth saying: a probe that
	// deferred the file took no time and reads as "0s".
	if (entry.seconds !== undefined && entry.seconds >= 1 && !PAUSES.has(entry.event))
		out.push(duration(entry.seconds));
	// Named, since the line it joins is a list of counts.
	if (entry.event === 'sweep' && entry.library_bytes)
		out.push(`${size(entry.library_bytes)} library`);
	if (entry.in_place) out.push('in place');
	if (entry.dry_run) out.push('dry run');
	return out;
}

/** What an expanded row shows: everything the line itself had no room for. */
export type Detail = { label: string; values: string[]; mono?: boolean };

function changed(now: Event, before: Event): string[] {
	const after = now.config ?? {};
	const previous = before.config ?? {};
	return [...new Set([...Object.keys(previous), ...Object.keys(after)])]
		.sort()
		.filter((field) => spell(previous[field]) !== spell(after[field]))
		.map((field) => `${field}: ${spell(previous[field])} to ${spell(after[field])}`);
}

// Event sources, named as the overview names runs.
const SOURCES: Record<string, string> = {
	sweep: 'Sweep',
	webhook: 'Import',
	cli: 'Command line'
};

/** The rows an opened event shows. `before` is the previous config event;
 * without it a `config` line shows the settings whole. */
export function details(entry: Event, before?: Event): Detail[] {
	const out: Detail[] = [];
	const add = (label: string, values: (string | undefined)[], mono = false) => {
		const kept = values.filter((value) => !!value) as string[];
		if (kept.length) out.push({ label, values: kept, mono });
	};
	switch (entry.event) {
		case 'modified':
		case 'pending':
		case 'failed':
		case 'deferred':
		case 'skipped':
			add('File', [entry.path], true);
			add('Replaced', [entry.from_path], true);
			// A skip's detail is the app's own words. A failure's is the tool's
			// message, spelled as it came.
			if (entry.event === 'skipped')
				add(outcome(entry).word, [entry.detail && capitalized(entry.detail)]);
			else add('Problem', [entry.detail]);
			add('Reasons', (entry.reasons ?? []).map(capitalized));
			add('Additional changes', (entry.incidental ?? []).map(capitalized));
			add('Rules', [firedRules(entry).join(', ')]);
			add('Added', entry.adds ?? []);
			add('Rebuilt', entry.rebuilds ?? []);
			add('Dropped', entry.drops ? [count(entry.drops, 'track')] : []);
			if (entry.bytes_before !== undefined && entry.bytes_after !== undefined) {
				add('Size', [`${size(entry.bytes_before)} to ${size(entry.bytes_after)}`], true);
			}
			add('By', [entry.by]);
			break;
		case 'webhook':
			// A delivery recorded before the names were.
			add('Files', entry.paths ?? ['recorded as a count only'], !!entry.paths);
			break;
		case 'sweep':
		case 'recheck':
			// Only a re-check has these two, and only the count is worth a row.
			add('Titles', [entry.titles === undefined ? undefined : count(entry.titles, 'title')]);
			add('Files', [entry.event === 'recheck' ? count(entry.files, 'file') : undefined]);
			add('Verdicts', verdicts(entry));
			// Only on a stopped run.
			add('Not checked', [entry.stopped === undefined ? undefined : count(entry.stopped, 'file')]);
			add('Library', [entry.library_bytes ? size(entry.library_bytes) : undefined], true);
			add('Cached results', [
				entry.cached === undefined ? undefined : count(entry.cached, 'result')
			]);
			break;
		case 'config': {
			if (before) {
				const lines = changed(entry, before);
				add('Changed', lines.length ? lines : ['nothing']);
			} else {
				// The oldest config in the window has nothing to differ from.
				add(
					'Settings',
					Object.entries(entry.config ?? {}).map(([field, value]) => `${field}: ${spell(value)}`)
				);
			}
			break;
		}
		case 'settings':
			add('Changed', moved(entry));
			add('By', [entry.by]);
			break;
		case 'paused':
		case 'resumed':
		case 'held': // Events written before pause terminology.
		case 'item_paused':
		case 'lifted':
		case 'item_resumed': {
			// A title-wide pause's path is its folder. The service's has none.
			add('Path', [entry.path], true);
			const pause = span(entry);
			add('Paused', [pause.from && stamp(pause.from.toISOString())]);
			add(PAUSES.has(entry.event) ? 'Until' : 'Resumed', [
				pause.to && stamp(pause.to.toISOString())
			]);
			add('Length', [
				pause.seconds !== undefined
					? duration(pause.seconds)
					: OPEN_ENDED.has(entry.event)
						? 'Until resumed'
						: undefined
			]);
			add('By', [entry.by]);
			break;
		}
		case 'retagged':
			add('File', [entry.path], true);
			add('Track', [stream(entry)]);
			add('Changed', retagged(entry).map(capitalized));
			add('By', [entry.by]);
			break;
	}
	add('Source', [entry.source && (SOURCES[entry.source] ?? capitalized(entry.source))]);
	add('Run', [entry.run], true);
	add('Settings id', [entry.config_id], true);
	add('Version', [entry.version], true);
	return out;
}

// Kept from one keystroke to the next, since `details` allocates per line and a
// search runs over everything loaded. Rebuilt if the card landed after it.
const HAYSTACKS = new WeakMap<Event, { title: string; text: string }>();

/**
 * A line as the words a search runs against, which is everything the row can
 * show, opened or not. The event's own name goes in too, since the headline
 * may use "Plan" rather than "sweep".
 */
export function searchable(entry: Event, title = ''): string {
	const held = HAYSTACKS.get(entry);
	if (held?.title === title) return held.text;
	const text = words(
		[entry.event, headline(entry, title), detail(entry)]
			.concat(details(entry).flatMap((row) => row.values))
			.join(' ')
	);
	HAYSTACKS.set(entry, { title, text });
	return text;
}

// The verdict's colour from the library's pips; everything else is background.
export function dot(entry: Event): string {
	return isVerdict(entry.event) ? pip[entry.event] : 'bg-faint';
}

/** The word a line about one file ends on, in its colour. */
export type Outcome = { word: string; text: string };

/**
 * The verdict, or what a person did to the file: Cancelled where a worker had
 * it, Skipped where none had, as the buttons say. Not verdicts, so no colour.
 */
export function outcome(entry: Event): Outcome {
	if (isVerdict(entry.event))
		return { word: verdictLabel(entry.event), text: verdictText[entry.event] };
	return { word: entry.where === 'active' ? 'Cancelled' : 'Skipped', text: 'text-faint' };
}

/**
 * The mark for a line about the service rather than a title, which has its
 * poster to be known by. Both runs share one: what the line is about is a run,
 * and which kind is the first word of the headline. Only these kinds: a mark
 * for a title's line would be a picture of nothing beside the words that
 * already say it.
 */
const MARKS: Record<string, GlyphName> = {
	sweep: 'refresh',
	recheck: 'refresh',
	paused: 'pause',
	resumed: 'play',
	config: 'sliders',
	settings: 'sliders',
	// A delivery spanning two titles names neither, so it has no poster to show
	// even though it is about files arriving.
	webhook: 'arrow'
};

export function badge(entry: Event): GlyphName | '' {
	return MARKS[entry.event] ?? '';
}
