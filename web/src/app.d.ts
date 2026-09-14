// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	// Baked in by vite.config.ts, from the service's own __version__.
	const __SERVICE_VERSION__: string;

	namespace App {
		// What the error page is told. See hooks.client.ts.
		interface Error {
			message: string;
			// The heading where the status is not the answer: a fetch that never
			// reached the service has none of its own.
			heading?: string;
		}
		// interface Locals {}
		// interface PageData {}
		// What is over the page. Every overlay gets a shallow-routing history
		// entry, so back puts it away rather than leaving the page.
		interface PageState {
			queue?: boolean;
			// The title sheet.
			sheet?: boolean;
			// Picking titles on the library, and the bar that goes with it.
			picking?: boolean;
			// The nav drawer, which only exists below lg.
			menu?: boolean;
			// A worker's log, opened from the file it belongs to.
			log?: boolean;
		}
		// interface Platform {}
	}
}

export {};
