import { getEvents, LOOKBACK, type Event } from '$lib/events';
import { getSummary, type Summary } from '$lib/library';
import { order } from '$lib/order.svelte';
import { getActivity } from '$lib/runs';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	// The live half decides whether the page is worth showing at all. The other
	// two answer "nothing yet" for a service still being set up, so a missing
	// events.jsonl or an unlistable *arr costs a section, not the overview.
	const [activity, recent, library] = await Promise.all([
		getActivity(fetch),
		getEvents(fetch, LOOKBACK).then(
			(history): Event[] => history.events,
			(): Event[] => []
		),
		getSummary(fetch, order.strip).then(
			(found): Summary | null => found,
			(): Summary | null => null
		)
	]);
	return { activity, recent, library };
};
