// The demo build's service, in the browser. $lib/api sends every request here
// instead of over the network, $lib/stream opens ./stream instead of an
// EventSource, and $lib/library draws covers from ./posters. Nothing persists:
// a reload is a fresh install with the same sample library.

import { answer } from './routes';

// A short wait before each answer, so loading states show as they would on a
// LAN rather than flashing.
const LATENCY_MS = 60;
const LATENCY_SPREAD_MS = 90;

function pause(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

/** One request, answered as `fetch` would answer it from the service. */
export async function demoFetch(path: string, init?: RequestInit): Promise<Response> {
	const url = new URL(path, 'http://demo.invalid');
	const method = (init?.method ?? 'GET').toUpperCase();
	let body: Record<string, unknown> = {};
	if (init?.body) {
		try {
			const parsed = JSON.parse(String(init.body));
			if (parsed && typeof parsed === 'object') body = parsed;
		} catch {
			return new Response(JSON.stringify({ status: 'bad JSON' }), {
				status: 400,
				headers: { 'content-type': 'application/json' }
			});
		}
	}
	await pause(LATENCY_MS + Math.random() * LATENCY_SPREAD_MS);
	const { status, body: answered } = await answer(method, url.pathname, url.searchParams, body);
	return new Response(JSON.stringify(answered), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}
