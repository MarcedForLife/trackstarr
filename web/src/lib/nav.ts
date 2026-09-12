// The three primary destinations, drawn by both the desktop rail and the phone's
// tab bar. Settings stays behind the drawer rather than in the thumb zone.

import { base } from '$app/paths';
import type { Pathname } from '$app/types';

// `Pathname`, so a renamed route fails the build rather than the tap.
export type Destination = {
	href: Pathname;
	label: string;
	icon: 'overview' | 'library' | 'events';
};

export const PRIMARY: Destination[] = [
	{ href: '/', label: 'Overview', icon: 'overview' },
	{ href: '/library', label: 'Library', icon: 'library' },
	{ href: '/events', label: 'Events', icon: 'events' }
];

// The pages behind the gear. With PRIMARY, every route the layout prefetches.
export const SETTINGS: Pathname[] = [
	'/settings',
	'/settings/rules',
	'/settings/sweep',
	'/settings/connections',
	'/settings/appearance',
	'/settings/account'
];

// Shared by the mobile header, desktop heading and browser title.
export const PAGE_TITLES: Partial<Record<Pathname, string>> = {
	'/': 'Overview',
	'/library': 'Library',
	'/events': 'Events',
	'/settings': 'General',
	'/settings/rules': 'Rules',
	'/settings/sweep': 'Sweep',
	'/settings/connections': 'Connections',
	'/settings/appearance': 'Appearance',
	'/settings/account': 'Account'
};

/** A pathname as the routes above spell it, with the deployment's base path
 * off the front. `page.url.pathname` carries the base, and the demo build sits
 * under one. */
export function routeOf(pathname: string): string {
	return pathname.slice(base.length) || '/';
}
