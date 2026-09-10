import { getShelf } from '$lib/library';
import { getActivity } from '$lib/runs';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	// Together, not one after the other. The shelf is a few hundred kilobytes and
	// the activity is a line, but the run controls need the second to know
	// whether this install may rewrite at all.
	const [shelf, activity] = await Promise.all([getShelf(fetch), getActivity(fetch)]);
	return { shelf, activity };
};
