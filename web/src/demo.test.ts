// The demo's service checked against the same shape the fixtures are, and to itself:
// its shelf tallies to its summary, its history has words for every line, and
// its runs move the library the way the service's would.

import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { catalogue } from '$lib/demo/catalogue';
import { nextRun, nextRuns } from '$lib/demo/cron';
import manifest from '$lib/demo/posters.json';
import { answer } from '$lib/demo/routes';
import { queued, reorder, simulateImport, stopTicking, tick, undoQueue } from '$lib/demo/runs';
import { currentState, pausedTitle, reset } from '$lib/demo/state';
import {
	detail,
	details,
	headline,
	layouts,
	measures,
	notes,
	verdicts as counted,
	type EventPage
} from '$lib/events';
import {
	asVerdict,
	FILTERS,
	pip,
	type Shelf,
	type Summary,
	type TitleDetail,
	type TitleLink
} from '$lib/library';
import type { ConnectionResult } from '$lib/connections';
import { FLOW } from '$lib/order.svelte';
import type { QueuePage } from '$lib/queue';
import type { Outcome } from '$lib/retag';
import type { Activity, Run } from '$lib/runs';
import type { SettingsSnapshot } from '$lib/settings';
import type { ScheduleCheck } from '$lib/sweep';
import { sift, tally } from '$lib/shelfview';

type Wire<T> = T extends Activity ? Omit<T, 'runs'> & { runs: Omit<Run, 'seen'>[] } : T;

async function call<T>(method: string, path: string, body: object = {}): Promise<Wire<T>> {
	const url = new URL(path, 'http://demo.invalid');
	const { status, body: answered } = await answer(
		method,
		url.pathname,
		url.searchParams,
		body as Record<string, unknown>
	);
	expect(status, JSON.stringify(answered)).toBe(200);
	return answered as Wire<T>;
}

const get = <T>(path: string) => call<T>('GET', path);
const post = <T>(path: string, body: object = {}) => call<T>('POST', path, body);

async function refusal(method: string, path: string, body: object = {}): Promise<number> {
	const url = new URL(path, 'http://demo.invalid');
	return (await answer(method, url.pathname, url.searchParams, body as Record<string, unknown>))
		.status;
}

beforeEach(reset);
afterEach(stopTicking);

/** Move the simulation on by `seconds`, a tick at a time. */
function advance(seconds: number) {
	const here = currentState();
	let now = Date.now();
	tick(here, now);
	for (let passed = 0; passed < seconds; passed += 5) {
		now += 5000;
		tick(here, now);
	}
}

describe('the library', () => {
	test('counts the shelf to the summary it serves', async () => {
		const shelf = await get<Shelf>('/api/library');
		const summary = await get<Summary>('/api/library/summary?sort=processed');
		expect(tally(shelf.titles)).toEqual(summary.counts);
		expect(shelf.titles).toHaveLength(summary.titles);
		const view = {
			filters: [],
			hidden: [],
			kind: '',
			needle: '',
			sort: 'processed' as const,
			flow: FLOW.processed
		};
		const ordered = sift(shelf.titles, view).map((card) => card.id);
		expect(ordered.slice(0, summary.head.length)).toEqual(summary.head.map((card) => card.id));
	});

	test('leads every card with a word the grid draws', async () => {
		const shelf = await get<Shelf>('/api/library');
		const states = new Set(shelf.titles.map((card) => card.state));
		for (const card of shelf.titles) {
			expect(asVerdict(card.state)).toBe(card.state);
			expect(pip).toHaveProperty(card.state);
			for (const word of Object.keys(card.counts ?? {})) expect(asVerdict(word)).toBe(word);
		}
		for (const state of ['pending', 'skip', 'unsupported', 'conform', 'mixed', 'missing']) {
			expect([...states], state).toContain(state);
		}
		// Nothing in the sample library has failed: a demo that opens on a broken
		// rewrite looks unhealthy. The delivered film has already been checked too.
		for (const state of FILTERS.filter((state) => state !== 'failed' && state !== 'unchecked'))
			expect(tally(shelf.titles)[state], state).toBeGreaterThan(0);
	});

	test('plans a pending file and records a rewritten one', async () => {
		const sintel = await get<TitleDetail>('/api/library/title?id=arr:radarr:2');
		expect(sintel.files[0].status).toBe('pending');
		expect(sintel.files[0].planned.some((track) => track.flags?.includes('generated'))).toBe(true);
		expect(sintel.files[0].why.reasons?.join(' ')).toContain('drop subtitle');
		const elephants = await get<TitleDetail>('/api/library/title?id=arr:radarr:4');
		expect(elephants.files[0].status).toBe('conform');
		expect(elephants.files[0].modified?.added).toEqual([2]);
		expect(await refusal('GET', '/api/library/title?id=nothing')).toBe(404);
	});

	test('leaves a file whose every audio track would go, and never opens an unsupported one', async () => {
		const potemkin = await get<TitleDetail>('/api/library/title?id=arr:radarr:31');
		expect(potemkin.files[0].status).toBe('skip');
		const plan9 = await get<TitleDetail>('/api/library/title?id=arr:radarr:28');
		expect(plan9.files[0].status).toBe('unsupported');
		expect(plan9.files[0].tracks).toEqual([]);
	});
});

