// Titles and files nobody wants rewritten yet. $lib/runs is the switch for the
// whole service; this is the one for a single title.

import { request } from '$lib/api';

// One thing left alone, and until when. `seconds` counts down from when the
// snapshot was read; both it and `until` are null for a pause with no end.
export type Pause = {
	path: string;
	seconds: number | null;
	until: string | null;
	by: string;
	reason: string;
	at: string;
	// The library title it was placed on, and that title's name. Empty for a
	// pause placed on one file off a run.
	title: string;
	name: string;
};

// Optional, and defaulted at every call: a body without the key is an answer
// the sheet can still draw, where reading `.find` off nothing takes the sheet
// down with it.
type Pauses = { pauses?: Pause[] };

/**
 * How long a pause can be placed for: a film, an evening, or until somebody
 * says otherwise.
 */
export const SPANS: { seconds: number; label: string }[] = [
	{ seconds: 3600, label: '1 hour' },
	{ seconds: 3 * 3600, label: '3 hours' },
	{ seconds: 8 * 3600, label: 'Tonight' },
	{ seconds: 0, label: 'Until I resume' }
];

export async function getPauses(fetcher: typeof fetch = fetch): Promise<Pause[]> {
	return (await request<Pauses>('/api/pauses', undefined, fetcher)).pauses ?? [];
}

/** Pause titles by id or files by path. Answers with every pause now standing. */
export async function place(
	what: { ids?: string[]; paths?: string[] },
	seconds: number,
	reason = ''
): Promise<Pause[]> {
	const answer = await request<Pauses>('/api/pauses', {
		method: 'POST',
		body: JSON.stringify({ ...what, seconds, reason })
	});
	return answer.pauses ?? [];
}

export async function resume(what: { ids?: string[]; paths?: string[] }): Promise<Pause[]> {
	const answer = await request<Pauses>('/api/pauses/resume', {
		method: 'POST',
		body: JSON.stringify(what)
	});
	return answer.pauses ?? [];
}

export function forTitle(pauses: Pause[], id: string): Pause | undefined {
	return pauses.find((pause) => pause.title === id);
}
