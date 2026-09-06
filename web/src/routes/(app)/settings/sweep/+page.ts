import { getRatings, getSummary } from '$lib/library';
import { getSettings } from '$lib/settings';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	// The library for one number, how many verdicts are stored, and the scores
	// for the ratings section. Null rather than fatal: the page is still worth
	// showing when the *arrs cannot be reached.
	const [snapshot, library, ratings] = await Promise.all([
		getSettings(fetch),
		getSummary(fetch).catch(() => null),
		getRatings(fetch).catch(() => null)
	]);
	return { snapshot, library, ratings };
};