describe('the history', () => {
	test('has words for every line, and counts every verdict a summary counts', async () => {
		const page = await get<EventPage>('/api/events?limit=500');
		expect(page.events.length).toBeGreaterThan(40);
		expect(page.next).toBeNull();
		for (const entry of page.events) {
			expect(headline(entry), entry.event).not.toBe(entry.event);
			detail(entry);
			notes(entry);
			layouts(entry);
			measures(entry);
			expect(details(entry).length).toBeGreaterThan(0);
			if ('counts' in entry) expect(counted(entry)).toHaveLength(Object.keys(entry.counts!).length);
			if (entry.title) expect(page.titles).toHaveProperty(entry.title);
		}
		const stamps = page.events.map((entry) => entry.ts);
		expect(stamps).toEqual([...stamps].sort().reverse());
	});

	test('pages back without repeating a line', async () => {
		const first = await get<EventPage>('/api/events?limit=10');
		expect(first.events).toHaveLength(10);
		expect(first.next).not.toBeNull();
		const second = await get<EventPage>(`/api/events?limit=10&before=${first.next}`);
		const seen = new Set(first.events.map((entry) => entry.ts + entry.event));
		for (const entry of second.events) expect(seen.has(entry.ts + entry.event)).toBe(false);
		expect(second.events[0].ts <= first.events[9].ts).toBe(true);
	});

	test('narrows to a span', async () => {
		const since = new Date(Date.now() - 3600_000).toISOString();
		const page = await get<EventPage>(`/api/events?limit=100&since=${since}`);
		expect(page.events.length).toBeGreaterThan(0);
		for (const entry of page.events) expect(entry.ts >= since).toBe(true);
	});
});

describe('the runs', () => {
	test('opens mid-sweep with an import waiting on the slot', async () => {
		const activity = await get<Activity>('/api/runs');
		expect(activity.runs.map((run) => run.type).sort()).toEqual(['import', 'sweep']);
		const sweep = activity.runs.find((run) => run.type === 'sweep')!;
		expect(sweep.active[0].stage).toBe('encoding');
		expect(sweep.upcoming?.length).toBeGreaterThan(2);
		expect(sweep.recent?.map((row) => row.status).sort()).toEqual(['deferred', 'modified']);
		expect(sweep.done + sweep.active.length + (sweep.queued ?? 0)).toBe(sweep.total);
		expect(activity.rewrites).toBe(1);
		expect(activity.may_rewrite).toBe(true);
		expect(activity.next_sweep).not.toBeNull();
		// Nothing held, since a pause is a rare thing to meet on the opening board.
		expect(activity.pauses).toEqual([]);
	});

	test('the queue carries the last check behind a row, and says when rules passed it', async () => {
		const page = await get<QueuePage>('/api/queue');
		expect(page.plans_current).toBe(true);
		const judged = page.items.filter((item) => page.plans?.[item.path]);
		expect(judged.length).toBeGreaterThan(0);
		// The seeded import was checked even while the sweep occupied the slot.
		expect(judged.length).toBe(page.items.length);
		expect(page.items[0].path).toContain('Coffee Run');
		await post('/api/settings', { LANGUAGES: ['original', 'eng', 'ger:keep'] });
		const stale = await get<QueuePage>('/api/queue');
		expect(stale.plans_current).toBe(false);
		expect(stale.plans?.[judged[0].path]).toBeDefined();
	});

	test('finishes the encode, rewrites the file and records it', async () => {
		await get<Activity>('/api/runs');
		const before = (await get<EventPage>('/api/events?limit=1')).events[0];
		advance(400);
		const sintel = await get<TitleDetail>('/api/library/title?id=arr:radarr:2');
		expect(sintel.files[0].status).toBe('conform');
		expect(sintel.files[0].modified?.dropped).toEqual([3, 4, 5]);
		expect(sintel.files[0].tracks.some((track) => track.title === 'Stereo')).toBe(true);
		const page = await get<EventPage>('/api/events?limit=10');
		const modified = page.events.find(
			(entry) => entry.event === 'modified' && entry.path === sintel.files[0].path
		);
		// The same word the plan chips use: history and plans name a layout alike.
		expect(modified?.adds).toEqual(['Stereo']);
		expect(modified?.drops).toBe(3);
		expect(page.events.indexOf(modified!)).toBeLessThan(
			page.events.findIndex((entry) => entry.ts === before.ts && entry.event === before.event)
		);
		const activity = await get<Activity>('/api/runs');
		const sweep = activity.runs.find((run) => run.type === 'sweep')!;
		expect(sweep.counts.modified).toBeGreaterThanOrEqual(2);
	});

	test('runs the whole sweep down and closes it with a summary', async () => {
		await get<Activity>('/api/runs');
		advance(6000);
		const activity = await get<Activity>('/api/runs');
		expect(activity.runs).toEqual([]);
		// Only the one a download client still holds is left owing a rewrite.
		const shelf = await get<Shelf>('/api/library');
		expect(shelf.titles.filter((card) => card.state === 'pending').map((card) => card.id)).toEqual([
			'arr:radarr:27'
		]);
		const page = await get<EventPage>('/api/events?limit=30');
		const summary = page.events.find((entry) => entry.event === 'sweep')!;
		expect(summary.dry_run).toBe(false);
		expect(summary.counts?.modified).toBeGreaterThan(5);
	});

	test('pauses, refuses a second walk, and stops after the file', async () => {
		const paused = await post<Activity>('/api/runs/pause');
		expect(paused.paused).toBe(true);
		expect(await refusal('POST', '/api/runs/start', { mode: 'apply' })).toBe(409);
		const sweep = paused.runs.find((run) => run.type === 'sweep')!;
		await post('/api/runs/stop', { run: sweep.id });
		advance(400);
		const activity = await get<Activity>('/api/runs');
		expect(activity.runs.find((run) => run.type === 'sweep')).toBeUndefined();
		const page = await get<EventPage>('/api/events?limit=5');
		const summary = page.events.find((entry) => entry.event === 'sweep')!;
		expect(summary.stopped).toBeGreaterThan(0);
		expect(summary.files! + summary.stopped!).toBe(sweep.total);
		expect(summary.files).toBeLessThan(sweep.total);
		expect(page.events.some((entry) => entry.event === 'paused')).toBe(true);
	});

	test('a rules change drops the cache and a report sweep judges afresh', async () => {
		await post('/api/runs/abort');
		const saved = await post<SettingsSnapshot>('/api/settings', {
			LANGUAGES: ['original', 'eng', 'ger:keep']
		});
		expect(saved.settings.LANGUAGES.value).toEqual(['original', 'eng', 'ger:keep']);
		expect((await get<Shelf>('/api/library')).current).toBe(false);
		const started = await post<{ run: string }>('/api/runs/start', { mode: 'report' });
		expect(started.run).toBeTruthy();
		advance(200);
		const shelf = await get<Shelf>('/api/library');
		expect(shelf.current).toBe(true);
		// Sintel keeps its German subtitle now, so its plan shrinks but stays.
		const sintel = await get<TitleDetail>('/api/library/title?id=arr:radarr:2');
		expect(sintel.files[0].why.reasons?.some((reason) => reason.includes('ger'))).toBe(false);
		expect(sintel.files[0].status).toBe('pending');
	});

	test("refuses a bad save with the validator's problems", async () => {
		const url = new URL('/api/settings', 'http://demo.invalid');
		const { status, body } = await answer('POST', url.pathname, url.searchParams, {
			AUDIO_LAYOUTS: ['2.0', '2.0:remove']
		});
		expect(status).toBe(400);
		expect((body as { problems: string[] }).problems[0]).toContain('twice');
	});
});

