// The service's API, answered from the world: one row per path, the same
// access rules, the same refusals. What each handler returns is what the
// service would put on the wire.

import type { ConnectionResult } from '$lib/connections';
import type { EventPage } from '$lib/events';
import type { Sort } from '$lib/order.svelte';
import type { DirCheck, ScheduleCheck } from '$lib/sweep';
import { localStamp, nextRuns } from './cron';
import {
	abort,
	activity,
	mayRewrite,
	recheck,
	runLog,
	seed,
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
	card,
	clearVerdicts,
	detail,
	holdsNow,
	liftHolds,
	links,
	placeHolds,
	retag,
	saveSettings,
	settingsSnapshot,
	shelf,
	summary,
	world,
	type World
} from './world';

export type Answer = { status: number; body: unknown };

type Body = Record<string, unknown>;
type Handler = (world: World, query: URLSearchParams, body: Body) => Answer | Promise<Answer>;

// Who may call a route: anyone signed in, or only an admin.
type Route = { path: string; admin: boolean; handler: Handler };

const ok = (body: unknown): Answer => ({ status: 200, body });
const refuse = (status: number, text: string): Answer => ({ status, body: { status: text } });
const refused = (answer: Refused | object): answer is Refused =>
	'body' in answer && typeof (answer as Refused).status === 'number';

/** The world, seeded and ticking. */
function current(): World {
	const here = world();
	if (!here.seeded) {
		seed(here);
		here.seeded = true;
	}
	start(here);
	return here;
}

function busy(here: World): Answer | null {
	const going = here.runs.find((run) => run.kind !== 'import');
	return going
		? { status: 409, body: { status: 'a sweep is running. Stop it first', run: going.id } }
		: null;
}

function strings(value: unknown): string[] {
	return Array.isArray(value) ? value.map(String) : [];
}

/** Which titles a body names, by id or by the folder a hold names. */
function named(here: World, body: Body) {
	const ids = strings(body.ids);
	const paths = strings(body.paths);
	return here.titles.filter(
		(title) => ids.includes(title.spec.id) || paths.includes(title.spec.folder)
	);
}

function holdTargets(here: World, body: Body) {
	return [...named(here, body), ...strings(body.paths).filter((path) => here.byPath.has(path))];
}

function eventsPage(here: World, query: URLSearchParams): EventPage {
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

// What the connections page's Test button hears back, per service.
const CONNECTIONS: Record<string, ConnectionResult> = {
	radarr: { ok: true, detail: 'Radarr 5.14.0.9383, 28 films', hint: '', webhook: 'connected' },
	sonarr: { ok: true, detail: 'Sonarr 4.0.10.2544, 6 series', hint: '', webhook: 'connected' },
	plex: {
		ok: true,
		detail: 'Plex Media Server 1.41.0, 2 libraries',
		hint: '',
		webhook: ''
	},
	jellyfin: { ok: true, detail: 'Jellyfin 10.10.3, 2 libraries', hint: '', webhook: '' }
};

function testConnection(here: World, body: Body): ConnectionResult {
	const service = String(body.service ?? '');
	const url = String(body.url || here.settings[`${service.toUpperCase()}_URL`] || '');
	const canned = CONNECTIONS[service];
	if (!canned) return { ok: false, detail: 'no such service', hint: '', webhook: '' };
	if (!url)
		return { ok: false, detail: 'no URL set', hint: 'Fill in the address first.', webhook: '' };
	if (!/^https?:\/\/[^\s/]+/.test(url)) {
		return {
			ok: false,
			detail: `could not connect to ${url}`,
			hint: 'Check the address from inside the container.',
			webhook: ''
		};
	}
	if (service === 'plex' && !strings(here.settings.PLEX_PATH_MAP).length) {
		return { ...canned, hint: 'Map /data to /media in PLEX_PATH_MAP so Plex finds the rewrites.' };
	}
	return canned;
}

function checkSweep(here: World, body: Body): ScheduleCheck {
	const at = String(body.at ?? '');
	const zone = String(body.tz || here.settings.TZ || 'UTC');
	const runs = nextRuns(at, new Date(), 3);
	const mounted = strings(here.settings.MEDIA_DIRS);
	const dirs: DirCheck[] = strings(body.dirs).map((path) => {
		if (!path.startsWith('/')) return { path, state: 'invalid', detail: 'not an absolute path' };
		if (mounted.includes(path))
			return {
				path,
				state: 'ok',
				detail: `${here.titles.filter((title) => title.spec.folder.startsWith(path)).length} titles`
			};
		return { path, state: 'missing', detail: 'nothing is mounted here' };
	});
	return {
		ok: runs !== null,
		runs: (runs ?? []).map(localStamp),
		zone,
		error: runs === null ? `cannot read "${at}" as a schedule` : '',
		dirs
	};
}

function signIn(here: World, body: Body): Answer {
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
		path: '/api/holds',
		admin: false,
		handler: (here) => ok({ holds: holdsNow(here, Date.now()) })
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
		handler: async (here, _query, body) => (await pause(600), ok(testConnection(here, body)))
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
		path: '/api/holds',
		admin: true,
		handler: (here, _query, body) => {
			const titles = holdTargets(here, body);
			if (!titles.length) return refuse(404, 'no such title or file');
			placeHolds(
				here,
				titles,
				Number(body.seconds) || 0,
				String(body.reason ?? ''),
				here.account!.name
			);
			return ok({ holds: holdsNow(here, Date.now()) });
		}
	},
	{
		path: '/api/holds/lift',
		admin: true,
		handler: (here, _query, body) => {
			liftHolds(here, holdTargets(here, body), here.account!.name);
			return ok({ holds: holdsNow(here, Date.now()) });
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

function ratings(here: World) {
	return {
		scored: here.settings.IMDB_RATINGS
			? here.titles.filter((title) => title.spec.rating).length
			: 0,
		fetched: here.ratingsFetched
	};
}

/** What /api/status carries beside the version: the queue's numbers, without
 * the runs. */
function queueStatus(here: World) {
	const { queue, working, rewrites, parked, may_rewrite, next_sweep, holds } = activity(here);
	return { queue, working, rewrites, parked, may_rewrite, next_sweep, holds };
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
