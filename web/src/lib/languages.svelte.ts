// The service's names for ISO 639-2/B codes. Only the settings snapshot carries
// them and they never change, so one read serves the tab.

import { getSettings } from '$lib/settings';

let names = $state<Record<string, string> | null>(null);
let reading = false;

/** The names, or null until the first read lands. Reactive. */
export function languageNames(): Record<string, string> | null {
	return names;
}

/** Ask for the names unless they are here or on the way. A failed read leaves
 * an empty map, so the codes stand in until a later call tries again. */
export function readLanguageNames(): void {
	if (reading || (names && Object.keys(names).length)) return;
	reading = true;
	getSettings()
		.then((snapshot) => (names = snapshot.languages))
		.catch(() => (names ??= {}))
		.finally(() => (reading = false));
}