describe('the sheet', () => {
	test('tags an untagged surround track and the downmix rule finds its source', async () => {
		const before = await get<TitleDetail>('/api/library/title?id=arr:radarr:30');
		expect(before.files[0].status).toBe('conform');
		const outcome = await post<{ results: { status: string; verdict: string }[] }>(
			'/api/library/retag',
			{
				tracks: [{ path: before.files[0].path, index: 1 }],
				lang: 'eng'
			}
		);
		expect(outcome.results[0]).toMatchObject({ status: 'retagged', verdict: 'pending' });
		const after = await get<TitleDetail>('/api/library/title?id=arr:radarr:30');
		expect(after.files[0].why.reasons?.[0]).toContain('add 2.0 downmix');
		const page = await get<EventPage>('/api/events?limit=1');
		expect(page.events[0].event).toBe('retagged');
	});

	test('refuses to edit an MP4 in place', async () => {
		const cosmos = await get<TitleDetail>('/api/library/title?id=arr:radarr:5');
		const outcome = await post<{ results: { status: string }[] }>('/api/library/retag', {
			tracks: [{ path: cosmos.files[0].path, index: 1 }],
			lang: 'fre'
		});
		expect(outcome.results[0].status).toBe('refused');
	});

	test('pauses a title and resumes it', async () => {
		const paused = await post<{ pauses: { title_id: string; seconds: number | null }[] }>(
			'/api/pauses',
			{
				ids: ['arr:radarr:25'],
				seconds: 3 * 3600,
				reason: 'watching it'
			}
		);
		expect(paused.pauses.find((pause) => pause.title_id === 'arr:radarr:25')?.seconds).toBe(
			3 * 3600
		);
		const resumed = await post<{ pauses: { title_id: string }[] }>('/api/pauses/resume', {
			ids: ['arr:radarr:25']
		});
		expect(resumed.pauses.some((pause) => pause.title_id === 'arr:radarr:25')).toBe(false);
	});

	test('pauses a single file by path and resumes it without pausing its siblings', async () => {
		const title = currentState().titles.find((title) => title.files.length > 1)!;
		const path = title.files[0].path;
		const paused = await post<{ pauses: { path: string; title: string; seconds: number }[] }>(
			'/api/pauses',
			{
				paths: [path],
				seconds: 3600
			}
		);
		expect(paused.pauses.find((pause) => pause.path === path)).toMatchObject({
			title_id: '',
			seconds: 3600
		});
		expect(pausedTitle(currentState(), title, path)).toBeDefined();
		expect(pausedTitle(currentState(), title, title.files[1].path)).toBeUndefined();
		const resumed = await post<{ pauses: { path: string }[] }>('/api/pauses/resume', {
			paths: [path]
		});
		expect(resumed.pauses.some((pause) => pause.path === path)).toBe(false);
	});

	test.each(['report', 'apply'])('starts a title %s while a sweep is running', async (mode) => {
		const before = await get<Activity>('/api/runs');
		const sweep = before.runs.find((run) => run.type === 'sweep')!;
		expect(sweep).toBeDefined();
		const started = await post<{ run: string }>('/api/library/run', {
			ids: ['arr:radarr:3'],
			mode
		});
		const after = await get<Activity>('/api/runs');
		expect(after.runs.some((run) => run.id === sweep.id)).toBe(true);
		expect(after.runs.find((run) => run.id === started.run)?.dry_run).toBe(mode === 'report');
	});

	test.each(['report', 'apply'])(
		'a passed title %s finishes while rewrite slots are busy',
		async (mode) => {
			const here = currentState();
			const title = here.titles.find(
				(title) => title.files.length === 1 && title.files[0].status === 'conform'
			)!;
			const started = await post<{ run: string }>('/api/library/run', {
				ids: [title.spec.id],
				mode
			});
			// Long enough for the probe: a re-check opens its files rather than
			// judging them where it lists them.
			advance(10);
			expect(here.runs.some((run) => run.type === 'sweep')).toBe(true);
			expect(here.runs.some((run) => run.id === started.run)).toBe(false);
			const page = await get<EventPage>('/api/events?limit=20');
			expect(
				page.events.find((entry) => entry.run === started.run && entry.event === 'recheck')?.counts
			).toEqual({ conform: 1 });
			expect(title.files[0].status).toBe('conform');
		}
	);

	test('re-checks a title on its own', async () => {
		await post('/api/runs/abort');
		const started = await post<{ run: string; titles: number }>('/api/library/run', {
			ids: ['arr:radarr:3'],
			mode: 'apply'
		});
		expect(started.titles).toBe(1);
		advance(150);
		const tears = await get<TitleDetail>('/api/library/title?id=arr:radarr:3');
		expect(tears.files[0].status).toBe('conform');
		expect(tears.files[0].modified).toBeDefined();
		const page = await get<EventPage>('/api/events?limit=5');
		expect(page.events.some((entry) => entry.event === 'recheck')).toBe(true);
	});

	test('offers the *arr and IMDb pages with the title, and again among its links', async () => {
		const sintel = await get<TitleDetail>('/api/library/title?id=arr:radarr:2');
		const { links } = await get<{ links: TitleLink[] }>('/api/library/links?id=arr:radarr:2');
		const known = sintel.servers.filter((server) => server.url);
		expect(known.map((server) => server.server)).toEqual(['radarr', 'imdb']);
		expect(links.map((link) => link.server)).toEqual(['plex', 'radarr', 'imdb']);
		expect(links.slice(1)).toEqual(known);
		expect(links[1].url).toBe('http://localhost:7878/movie/sintel');
		expect(links[2].url).toBe('https://www.imdb.com/title/tt1727587/');
		// A viewer's click on a server that does not exist stays on their machine.
		expect(links[0].url.startsWith('http://localhost:')).toBe(true);

		const holmes = await get<{ links: TitleLink[] }>('/api/library/links?id=arr:sonarr:40');
		expect(holmes.links.find((link) => link.server === 'sonarr')?.url).toBe(
			'http://localhost:8989/series/sherlock-holmes'
		);
	});
});

