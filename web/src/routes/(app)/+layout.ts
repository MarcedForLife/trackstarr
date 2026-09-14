import type { Pathname } from '$app/types';
import { PAGE_TITLES, routeOf } from '$lib/nav';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = ({ url }) => ({
	pageTitle:
		PAGE_TITLES[(routeOf(url.pathname).replace(/\/$/, '') || '/') as Pathname] ?? 'Trackstarr'
});
