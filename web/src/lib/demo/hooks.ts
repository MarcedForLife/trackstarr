// Where the demo build takes over from the service: the five places the pages
// reach for something only a service can give. `$demo` resolves here under
// `vite --mode demo` and to ./none.ts otherwise, so the normal build never
// imports the rest of this directory. See vite.config.ts.

import type { Component } from 'svelte';
import { demoFetch } from './index';
import { poster } from './posters';
import { demoSource } from './stream';

/** Answers a request in the browser, in place of `fetch`. */
export const fetcher: (path: string, init?: RequestInit) => Promise<Response> = demoFetch;

/** Opens the stand-in for the event stream, in place of an EventSource. */
export const source: () => import('./stream').Source = demoSource;

/** A title's poster from the build, in place of the cover endpoint. */
export const cover: (id: string) => string = poster;

/** The notice over every page, loaded on demand. */
export const notice: () => Promise<{ default: Component }> = () =>
	import('$lib/components/DemoNotice.svelte');

/** What the sign-in page says under its heading. */
export const loginHint =
	'This is the demo: any name and password sign you in. The name "viewer" gets the read-only role.';