describe('signing in', () => {
	test('any name gets in, and the viewer role is read-only', async () => {
		await post('/api/auth/logout');
		expect(await refusal('GET', '/api/auth/me')).toBe(401);
		expect(await refusal('GET', '/api/library')).toBe(401);
		const viewer = await post<{ role: string }>('/api/auth/login', {
			username: 'viewer',
			password: 'x'
		});
		expect(viewer.role).toBe('viewer');
		expect(await refusal('POST', '/api/runs/pause')).toBe(403);
		await get<Shelf>('/api/library');
		const admin = await post<{ role: string }>('/api/auth/login', {
			username: 'anyone',
			password: 'x'
		});
		expect(admin.role).toBe('admin');
	});
});

describe('the schedule', () => {
	test('reads a cron expression back as moments', () => {
		const from = new Date(2026, 8, 10, 12, 0, 0);
		const nightly = nextRuns('0 3 * * *', from, 2)!;
		expect(nightly.map((at) => at.getHours())).toEqual([3, 3]);
		expect(nightly[1].getTime() - nightly[0].getTime()).toBe(86_400_000);
		expect(nextRuns('*/15 * * * *', from, 2)!.map((at) => at.getMinutes())).toEqual([15, 30]);
		expect(nextRuns('0 4 * * 0', from, 1)![0].getDay()).toBe(0);
	});

	// The sweep page shows these, so they are the service's own wording.
	test('refuses a schedule the way the service refuses it', () => {
		const from = new Date(2026, 8, 10, 12, 0, 0);
		expect(() => nextRuns('0 4 * *', from, 1)).toThrow(
			'a schedule is 5 fields: minute hour day-of-month month day-of-week'
		);
		expect(() => nextRuns('nonsense * * * *', from, 1)).toThrow(
			"minute has a non-number 'nonsense'"
		);
		expect(() => nextRuns('0 99 * * *', from, 1)).toThrow("hour '99' is outside 0-23");
		expect(() => nextRuns('0 3 30 2 *', from, 1)).toThrow('the schedule never matches a real date');
		expect(nextRun('', from)).toBeNull();
		expect(nextRun('nonsense', from)).toBeNull();
	});
});

