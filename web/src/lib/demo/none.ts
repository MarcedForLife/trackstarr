// What `$demo` resolves to outside the demo build: every hook absent, typed off
// ./hooks so the two cannot drift. This file imports nothing, which is the
// point: the normal build carries none of the demo.

import type * as hooks from './hooks';

export const fetcher: typeof hooks.fetcher | null = null;
export const source: typeof hooks.source | null = null;
export const cover: typeof hooks.cover | null = null;
export const notice: typeof hooks.notice | null = null;
export const loginHint: typeof hooks.loginHint | '' = '';
