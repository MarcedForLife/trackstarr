// The last word on an unhandled error. The only hook: `ssr = false`, so every
// load runs in the browser. SvelteKit's "Internal Error" under a 500 is wrong
// for a service that never answered, so each kind says which it was.

import type { HandleClientError } from '@sveltejs/kit';
import { ApiError, refusalText } from '$lib/api';

export const handleError: HandleClientError = ({ error, status }) => {
	// A path the router does not know, which with an SPA fallback is every typo.
	if (status === 404) return { message: 'No such page.' };

	// Nothing came back. A failed fetch is a TypeError with engine-specific words,
	// so the type is all that is known.
	if (error instanceof TypeError) {
		return { heading: 'No answer', message: 'Could not reach the service.' };
	}

	// It answered unusably. Its own status, not SvelteKit's 500: a 502 and a 503
	// are different waits.
	if (error instanceof ApiError) {
		return {
			heading: String(error.status),
			message: error.answer.status
				? refusalText(error)
				: 'The service could not answer for this page.'
		};
	}

	// Anything else is this page's own doing. Logged, since unlike the other two
	// there is no request left to look at.
	console.error(error);
	return { message: 'Something in this page went wrong.' };
};