// The settings pages show these, and a demo that words them its own way is a
// demo of an app that does not exist.
describe('the refusals', () => {
	test('answers a connection test as the service does', async () => {
		// Jellyfin is the one the demo leaves unconfigured, address first.
		expect(
			await post<ConnectionResult>('/api/connections/test', { service: 'jellyfin' })
		).toMatchObject({
			ok: false,
			detail: 'No address set, so Jellyfin is switched off.',
			hint: ''
		});
		expect(
			await post<ConnectionResult>('/api/connections/test', {
				service: 'jellyfin',
				url: 'http://jellyfin:8096'
			})
		).toMatchObject({ ok: false, detail: 'No API key set, so Jellyfin is switched off.' });
		expect(
			await post<ConnectionResult>('/api/connections/test', {
				service: 'radarr',
				url: 'radarr:7878',
				key: 'k'
			})
		).toMatchObject({ ok: false, detail: 'The address has to start with http:// or https://.' });
		expect(await refusal('POST', '/api/connections/test', { service: 'emby' })).toBe(404);
	});

	test('names an *arr and counts a media server the way each answers', async () => {
		const radarr = await post<ConnectionResult>('/api/connections/test', { service: 'radarr' });
		expect(radarr).toMatchObject({ ok: true, detail: 'Radarr 5.14.0.9383', webhook: 'connected' });
		expect(radarr.paths).toBe('ready');
		expect(radarr.hint).toContain('/data/media/movies:');
		expect(radarr.hint).toContain('inside MEDIA_DIRS');
		const plex = await post<ConnectionResult>('/api/connections/test', { service: 'plex' });
		expect(plex).toMatchObject({ ok: true, detail: 'Plex, 2 libraries on Yggdrasil', webhook: '' });
	});

	test('checks a sweep path the way the service checks it', async () => {
		const checked = await post<ScheduleCheck>('/api/sweep/check', {
			at: '0 4 * * *',
			dirs: ['/data:/x', '/nowhere', '/data/media/movies']
		});
		expect(checked.dirs.map((dir) => [dir.state, dir.detail])).toEqual([
			['invalid', 'A colon separates the entries, so a path cannot contain one.'],
			['missing', 'Nothing is mounted there yet, so nothing would be swept.'],
			['ok', '']
		]);
		expect(checked.error).toBe('');
		const wrong = await post<ScheduleCheck>('/api/sweep/check', { at: '0 99 * * *', dirs: [] });
		expect(wrong).toMatchObject({ ok: false, error: "hour '99' is outside 0-23" });
		// An unset schedule is not a refusal: the sweep is started by hand.
		expect(await post<ScheduleCheck>('/api/sweep/check', { at: '', dirs: [] })).toMatchObject({
			ok: true,
			runs: [],
			error: ''
		});
	});

	test('refuses an edit in place the way mkvtag refuses it', async () => {
		const sherlock = await get<TitleDetail>('/api/library/title?id=arr:radarr:27');
		const answer = await post<{ results: Outcome[] }>('/api/library/retag', {
			tracks: [{ path: sherlock.files[0].path, index: 1 }],
			lang: 'eng'
		});
		expect(answer.results[0]).toMatchObject({
			status: 'refused',
			detail:
				"hardlinked: an edit in place would change the download client's copy too; " +
				'a rewrite makes a new file instead'
		});
	});
});

describe('the posters', () => {
	test('names only titles in the catalogue, under licences the demo may show', () => {
		const ids = new Set(catalogue().map((title) => title.id));
		for (const [id, poster] of Object.entries(manifest)) {
			expect(ids.has(id), id).toBe(true);
			expect(poster.file.startsWith('File:'), id).toBe(true);
			expect(poster.by.length, id).toBeGreaterThan(0);
			expect(['CC BY 3.0', 'CC BY 4.0', 'CC BY-SA 3.0', 'CC0', 'Public domain'], id).toContain(
				poster.licence
			);
		}
	});
});

