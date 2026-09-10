// SPA mode: the backend is a stdlib Python server that only hands out these
// files.
export const ssr = false;
export const prerender = false;

import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { me } from '$lib/api';
import { routeOf } from '$lib/nav';
import type { LayoutLoad } from './$types';

// Every route learns who is signed in; anonymous visitors only ever see
// /login, and a session still owing a password change only /password.
export const load: LayoutLoad = async ({ url, fetch }) => {
	const user = await me(fetch);
	const route = routeOf(url.pathname);
	if (!user) {
		if (route !== '/login') redirect(307, resolve('/login'));
	} else if (user.must_change && route !== '/password') {
		redirect(307, resolve('/password'));
	} else if (!user.must_change && route === '/login') {
		redirect(307, resolve('/'));
	}
	return { user };
};
