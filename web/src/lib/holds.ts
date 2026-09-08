// Titles and files nobody wants rewritten yet. $lib/runs is the switch for the
// whole service; this is the one for a single title.

import { request } from '$lib/api';

// One thing left alone, and until when. `seconds` counts down from when the
// snapshot was read; both it and `until` are null for a hold with no end.
export type Hold = {
	path: string;
	seconds: number | null;
	until: string | null;
	by: string;
	reason: string;
	at: string;
	// The library title it was placed on, and that title's name. Empty for a
	// hold placed on one file off a run.
	title: string;
	name: string;
};

type Held = { holds: Hold[] };

/**
 * How long a hold can be placed for: a film, an evening, or until somebody
 * says otherwise. Three, so the row still has room for a way out of it.
 * Nothing shorter than a film: a rewrite takes longer than that, so the hold
 * would lapse under the encode it was meant to stop.
 */
export const SPANS: { seconds: number; label: string }[] = [
	{ seconds: 3 * 3600, label: '3 hours' },
	{ seconds: 8 * 3600, label: 'Tonight' },
	{ seconds: 0, label: 'Until I lift it' }
];

export async function getHolds(fetcher: typeof fetch = fetch): Promise<Hold[]> {
	return (await request<Held>('/api/holds', undefined, fetcher)).holds;
}

/** Hold titles by id or files by path. Answers with every hold now standing. */
export async function place(
	what: { ids?: string[]; paths?: string[] },
	seconds: number,
	reason = ''
): Promise<Hold[]> {
	const answer = await request<Held>('/api/holds', {
		method: 'POST',
		body: JSON.stringify({ ...what, seconds, reason })
	});
	return answer.holds;
}

export async function lift(what: { ids?: string[]; paths?: string[] }): Promise<Hold[]> {
	const answer = await request<Held>('/api/holds/lift', {
		method: 'POST',
		body: JSON.stringify(what)
	});
	return answer.holds;
}

export function forTitle(holds: Hold[], id: string): Hold | undefined {
	return holds.find((hold) => hold.title === id);
}