describe('demo webhook tools', () => {
	test('poses imports-only work from both arrs and permits a sweep alongside it', async () => {
		await post('/api/demo/scenario', { name: 'imports-only' });
		let activity = await get<Activity>('/api/runs');
		expect(activity.runs.map((run) => run.type)).toEqual(['import', 'import']);
		expect(activity.paused).toBe(false);
		advance(5);
		activity = await get<Activity>('/api/runs');
		expect(activity.runs.some((run) => run.active.length > 0)).toBe(true);
		await post('/api/runs/start', { mode: 'report' });
		activity = await get<Activity>('/api/runs');
		expect(activity.runs.map((run) => run.type).sort()).toEqual(['import', 'import', 'sweep']);
		// By run, since the seeded history has webhooks of its own.
		const posed = new Set(
			activity.runs.filter((run) => run.type === 'import').map((run) => run.id)
		);
		const history = await get<EventPage>('/api/events');
		const delivered = history.events
			.filter((event) => event.event === 'webhook' && posed.has(event.run as string))
			.map((event) => event.arr)
			.sort();
		expect(delivered).toEqual(['radarr', 'sonarr']);
	});

	test('puts the opening board back, signed in as before', async () => {
		const opening = await get<Activity>('/api/runs');
		await post('/api/runs/abort');
		await post('/api/demo/import', { arr: 'sonarr' });
		// Still an admin, or the route refuses; the name is what proves it contents.
		currentState().account = { name: 'someone', role: 'admin', must_change: false };
		await post('/api/demo/scenario', { name: 'full' });
		const again = await get<Activity>('/api/runs');
		expect(again.runs.map((run) => run.type).sort()).toEqual(
			opening.runs.map((run) => run.type).sort()
		);
		expect(again.rewrites).toBe(opening.rewrites);
		expect(currentState().account?.name).toBe('someone');
	});

	test('breaks a rewrite, which is the one verdict the sample library never reaches', async () => {
		const before = await get<Shelf>('/api/library');
		expect(tally(before.titles).failed ?? 0).toBe(0);
		await post('/api/demo/scenario', { name: 'failed' });
		const shelf = await get<Shelf>('/api/library');
		expect(tally(shelf.titles).failed).toBeGreaterThan(0);
		const history = await get<EventPage>('/api/events');
		const broke = history.events.find((event) => event.event === 'failed')!;
		expect(broke.detail).toContain('ffmpeg exited 1');
		expect(headline(broke)).not.toBe('failed');
		// A later sweep leaves it failed: the work it broke on is still there.
		advance(600);
		expect(tally((await get<Shelf>('/api/library')).titles).failed).toBeGreaterThan(0);
	});

	test('holds everything, with the files in flight back in the queue', async () => {
		const going = await get<Activity>('/api/runs');
		const waiting = going.runs.reduce((sum, run) => sum + (run.queued ?? 0), 0);
		await post('/api/demo/scenario', { name: 'held' });
		const held = await get<Activity>('/api/runs');
		expect(held.paused).toBe(true);
		expect(held.runs.flatMap((run) => run.active)).toEqual([]);
		expect(held.runs.reduce((sum, run) => sum + (run.queued ?? 0), 0)).toBeGreaterThan(waiting);
		// Held means held: nothing picks the queue up again while it is paused.
		advance(600);
		const later = await get<Activity>('/api/runs');
		expect(later.runs.flatMap((run) => run.active)).toEqual([]);
	});

	test('refuses a scenario it has no board for', async () => {
		expect(await refusal('POST', '/api/demo/scenario', { name: 'nothing' })).toBe(400);
	});

	test('adds distinct episode deliveries without replacing existing work', async () => {
		const before = await get<Activity>('/api/runs');
		await post('/api/demo/import', { arr: 'sonarr' });
		await post('/api/demo/import', { arr: 'sonarr' });
		const after = await get<Activity>('/api/runs');
		expect(after.runs.length).toBe(before.runs.length + 2);
		expect(new Set(after.runs.map((run) => run.id)).size).toBe(after.runs.length);
		expect(after.runs.slice(-2).every((run) => run.total > 1)).toBe(true);
	});

	test('replaces a conforming release with a 4K file and generates its missing stereo', async () => {
		await get('/api/runs');
		const title = currentState().titles.find(
			(title) => title.spec.sources[0].instance_id === 'radarr'
		)!;
		const original = title.files[0].path;
		// A clear board, so the delivery lands on the first sample rather than
		// whichever one the seeded runs left free.
		await post('/api/runs/abort');
		await post('/api/demo/import', { arr: 'radarr' });
		const file = title.files[0];
		expect(file.name).toContain('2160p');
		expect(currentState().byPath.has(original)).toBe(false);
		expect(currentState().byPath.get(file.path)).toBe(file);
		expect(file.contents.find((track) => track.kind === 'video')?.codec).toBe('hevc');
		expect(
			file.contents.filter((track) => track.kind === 'audio').map((track) => track.channels)
		).toEqual([6]);
		advance(3600);
		expect(file.modified).toBeDefined();
		expect(file.status).toBe('conform');
		expect(file.contents.some((track) => track.kind === 'audio' && track.channels === 2)).toBe(
			true
		);
		await post('/api/runs/abort');
		await post('/api/demo/import', { arr: 'radarr' });
		expect(file.contents.some((track) => track.channels === 2)).toBe(false);
	});

	test('honours report-only mode for deliveries', async () => {
		await get('/api/runs');
		currentState().settings.REWRITE_MODE = 'report';
		await post('/api/runs/abort');
		await post('/api/demo/import', { arr: 'radarr' });
		const activity = await get<Activity>('/api/runs');
		expect(activity.runs[0].dry_run).toBe(true);
	});
});

