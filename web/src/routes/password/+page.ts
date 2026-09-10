import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import type { PageLoad } from './$types';

// Only the forced first-run change lives on this bare screen.
export const load: PageLoad = async ({ parent }) => {
	const { user } = await parent();
	if (user && !user.must_change) redirect(307, resolve('/settings/account'));
};
