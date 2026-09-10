// The demo's service held to the same shape the fixtures are, and to itself:
// its shelf tallies to its summary, its history has words for every line, and
// its runs move the library the way the service's would.

import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { catalogue } from '$lib/demo/catalogue';
import { nextRuns } from '$lib/demo/cron';
import manifest from '$lib/demo/posters.json';
import { answer } from '$lib/demo/routes';
import { stopTicking, tick } from '$lib/demo/runs';
import { reset, world } from '$lib/demo/world';
import { chips, detail, details, headline, verdicts as counted, type EventPage } from '$lib/events';
import {
	asVerdict,
	FILTERS,
	pip,
	type Shelf,
	type Summary,
	type TitleDetail,
	type TitleLink
} from '$lib/library';
import { FLOW } from '$lib/order.svelte';
import type { Activity, Run } from '$lib/runs';
import type { SettingsSnapshot } from '$lib/settings';
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
	const here = world();
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

	test('leads every card with a word the grid draws, and shows every one', async () => {
		const shelf = await get<Shelf>('/api/library');
		const states = new Set(shelf.titles.map((card) => card.state));
		for (const card of shelf.titles) {
			expect(asVerdict(card.state)).toBe(card.state);
			expect(pip).toHaveProperty(card.state);
			for (const word of Object.keys(card.counts ?? {})) expect(asVerdict(word)).toBe(word);
		}
		for (const state of [
			'pending',
			'skip',
			'unsupported',
			'conform',
			'mixed',
			'unchecked',
			'missing'
		]) {
			expect([...states], state).toContain(state);
		}
		// Nothing in the sample library has failed: a demo that opens on a broken
		// rewrite looks unhealthy, so that chip alone stays empty.
		for (const state of FILTERS.filter((state) => state !== 'failed'))
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
			chips(entry);
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
		expect(activity.runs.map((run) => run.kind).sort()).toEqual(['import', 'sweep']);
		const sweep = activity.runs.find((run) => run.kind === 'sweep')!;
		expect(sweep.active[0].stage).toBe('encoding');
		expect(sweep.upcoming?.length).toBeGreaterThan(2);
		expect(sweep.recent?.map((row) => row.status).sort()).toEqual(['deferred', 'modified']);
		expect(sweep.done + sweep.active.length + (sweep.queued ?? 0)).toBe(sweep.total);
		expect(activity.rewrites).toBe(1);
		expect(activity.may_rewrite).toBe(true);
		expect(activity.next_sweep).not.toBeNull();
		expect(activity.holds).toHaveLength(1);
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
		expect(modified?.downmixed).toEqual(['2.0']);
		expect(page.events.indexOf(modified!)).toBeLessThan(page.events.indexOf(before));
		const activity = await get<Activity>('/api/runs');
		const sweep = activity.runs.find((run) => run.kind === 'sweep')!;
		expect(sweep.counts.modified).toBeGreaterThanOrEqual(2);
	});

	test('runs the whole sweep down and closes it with a summary', async () => {
		await get<Activity>('/api/runs');
		advance(6000);
		const activity = await get<Activity>('/api/runs');
		expect(activity.runs).toEqual([]);
		// Only the held film and the one a download client still holds are left
		// owing a rewrite.
		const shelf = await get<Shelf>('/api/library');
		expect(shelf.titles.filter((card) => card.state === 'pending').map((card) => card.id)).toEqual([
			'arr:radarr:21',
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
		const sweep = paused.runs.find((run) => run.kind === 'sweep')!;
		await post('/api/runs/stop', { run: sweep.id });
		advance(400);
		const activity = await get<Activity>('/api/runs');
		expect(activity.runs.find((run) => run.kind === 'sweep')).toBeUndefined();
		const page = await get<EventPage>('/api/events?limit=5');
		const summary = page.events.find((entry) => entry.event === 'sweep')!;
		expect(summary.stopped).toBeGreaterThan(0);
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

	test('holds a title and lifts it', async () => {
		const held = await post<{ holds: { title: string; seconds: number | null }[] }>('/api/holds', {
			ids: ['arr:radarr:25'],
			seconds: 3 * 3600,
			reason: 'watching it'
		});
		expect(held.holds.find((hold) => hold.title === 'arr:radarr:25')?.seconds).toBe(3 * 3600);
		const lifted = await post<{ holds: { title: string }[] }>('/api/holds/lift', {
			ids: ['arr:radarr:25']
		});
		expect(lifted.holds.some((hold) => hold.title === 'arr:radarr:25')).toBe(false);
	});

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
		expect(nextRuns('nonsense', from, 1)).toBeNull();
		expect(nextRuns('0 4 * *', from, 1)).toBeNull();
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