describe('import priority', () => {
	test.each(['none', 'reordered', 'undone'])(
		'checks imports beside a sweep and respects %s order',
		async (curation) => {
			await get<Activity>('/api/runs');
			stopTicking();
			const state = currentState();
			const sweep = state.runs.find((run) => run.type === 'sweep')!;
			const active = sweep.active[0];
			const chosen = queued(state).find((item) => item.run === sweep.id)!;
			if (curation !== 'none') {
				const moved = reorder(state, [chosen]);
				if (curation === 'undone') expect(undoQueue(state, moved.undo)).toBe(true);
			}
			const now = Date.now();
			const delivery = simulateImport(state, 'radarr', now);
			if (!('run' in delivery)) throw new Error('delivery refused');
			const run = state.runs.find((run) => run.id === delivery.run)!;
			const path = run.queue[0].path;
			tick(state, now);
			expect(run.active[0].path).toBe(path);
			tick(state, now + 2500);
			expect(state.byPath.get(path)?.status).toBe('pending');
			expect(run.done).toBe(0);
			expect(run.active).toHaveLength(0);
			expect(sweep.active[0]).toBe(active);
			expect(queued(state)[0].path).toBe(curation === 'reordered' ? chosen.path : path);
		}
	);

	test('settles a conforming import while the sweep holds the rewrite slot', async () => {
		await get<Activity>('/api/runs');
		stopTicking();
		const state = currentState();
		const now = Date.now();
		const delivery = simulateImport(state, 'radarr', now);
		if (!('run' in delivery)) throw new Error('delivery refused');
		const run = state.runs.find((run) => run.id === delivery.run)!;
		const file = state.byPath.get(run.queue[0].path)!;
		file.contents = state.titles
			.flatMap((title) => title.files)
			.find((file) => file.status === 'conform' && file.lang === 'eng')!.tracks;
		tick(state, now);
		tick(state, now + 2500);
		expect(file.status).toBe('conform');
		expect(run.done).toBe(1);
		expect(run.counts.conform).toBe(1);
		expect(queued(state).some((item) => item.run === run.id)).toBe(false);
		expect(state.runs.find((run) => run.type === 'sweep')?.active[0].stage).toBe('encoding');
	});
});

test.each(['report', 'apply'])('runs only the selected variant in %s mode', async (mode) => {
	await post('/api/demo/scenario', { name: 'multiple-variants' });
	const state = currentState();
	const title = state.titles.find(
		(title) => title.spec.kind === 'movie' && title.spec.sources.length > 1
	)!;
	const target = title.files.find((file) => file.path.includes('/media/4k/'))!;
	const siblings = title.files.filter((file) => file !== target);
	const before = siblings.map((file) => ({
		status: file.status,
		bytes: file.bytes,
		tracks: structuredClone(file.tracks)
	}));
	const started = await post<{ run: string }>('/api/library/run', { paths: [target.path], mode });
	const run = state.runs.find((run) => run.id === started.run)!;
	expect(run.total).toBe(1);
	expect(run.label).toBe(target.name);
	expect(run.dry_run).toBe(mode === 'report');
	advance(300);
	expect(state.runs.some((run) => run.id === started.run)).toBe(false);
	expect(target.status).toBe(mode === 'report' ? 'pending' : 'conform');
	expect(
		siblings.map((file) => ({ status: file.status, bytes: file.bytes, tracks: file.tracks }))
	).toEqual(before);
});

test('multiple variants are optional and make one title with a folder per instance', async () => {
	const opening = await get<Shelf>('/api/library');
	expect(opening.titles.some((title) => title.source_count)).toBe(false);
	await post('/api/demo/scenario', { name: 'multiple-variants' });
	const shelf = await get<Shelf>('/api/library');
	const held = shelf.titles.filter((title) => title.source_count);
	expect(held.map((title) => title.source_count)).toEqual([2, 2]);
	expect(shelf.titles).toHaveLength(opening.titles.length);
	for (const card of held) {
		const title = await get<TitleDetail>(`/api/library/title?id=${encodeURIComponent(card.id)}`);
		const label = card.kind === 'movie' ? 'Radarr' : 'Sonarr';
		expect(title.folders.map((folder) => folder.source)).toEqual([label, `${label} 4k`]);
		expect(title.folders[1].folder).toContain('/media/4k/');
		const upgrades = title.files.filter((file) => file.path.includes('/media/4k/'));
		expect(upgrades.length).toBeGreaterThan(0);
		expect(upgrades.every((file) => file.source === `${label} 4k`)).toBe(true);
		expect(title.files.every((file) => ['conform', 'pending'].includes(file.status))).toBe(true);
		expect(title.files.every((file) => file.tracks.some((track) => track.kind === 'audio'))).toBe(
			true
		);
		expect(upgrades.every((file) => file.status === 'pending')).toBe(true);
		for (const file of upgrades) {
			expect(file.name).toContain('2160p HEVC');
			expect(file.tracks.find((track) => track.kind === 'video')).toMatchObject({
				codec: 'hevc',
				bitrate: 24_000_000
			});
			expect(file.tracks.find((track) => track.kind === 'audio')).toMatchObject({
				codec: 'eac3',
				channels: 6
			});
			expect(file.bytes).toBeGreaterThan(
				Math.min(
					...title.files
						.filter((original) => !original.path.includes('/media/4k/'))
						.map((original) => original.bytes)
				)
			);
		}
		expect(
			title.files
				.filter((file) => !file.path.includes('/media/4k/'))
				.every((file) => file.source === label)
		).toBe(true);
		// A title pause holds every folder; resuming lets both go.
		const paused = await post<{ pauses: { title_id: string; path: string }[] }>('/api/pauses', {
			ids: [card.id],
			seconds: 3600
		});
		expect(
			paused.pauses.filter((pause) => pause.title_id === card.id).map((pause) => pause.path)
		).toEqual(title.folders.map((folder) => folder.folder));
		const resumed = await post<{ pauses: { title_id: string }[] }>('/api/pauses/resume', {
			ids: [card.id]
		});
		expect(resumed.pauses.some((pause) => pause.title_id === card.id)).toBe(false);
		const links = await get<{ links: TitleLink[] }>(
			`/api/library/links?id=${encodeURIComponent(card.id)}`
		);
		expect(
			links.links
				.filter(
					(link) => link.server === card.kind.replace('movie', 'radarr').replace('series', 'sonarr')
				)
				.map((link) => link.label)
		).toEqual([label, `${label} 4k`]);
		expect(
			links.links.some((link) => link.url.includes(card.kind === 'movie' ? ':7879/' : ':8990/'))
		).toBe(true);
	}
	const snapshot = await get<SettingsSnapshot>('/api/settings');
	expect(
		snapshot.arr_instances.find((instance) => instance.id === 'radarr-4k')!.fields.api_key
	).toEqual({ value: '', env: false, set: true, env_name: 'RADARR_4K_API_KEY' });
	const checked = await post<ConnectionResult>('/api/connections/test', { service: 'radarr-4k' });
	expect(checked.ok).toBe(true);
	expect(checked.hint).toContain('/data/media/4k/movies:');
	await post('/api/demo/scenario', { name: 'full' });
	expect((await get<Shelf>('/api/library')).titles).toHaveLength(opening.titles.length);
});

