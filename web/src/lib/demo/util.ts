// Small shared pieces of the demo: stamps, ids and the clock arithmetic the
// world and its history both do.

/** The version the demo answers with, where the service says its own. */
export const VERSION = '0.1.0-demo';

export const MINUTE_MS = 60_000;
export const HOUR_MS = 60 * MINUTE_MS;
export const DAY_MS = 24 * HOUR_MS;

// How long before the demo opened its two runs began: the sweep somebody
// started by hand, and the import Radarr delivered into it.
export const SWEEP_STARTED_AGO_MS = 25 * MINUTE_MS;
export const IMPORT_STARTED_AGO_MS = 4 * MINUTE_MS;

/** A moment as every `ts` on the wire spells it. */
export function stamp(ms: number): string {
	return new Date(ms).toISOString();
}

/** A short hex digest of any text, for config ids and run ids. */
export function digest(text: string, length = 12): string {
	let a = 0x811c9dc5;
	let b = 0x1000193;
	for (const char of text) {
		a = Math.imul(a ^ char.codePointAt(0)!, 16777619) >>> 0;
		b = Math.imul(b + a, 2654435761) >>> 0;
	}
	return (a.toString(16).padStart(8, '0') + b.toString(16).padStart(8, '0')).slice(0, length);
}

/** A run's id as the service mints one: when it started, and a few hex
 * characters. */
export function runId(startedMs: number): string {
	const started = stamp(startedMs);
	return `${started}#${digest(started, 4)}`;
}

export function slug(name: string): string {
	return name
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-|-$/g, '');
}

/** The most recent `hour:minute` before `now`, in the browser's clock. */
export function lastAt(now: number, hour: number, minute: number): number {
	const at = new Date(now);
	at.setHours(hour, minute, 0, 0);
	if (at.getTime() > now) at.setDate(at.getDate() - 1);
	return at.getTime();
}

/** `hour:minute` on the day `daysAgo` days before today. */
export function dayAt(now: number, daysAgo: number, hour: number, minute: number): number {
	const at = new Date(now);
	at.setDate(at.getDate() - daysAgo);
	at.setHours(hour, minute, 0, 0);
	return at.getTime();
}

/** A number between 0 and 1 that is always the same for the same text, where
 * the demo wants variety without randomness. */
export function jitter(text: string): number {
	return parseInt(digest(text, 6), 16) / 0xffffff;
}
