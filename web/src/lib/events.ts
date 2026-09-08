// The history API. events.jsonl is append-only, so a page is addressed by byte
// offset: a line never moves.

import { request } from '$lib/api';
import { basename, duration, named, size, titled } from '$lib/format';
// The library's verdict vocabulary, so the history and the library agree on
// words and colours.
import { pip, verdictLabel, type Card } from '$lib/library';
import { phrase, tally } from '$lib/runs';

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
	downmixed?: string[];
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
	// The files a delivery queued, by name.
	paths?: string[];
	// The library title this line is about, as an id into the page's `titles`.
	// Absent on service-level lines and on a delivery spanning two titles.
	title?: string;
	// The settings in force, in full, so an old `config_id` still resolves.
	config?: Record<string, unknown>;
	// A settings save: only the names that moved. A side that held nothing is
	// absent rather than null.
	changed?: Record<string, { from?: unknown; to?: unknown }>;
	by?: string;
	// Why a title was held. `seconds` is how long the hold was placed for, and
	// is absent on one with no end.
	reason?: string;
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

export function getEvents(
	fetcher: typeof fetch = fetch,
	limit = 100,
	before: number | null = null,
	span: Span = {}
): Promise<EventPage> {
	const query = new URLSearchParams({ limit: String(limit) });
	if (before !== null) query.set('before', String(before));
	if (span.since) query.set('since', span.since);
	if (span.until) query.set('until', span.until);
	return request<EventPage>(`/api/events?${query}`, undefined, fetcher);
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

/** What happened, in the few words a single line has room for. */
export function headline(entry: Event): string {
	switch (entry.event) {
		// One file reaching one verdict, in the library's word for it. The
		// series name, not the file: this line truncates and a release name is
		// mostly tags. The episode is `marker` below.
		case 'fixed':
		case 'would-fix':
		case 'failed':
		case 'deferred':
			return `${verdictLabel(entry.event)}: ${named(entry.path).name}`;
		case 'sweep':
			// A stopped sweep's count is part of a library, and must say so.
			return entry.stopped
				? `Sweep stopped after ${count(entry.files, 'file')}`
				: `Swept ${count(entry.files, 'file')}`;
		case 'recheck':
			// Counted in titles, which is what was selected. Files are the detail
			// line's.
			return entry.stopped
				? `Re-check stopped after ${count(entry.files, 'file')}`
				: `Re-checked ${count(entry.titles, 'title')}`;
		case 'paused':
			return 'Processing paused';
		case 'resumed':
			return 'Processing resumed';
		case 'held':
			return `Held ${named(entry.path).name}`;
		case 'lifted':
			return `Hold lifted on ${named(entry.path).name}`;
		case 'skipped':
			return `Skipped ${named(entry.path).name}`;
		case 'webhook': {
			// Counted even for one, since a release file name truncates at any
			// width. The name goes on the second line.
			const who = entry.arr ? entry.arr[0].toUpperCase() + entry.arr.slice(1) : 'An import';
			return `${who} queued ${count(entry.files, 'file')}`;
		}
		case 'config':
			return 'Rules applied';
		case 'settings':
			return `Changed ${count(moved(entry).length, 'setting')}`;
		// An event this build has no words for is still worth a line.
		default:
			return entry.event;
	}
}

/** The episode a headline was about, kept out of the truncation: eight
 * rewrites of one series differ only in this. */
export function marker(entry: Event): string {
	return entry.path ? named(entry.path).episode : '';
}

/** How long ago, as a person says it rather than as a clock does. */
export function ago(ts: string): string {
	const seconds = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
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
		rule.replace(/_/g, ' ')
	);
}

/** The second line under a headline: why, or what, in a few more words. */
export function detail(entry: Event): string {
	switch (entry.event) {
		case 'fixed':
		case 'would-fix': {
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
			// The file count leads: the headline counted titles.
			return [count(entry.files, 'file'), ...verdicts(entry)].join(' · ');
		case 'paused':
		case 'resumed':
		case 'lifted':
			return entry.by ? `By ${entry.by}.` : '';
		case 'held':
			return [
				`Not rewritten ${entry.seconds ? `for ${duration(entry.seconds)}` : 'until it is lifted'}`,
				entry.reason,
				entry.by && `by ${entry.by}`
			]
				.filter(Boolean)
				.join(' · ');
		case 'skipped':
			// The run it was taken off is in the opened row; a skip lasts no
			// longer than that run.
			return entry.by ? `Left alone for that run by ${entry.by}.` : '';
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
			return 'The rules in force from here on.';
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

/** The mono chips under a line: the numbers, kept out of the prose. */
export function chips(entry: Event): string[] {
	const out: string[] = [];
	if (entry.from_path) out.push(`${extension(entry.from_path)} to ${extension(entry.path)}`);
	if (entry.bytes_before !== undefined && entry.bytes_after !== undefined) {
		const delta = entry.bytes_after - entry.bytes_before;
		out.push(`${delta < 0 ? '−' : '+'}${size(delta)}`);
	}
	for (const layout of entry.downmixed ?? []) out.push(`+${layout}`);
	if (entry.event === 'sweep' && entry.library_bytes) out.push(size(entry.library_bytes));
	if (entry.seconds !== undefined) out.push(duration(entry.seconds));
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

/** The rows an opened event shows. `before` is the previous config event;
 * without it a `config` line shows the settings whole. */
export function details(entry: Event, before?: Event): Detail[] {
	const out: Detail[] = [];
	const add = (label: string, values: (string | undefined)[], mono = false) => {
		const kept = values.filter((value) => !!value) as string[];
		if (kept.length) out.push({ label, values: kept, mono });
	};
	switch (entry.event) {
		case 'fixed':
		case 'would-fix':
		case 'failed':
		case 'deferred':
			add('File', [entry.path], true);
			add('Replaced', [entry.from_path], true);
			add('Problem', [entry.detail]);
			add('Reasons', entry.reasons ?? []);
			add('Alongside', entry.incidental ?? []);
			add('Rules', [[...(entry.rules ?? []), ...(entry.incidental_rules ?? [])].join(', ')]);
			add('Generated', entry.downmixed ?? []);
			if (entry.bytes_before !== undefined && entry.bytes_after !== undefined) {
				add('Size', [`${size(entry.bytes_before)} to ${size(entry.bytes_after)}`], true);
			}
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
			add('Not looked at', [
				entry.stopped === undefined ? undefined : count(entry.stopped, 'file')
			]);
			add('Library', [entry.library_bytes ? size(entry.library_bytes) : undefined], true);
			add('Reused', [
				entry.cached === undefined ? undefined : `${entry.cached.toLocaleString()} verdicts`
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
		case 'held':
		case 'lifted':
		case 'skipped':
			add('File', [entry.path], true);
			add('Reason', [entry.reason]);
			add('By', [entry.by]);
			break;
	}
	add('Source', [entry.source]);
	add('Run', [entry.run], true);
	add('Settings id', [entry.config_id], true);
	add('Version', [entry.version], true);
	return out;
}

// The verdict's colour from the library's pips; everything else is background.
export function dot(entry: Event): string {
	return pip[entry.event] ?? 'bg-faint';
}
