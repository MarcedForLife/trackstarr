import { beforeEach, expect, test, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn(() => Promise.resolve()) }));
vi.mock('$lib/stream', () => ({ shut: vi.fn() }));

let api: typeof import('$lib/api');
let navigation: typeof import('$app/navigation');
let stream: typeof import('$lib/stream');
let answers: Response[];
let asked: [string, RequestInit | undefined][];

/** What the service answers with: JSON either way, refusal or not. */
function said(status: number, body: unknown = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

beforeEach(async () => {
	answers = [];
	asked = [];
	globalThis.fetch = ((path: string, init?: RequestInit) => {
		asked.push([path, init]);
		return Promise.resolve(answers.shift() ?? said(200));
	}) as typeof fetch;
	// The module latches its one redirect for the life of the tab, so each test
	// gets its own copy rather than whatever the last one left behind.
	vi.resetModules();
	vi.clearAllMocks();
	api = await import('$lib/api');
	navigation = await import('$app/navigation');
	stream = await import('$lib/stream');
});

test('a body is sent as JSON, which is the service CSRF check', async () => {
	await api.request('/api/runs/stop', { method: 'POST', body: '{}' });
	expect(asked[0][1]?.headers).toEqual({ 'Content-Type': 'application/json' });
});

test('a session that has gone shuts the stream and re-reads it once', async () => {
	answers = [said(401), said(401), said(401)];
	// Three pollers noticing together is the ordinary shape of it: one wave, one
	// redirect. Three would race the layout guard against itself.
	await Promise.allSettled([
		api.request('/api/runs'),
		api.request('/api/library'),
		api.request('/api/events')
	]);
	expect(stream.shut).toHaveBeenCalledTimes(1);
	expect(navigation.invalidateAll).toHaveBeenCalledTimes(1);
});

test('a 401 from the auth endpoints is answered there, not here', async () => {
	answers = [said(401)];
	// A wrong password, or nobody signed in. Neither is a session expiring.
	expect(await api.me()).toBeNull();
	expect(navigation.invalidateAll).not.toHaveBeenCalled();
});

test("a 401 through a load's fetcher is the layout guard's, not ours", async () => {
	const fetcher = (() => Promise.resolve(said(401))) as typeof fetch;
	// The guard is re-reading the session on the same navigation and redirects
	// by itself.
	await expect(api.request('/api/runs', undefined, fetcher)).rejects.toThrow();
	expect(navigation.invalidateAll).not.toHaveBeenCalled();
});

test('me() is null only for nobody signed in, and throws for a service that is down', async () => {
	answers = [said(502, { status: 'bad gateway' })];
	// A proxy's 502 used to read as a signed-out reader: bounced to /login,
	// signed in, bounced again with nothing said about why.
	await expect(api.me()).rejects.toBeInstanceOf(api.ApiError);
});

test('a refusal is shown in the words the service used', async () => {
	answers = [said(409, { status: 'a sweep is already running' })];
	const refused = await api.request('/api/runs/start').catch((error) => error);
	expect(api.refusalText(refused)).toBe('A sweep is already running.');
	expect(api.refusalText(new Error('the network went'))).toBe('The service refused that.');
});
