import { getEvents } from '$lib/events';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	return { history: await getEvents(fetch) };
};
