// The service's API, answered from the state: one row per path, the same
// access rules, the same refusals. What each handler returns is what the
// service would put on the wire.

import { queueKey, type QueueItem } from '$lib/queue';
import type { ConnectionResult } from '$lib/connections';
import type { EventPage } from '$lib/events';
import type { Sort } from '$lib/order.svelte';
import type { DirCheck, ScheduleCheck } from '$lib/sweep';
import { localStamp, nextRuns } from './cron';
import {
	abort,
	activity,
	queued,
	queuePage,
	reorder,
	undoQueue,
	mayRewrite,
	recheck,
	refused,
	runLog,
	SCENARIOS,
	seed,
	simulateImport,
	setPaused,
	skip,
	start,
	startSweep,
	stopRun,
	type Refused
} from './runs';
import { publish } from './stream';
import { VERSION } from './util';
import {
	wireFile,
	card,
	clearVerdicts,
	detail,
	pausesNow,
	resumePauses,
	links,
	placePauses,
	retag,
	saveSettings,
	settingsSnapshot,
	shelf,
	summary,
	currentState,
	type State
} from './state';

export type Answer = { status: number; body: unknown };

type Body = Record<string, unknown>;
type Handler = (state: State, query: URLSearchParams, body: Body) => Answer | Promise<Answer>;

// Who may call a route: anyone signed in, or only an admin.
type Route = { path: string; admin: boolean; handler: Handler };

const ok = (body: unknown): Answer => ({ status: 200, body });
const refuse = (status: number, text: string): Answer => ({ status, body: { status: text } });
/** The state, seeded and ticking. */
function current(): State {
	const here = currentState();
	if (!here.seeded) {
		seed(here);
		here.seeded = true;
	}
	start(here);
	return here;
}

function busy(here: State): Answer | null {
	const going = here.runs.find((run) => run.kind !== 'import');
	return going
		? { status: 409, body: { status: 'a sweep is running. Stop it first', run: going.id } }
		: null;
}

function strings(value: unknown): string[] {
	return Array.isArray(value) ? value.map(String) : [];
}

/** Which titles a body names, by id or by the folder a pause names. */
function named(here: State, body: Body) {
	const ids = strings(body.ids);
	const paths = strings(body.paths);
	return here.titles.filter(
		(title) => ids.includes(title.spec.id) || paths.includes(title.spec.folder)
	);
}

function pauseTargets(here: State, body: Body) {
	return [...named(here, body), ...strings(body.paths).filter((path) => here.byPath.has(path))];
}

function eventsPage(here: State, query: URLSearchParams): EventPage {
	const limit = Math.max(1, Math.min(500, Number(query.get('limit')) || 100));
	const before = query.get('before');
	const since = query.get('since');
	const until = query.get('until');
	const from = before === null ? Infinity : Number(before);
	const within = here.lines.filter(
		({ seq, entry }) => seq < from && (!since || entry.ts >= since) && (!until || entry.ts <= until)
	);
	const page = within.slice(0, limit);
	const titles: EventPage['titles'] = {};
	for (const { entry } of page) {
		const title = entry.title ? here.byId.get(entry.title) : undefined;
		if (title && !titles[title.spec.id]) titles[title.spec.id] = card(here, title);
	}
	return {
		events: page.map((line) => line.entry),
		titles,
		next: within.length > limit ? page[page.length - 1].seq : null
	};
}

// What each service answers when it is reachable, worded as it words it. An
// *arr names itself and its version, Plex counts libraries, Jellyfin gives its
// server name.
const CONNECTIONS: Record<string, { label: string; key: string; detail: string; arr: boolean }> = {
	radarr: {
		label: 'Radarr',
		key: 'RADARR_API_KEY',
		detail: 'Radarr 5.14.0.9383',
		arr: true
	},
	sonarr: {
		label: 'Sonarr',
		key: 'SONARR_API_KEY',
		detail: 'Sonarr 4.0.10.2544',
		arr: true
	},
	plex: {
		label: 'Plex',
		key: 'PLEX_TOKEN',
		detail: 'Plex, 2 libraries on tower',
		arr: false
	},
	jellyfin: {
		label: 'Jellyfin',
		key: 'JELLYFIN_API_KEY',
		detail: 'Jellyfin 10.10.3',
		arr: false
	}
};

// The folders the demo's media servers index, for the hint about a library
// nothing we sweep lands inside.
const INDEXED: Record<string, string[]> = { plex: ['/media'], jellyfin: ['/media'] };

