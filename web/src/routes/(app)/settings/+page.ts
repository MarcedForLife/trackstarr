import { getSettings } from '$lib/settings';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	return { snapshot: await getSettings(fetch) };
};
