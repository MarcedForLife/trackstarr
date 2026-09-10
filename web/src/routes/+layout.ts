// SPA mode: the backend is a stdlib Python server that only hands out these
// files.
export const ssr = false;
export const prerender = false;

import { redirect } from '@sveltejs/kit';
import { me } from '$lib/api';
import type { LayoutLoad } from './$types';

// Every route learns who is signed in; anonymous visitors only ever see
// /login, and a session still owing a password change only /password.
export const load: LayoutLoad = async ({ url, fetch }) => {
	const user = await me(fetch);
	if (!user) {
		if (url.pathname !== '/login') redirect(307, '/login');
	} else if (user.must_change && url.pathname !== '/password') {
		redirect(307, '/password');
	} else if (!user.must_change && url.pathname === '/login') {
		redirect(307, '/');
	}
	return { user };
};
