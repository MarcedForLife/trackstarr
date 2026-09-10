// The auth API, and the one door the rest goes through. The session is an
// HttpOnly cookie; the JSON content type is the server's CSRF (cross-site
// request forgery) check.

import { invalidateAll } from '$app/navigation';
import { shut } from '$lib/stream';

export type Account = { name: string; role: 'admin' | 'viewer'; must_change: boolean };

// What a refusal carried. `status` is the service's own words for why, worth
// showing as written; `run` names the run a control lost out to; `problems` is
// the settings validator's list.
export type Refusal = { status?: string; run?: string; problems?: string[] };

export class ApiError extends Error {
	constructor(
		public status: number,
		public answer: Refusal = {}
	) {
		super(`the service answered ${status}`);
	}
}

const jsonHeaders = { 'Content-Type': 'application/json' };

// One redirect however many pollers notice the session has gone at once.
let leaving = false;

function expired() {
	if (leaving) return;
	leaving = true;
	// Or the stream retries against a dead session for as long as the tab lives.
	shut();
	// The root layout guard re-reads the session and redirects to /login.
	invalidateAll().finally(() => (leaving = false));
}

/** Why the service said no, in its own words where it gave any. */
export function refusalText(error: unknown): string {
	const said = error instanceof ApiError ? error.answer.status : '';
	return said ? said[0].toUpperCase() + said.slice(1) + '.' : 'The service refused that.';
}

async function refusalOf(response: Response): Promise<Refusal> {
	if (!(response.headers.get('content-type') ?? '').includes('json')) return {};
	const body = await response.json().catch(() => null);
	return body && typeof body === 'object' ? body : {};
}

/**
 * One request: its answer parsed, its refusal thrown.
 *
 * `fetcher` is SvelteKit's inside a load, where the layout guard handles a 401
 * itself, and the window's elsewhere, where a 401 means the session expired
 * under an open tab.
 */
export async function request<T>(
	path: string,
	init?: RequestInit,
	fetcher: typeof fetch = fetch
): Promise<T> {
	const response = await fetcher(path, {
		...init,
		headers: init?.body ? { ...jsonHeaders, ...init.headers } : init?.headers
	});
	if (response.ok) return response.json();
	// A 401 from the auth endpoints is a wrong password, not an expiry.
	const mine = fetcher === globalThis.fetch && !path.startsWith('/api/auth/');
	if (response.status === 401 && mine) expired();
	throw new ApiError(response.status, await refusalOf(response));
}

export async function me(fetcher: typeof fetch = fetch): Promise<Account | null> {
	try {
		return await request<Account>('/api/auth/me', undefined, fetcher);
	} catch (error) {
		// Only a 401 means nobody is signed in; a 502 must not bounce a reader
		// to /login.
		if (error instanceof ApiError && error.status === 401) return null;
		throw error;
	}
}

export function login(username: string, password: string): Promise<Account> {
	return request<Account>('/api/auth/login', {
		method: 'POST',
		body: JSON.stringify({ username, password })
	});
}

export async function logout(): Promise<void> {
	// Refused or unreachable, the sign-out carries on to /login either way.
	await request('/api/auth/logout', { method: 'POST', body: '{}' }).catch(() => {});
}

export function changePassword(current: string, replacement: string): Promise<Account> {
	return request<Account>('/api/auth/password', {
		method: 'POST',
		body: JSON.stringify({ current, new: replacement })
	});
}