function testConnection(here: State, body: Body): ConnectionResult {
	const name = String(body.service ?? '');
	const service = CONNECTIONS[name];
	const url = String(body.url || here.settings[`${name.toUpperCase()}_URL`] || '').replace(
		/\/+$/,
		''
	);
	// The saved key never leaves the service, so what is held is whether it is
	// set, which is all the check reads.
	const key = String(body.key || '') || (here.secretsSet.has(service.key) ? 'set' : '');
	const webhook = service.arr ? 'connected' : '';
	if (!url || !key) {
		const missing = !url ? 'address' : 'API key';
		return {
			ok: false,
			detail: `No ${missing} set, so ${service.label} is switched off.`,
			hint: '',
			webhook: '',
			webhook_detail: ''
		};
	}
	if (!/^https?:\/\//.test(url))
		return {
			ok: false,
			detail: 'The address has to start with http:// or https://.',
			hint: '',
			webhook: '',
			webhook_detail: ''
		};
	return {
		ok: true,
		detail: service.detail,
		hint: pathHint(here, name),
		webhook,
		webhook_detail: ''
	};
}

/** The hint a media server gets when it indexes nothing the sweep would send
 * it. Invisible otherwise, since a refresh is best effort. */
function pathHint(here: State, name: string): string {
	const indexed = INDEXED[name];
	if (!indexed) return '';
	const mapping = strings(here.settings[`${name.toUpperCase()}_PATH_MAP`]);
	const ours = strings(here.settings.MEDIA_DIRS).map((dir) => mapped(dir, mapping));
	if (!ours.length) return '';
	// Either direction: a server may index the whole library or one folder in it.
	const lands = ours.some((mine) =>
		indexed.some((theirs) => mine.startsWith(theirs) || theirs.startsWith(mine))
	);
	if (lands) return '';
	return (
		`It indexes ${indexed.join(', ')}, which nothing in MEDIA_DIRS ` +
		`(${strings(here.settings.MEDIA_DIRS).join(', ')}) lands inside, so refreshes would be ` +
		`skipped. Add a path map below, such as ${ours[0]}=${indexed[0]}.`
	);
}

/** One path through a `from=to` mapping, longest prefix first. */
function mapped(path: string, mapping: string[]): string {
	for (const entry of [...mapping].sort((left, right) => right.length - left.length)) {
		const [from, to] = entry.split('=');
		if (from && to && (path === from || path.startsWith(`${from}/`)))
			return to + path.slice(from.length);
	}
	return path;
}

function checkDir(here: State, path: string): DirCheck {
	// A colon separates the entries, so one in a path is the only outright
	// refusal. Nothing mounted is a warning, since a mount may come later.
	if (path.includes(':'))
		return {
			path,
			state: 'invalid',
			detail: 'A colon separates the entries, so a path cannot contain one.'
		};
	if (!strings(here.settings.MEDIA_DIRS).includes(path))
		return {
			path,
			state: 'missing',
			detail: 'Nothing is mounted there yet, so nothing would be swept.'
		};
	return { path, state: 'ok', detail: '' };
}

function checkSweep(here: State, body: Body): ScheduleCheck {
	const at = String(body.at ?? '');
	const zone = String(body.tz || here.settings.TZ || 'UTC');
	const dirs = strings(body.dirs).map((path) => checkDir(here, path));
	// No schedule is not a problem, it leaves the sweep to be started by hand.
	if (!at.trim()) return { ok: true, runs: [], zone, error: '', dirs };
	try {
		return { ok: true, runs: nextRuns(at, new Date(), 3).map(localStamp), zone, error: '', dirs };
	} catch (error) {
		return { ok: false, runs: [], zone, error: (error as Error).message, dirs };
	}
}

function signIn(here: State, body: Body): Answer {
	const username = String(body.username ?? '').trim();
	if (!username || !body.password) return refuse(401, 'wrong username or password');
	// Any name and password: the demo has nothing to protect. One name is a way
	// to see the read-only role.
	here.account = {
		name: username,
		role: username.toLowerCase() === 'viewer' ? 'viewer' : 'admin',
		must_change: false
	};
	return ok(here.account);
}

const GET: Route[] = [
	{
		path: '/api/queue',
		admin: false,
		handler: (here, query) =>
			ok(queuePage(here, query.get('q') ?? '', Number(query.get('offset')) || 0))
	},
	{
		path: '/api/status',
		admin: false,
		handler: (here) => ok({ version: VERSION, ...queueStatus(here) })
	},
	{ path: '/api/runs', admin: false, handler: (here) => ok(activity(here)) },
	{
		path: '/api/runs/log',
		admin: false,
		handler: (here, query) => {
			const run = query.get('run') ?? '';
			const path = query.get('path') ?? '';
			if (!run || !path) return refuse(400, 'run and path are required');
			return ok({ run, path, lines: runLog(here, run, path) });
		}
	},
	{
		path: '/api/pauses',
		admin: false,
		handler: (here) => ok({ pauses: pausesNow(here, Date.now()) })
	},
	{ path: '/api/settings', admin: false, handler: (here) => ok(settingsSnapshot(here)) },
	{ path: '/api/events', admin: false, handler: (here, query) => ok(eventsPage(here, query)) },
	{ path: '/api/library', admin: false, handler: (here) => ok(shelf(here)) },
	{
		path: '/api/library/summary',
		admin: false,
		handler: (here, query) => ok(summary(here, (query.get('sort') || 'processed') as Sort))
	},
	{
		path: '/api/library/work',
		admin: false,
		handler: (here, query) => {
			const title = here.byId.get(query.get('id') ?? '');
			if (!title) return refuse(404, 'no such title');
			const matches = (path: string) =>
				path === title.spec.folder || path.startsWith(`${title.spec.folder}/`);
			return ok({
				queued: queued(here).filter((item) => matches(item.path)),
				active: here.runs.flatMap((run) =>
					run.active
						.filter((item) => matches(item.path))
						.map((item) => ({ ...item, run: run.id, stopping: run.stopping }))
				),
				pauses: pausesNow(here, Date.now())
			});
		}
	},
	{
		path: '/api/library/file',
		admin: false,
		handler: (here, query) => {
			const path = query.get('path');
			if (!path) return refuse(400, 'path is required');
			const file = here.byPath.get(path);
			const title = file?.title ?? here.titles.find((title) => title.spec.folder === path);
			return ok({
				current: here.current,
				file: file ? wireFile(file) : null,
				card: title ? card(here, title) : null
			});
		}
	},
	{
		path: '/api/library/title',
		admin: false,
		handler: (here, query) => {
			const title = here.byId.get(query.get('id') ?? '');
			return title ? ok(detail(here, title)) : refuse(404, 'no such title');
		}
	},
	{
		path: '/api/library/links',
		admin: false,
		handler: (here, query) => {
			const title = here.byId.get(query.get('id') ?? '');
			return title ? ok({ links: links(here, title) }) : refuse(404, 'no such title');
		}
	},
	{ path: '/api/library/ratings', admin: false, handler: (here) => ok(ratings(here)) }
];

const POST: Route[] = [
	{
		path: '/api/demo/import',
		admin: true,
		handler: (here, _query, body) => {
			if (body.arr !== 'radarr' && body.arr !== 'sonarr')
				return refuse(400, 'choose Radarr or Sonarr');
			const result = simulateImport(here, body.arr);
			return refused(result) ? result : ok(result);
		}
	},
	{
		path: '/api/demo/scenario',
		admin: true,
		handler: (here, _query, body) => {
			const pose = SCENARIOS[String(body.name)];
			if (!pose) return refuse(400, `no scenario named ${body.name}`);
			const result = pose(here, Date.now());
			return refused(result) ? result : ok(result);
		}
	},
	{
		path: '/api/queue',
		admin: true,
		handler: (here, _query, body) => {
			if (body.action === 'undo')
				return undoQueue(here, body.token)
					? ok({ restored: true })
					: refuse(409, 'the queue was reordered again; undo is no longer available');
			const items = body.items as QueueItem[];
			if (!Array.isArray(items) || !items.length) return refuse(400, 'name queued files');
			if (body.action === 'top') return ok(reorder(here, items));
			const keys = new Set(items.map(queueKey)),
				targets = queued(here).filter((item) => keys.has(queueKey(item)));
			if (body.action === 'pause')
				placePauses(
					here,
					targets.map((item) => item.path),
					Number(body.seconds) || 0,
					'',
					here.account!.name
				);
			for (const item of targets) skip(here, item.run, item.path, here.account!.name);
			return ok({ changed: targets.length });
		}
	},
	{
		path: '/api/auth/logout',
		admin: false,
		handler: (here) => ((here.account = null), ok({ status: 'signed out' }))
	},
	{ path: '/api/auth/password', admin: false, handler: (here) => ok(here.account) },
	{
		path: '/api/settings',
		admin: true,
		handler: (here, _query, body) => {
			const outcome = saveSettings(
				here,
				body as Record<string, string | boolean | string[] | null>
			);
			return 'problems' in outcome ? { status: 400, body: outcome } : ok(outcome);
		}
	},
	{
		path: '/api/connections/test',
		admin: true,
		handler: async (here, _query, body) => {
			// A name the service does not know never reaches the check itself.
			if (!CONNECTIONS[String(body.service ?? '')]) return refuse(404, 'no such service');
			return (await pause(600), ok(testConnection(here, body)));
		}
	},
	{
		path: '/api/sweep/check',
		admin: true,
		handler: (here, _query, body) => ok(checkSweep(here, body))
	},
	{
		path: '/api/runs/start',
		admin: true,
		handler: (here, _query, body) => answerOf(startSweep(here, String(body.mode ?? 'report')))
	},
	{
		path: '/api/runs/stop',
		admin: true,
		handler: (here, _query, body) => answerOf(stopRun(here, String(body.run ?? '')))
	},
	{
		path: '/api/runs/pause',
		admin: true,
		handler: (here) => (setPaused(here, true, here.account!.name), ok(activity(here)))
	},
	{
		path: '/api/runs/resume',
		admin: true,
		handler: (here) => (setPaused(here, false, here.account!.name), ok(activity(here)))
	},
	{ path: '/api/runs/abort', admin: true, handler: (here) => ok(abort(here)) },
	{
		path: '/api/runs/skip',
		admin: true,
		handler: (here, _query, body) => {
			const run = String(body.run ?? '');
			const path = String(body.path ?? '');
			if (!run || !path) return refuse(400, 'name the run and the file to skip');
			return answerOf(skip(here, run, path, here.account!.name));
		}
	},
	{
		path: '/api/pauses',
		admin: true,
		handler: (here, _query, body) => {
			const titles = pauseTargets(here, body);
			if (!titles.length) return refuse(404, 'no such title or file');
			placePauses(
				here,
				titles,
				Number(body.seconds) || 0,
				String(body.reason ?? ''),
				here.account!.name
			);
			return ok({ pauses: pausesNow(here, Date.now()) });
		}
	},
	{
		path: '/api/pauses/resume',
		admin: true,
		handler: (here, _query, body) => {
			resumePauses(here, pauseTargets(here, body), here.account!.name);
			return ok({ pauses: pausesNow(here, Date.now()) });
		}
	},
	{
		path: '/api/library/run',
		admin: true,
		handler: (here, _query, body) => {
			const titles = named(here, body);
			if (!strings(body.ids).length) return refuse(400, 'name the titles to run');
			if (!titles.length) return refuse(404, 'no such title');
			return answerOf(recheck(here, titles, String(body.mode ?? 'report')));
		}
	},
	{
		path: '/api/library/retag',
		admin: true,
		handler: (here, _query, body) => {
			const targets = Array.isArray(body.tracks)
				? (body.tracks as { path: string; index: number }[])
				: [];
			if (!targets.length) return refuse(400, 'name the tracks to edit');
			return ok({
				results: retag(
					here,
					targets,
					body as { lang?: string; flags?: Record<string, boolean> },
					here.account!.name
				)
			});
		}
	},
	{
		path: '/api/library/clear',
		admin: true,
		handler: (here) => busy(here) ?? ok({ status: 'cleared', dropped: clearVerdicts(here) })
	},
	{
		path: '/api/library/ratings',
		admin: true,
		handler: async (here) => {
			if (!here.settings.IMDB_RATINGS) return refuse(409, 'title scores are switched off');
			await pause(900);
			here.ratingsFetched = Math.floor(Date.now() / 1000);
			publish('library');
			return ok({ ...ratings(here), titles: here.titles.filter((title) => title.spec.arr).length });
		}
	}
];

function answerOf(outcome: Refused | object): Answer {
	return refused(outcome) ? { status: outcome.status, body: outcome.body } : ok(outcome);
}

function ratings(here: State) {
	return {
		scored: here.settings.IMDB_RATINGS
			? here.titles.filter((title) => title.spec.rating).length
			: 0,
		fetched: here.ratingsFetched
	};
}

/** What /api/status carries beside the version: the queue's numbers, without
 * the runs. */
function queueStatus(here: State) {
	const { queue, working, rewrites, slots, parked, may_rewrite, next_sweep, pauses } =
		activity(here);
	return { queue, working, rewrites, slots, parked, may_rewrite, next_sweep, pauses };
}

function pause(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

/** One request against the demo's service. */
export async function answer(
	method: string,
	path: string,
	query: URLSearchParams,
	body: Body
): Promise<Answer> {
	const here = current();
	if (path === '/health') return ok({ status: 'ok', version: VERSION });
	if (path === '/api/auth/me')
		return here.account ? ok(here.account) : refuse(401, 'not signed in');
	if (path === '/api/auth/login')
		return method === 'POST' ? signIn(here, body) : refuse(405, 'POST');
	const route = (method === 'POST' ? POST : GET).find((each) => each.path === path);
	if (!here.account) return refuse(401, 'not signed in');
	if (!route) return refuse(404, 'no such endpoint');
	if (route.admin && here.account.role !== 'admin') return refuse(403, 'admins only');
	if (route.admin && !mayRewrite(here) && path === '/api/runs/start' && body.mode === 'apply') {
		// The service downgrades silently; the page reads may_rewrite first.
		body = { ...body, mode: 'report' };
	}
	return route.handler(here, query, body);
}
