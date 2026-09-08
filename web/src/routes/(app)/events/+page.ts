import { getEvents } from '$lib/events';
import { getActivity } from '$lib/runs';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	// The snapshot is for the sheet a poster opens, whose buttons must know
	// whether a run may start before the first look. Alongside the history:
	// neither should wait on the other.
	const [history, activity] = await Promise.all([getEvents(fetch), getActivity(fetch)]);
	return { history, activity };
};