test('large series pages whole episodes and every scenario resets its predecessor', async () => {
	const opening = await get<Shelf>('/api/library');
	await post('/api/demo/scenario', { name: 'multiple-variants' });
	await post('/api/demo/scenario', { name: 'large-series' });
	const shelf = await get<Shelf>('/api/library');
	expect(shelf.titles.some((title) => title.source_count)).toBe(false);
	const id = currentState().titles.find((title) => title.files.length === 1000)!.spec.id;
	const first = await get<TitleDetail>(`/api/library/title?id=${encodeURIComponent(id)}`);
	const second = await get<TitleDetail>(`/api/library/title?id=${encodeURIComponent(id)}&pages=2`);
	expect(first.total).toBe(1000);
	expect(first.files).toHaveLength(200);
	expect(second.files).toHaveLength(400);
	expect(second.files.slice(0, 200)).toEqual(first.files);
	await post('/api/demo/scenario', { name: 'large-series' });
	expect(currentState().titles.find((title) => title.spec.id === id)!.files).toHaveLength(1000);
	await post('/api/demo/scenario', { name: 'connection-trouble' });
	expect(
		(await get<TitleDetail>(`/api/library/title?id=${encodeURIComponent(id)}`)).total
	).toBeLessThan(200);
	const radarr = await post<ConnectionResult>('/api/connections/test', { service: 'radarr' });
	const sonarr = await post<ConnectionResult>('/api/connections/test', { service: 'sonarr' });
	expect(radarr.ok).toBe(false);
	expect(sonarr.webhook).toBe('unreachable');
	expect(sonarr.paths).toBe('attention');
	await post('/api/demo/scenario', { name: 'imports-only' });
	expect((await post<ConnectionResult>('/api/connections/test', { service: 'radarr' })).ok).toBe(
		true
	);
	await post('/api/demo/scenario', { name: 'full' });
	expect((await get<Shelf>('/api/library')).titles.map((title) => title.id)).toEqual(
		opening.titles.map((title) => title.id)
	);
});

test('structured connection lifecycle keeps identity and credentials through rename and removal', async () => {
	const id = 'radarr-new';
	let shot = await post<SettingsSnapshot>('/api/settings', {
		arr_instances: [
			{ id, create: true, values: { url: 'http://new', api_key: 'private', name: 'Public' } }
		]
	});
	expect(shot.settings).not.toHaveProperty('RADARR_NEW_URL');
	expect(JSON.stringify(shot)).not.toContain('private');
	shot = await post<SettingsSnapshot>('/api/settings', {
		arr_instances: [{ id, values: { name: 'Films', api_key: '' } }]
	});
	expect(shot.arr_instances.find((instance) => instance.id === id)!.fields).toMatchObject({
		name: { value: 'Films' },
		api_key: { set: true, value: '' }
	});
	expect(await refusal('POST', '/api/settings', { arr_instances: [{ id, create: true }] })).toBe(
		400
	);
	shot = await post<SettingsSnapshot>('/api/settings', {
		arr_instances: [{ id, values: { api_key: null } }]
	});
	expect(shot.arr_instances.find((instance) => instance.id === id)!.fields.api_key.set).toBe(false);
	shot = await post<SettingsSnapshot>('/api/settings', { arr_instances: [{ id, remove: true }] });
	expect(shot.arr_instances.some((instance) => instance.id === id)).toBe(false);
	expect(
		await refusal('POST', '/api/settings', { arr_instances: [{ id, values: { name: 'Late' } }] })
	).toBe(400);
});
