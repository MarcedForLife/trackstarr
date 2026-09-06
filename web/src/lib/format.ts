// The spellings the whole app shares: bytes, seconds, the last part of a path,
// a moment.

/** A count of bytes as a person reads it: 512 B, 1.4 GB, 20.0 TB. */
export function size(bytes: number): string {
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let value = Math.abs(bytes);
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	// A tenth of a GB is worth seeing; a tenth of a byte is not.
	return `${unit && value < 100 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** A count of seconds as a person reads it: 45s, 12m, 3h 40m. */
export function duration(seconds: number): string {
	if (seconds < 60) return `${Math.round(seconds)}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `${minutes}m`;
	return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

// Largest first, so 90000 is 25 hours rather than 1500 minutes.
const UNITS = [
	[86400, 'day'],
	[3600, 'hour'],
	[60, 'minute']
] as const;

/** Seconds read back in the unit they were set in: 7200 is "2 hours". Whole
 * units only; empty otherwise. */
export function wholeUnits(seconds: number): string {
	if (!Number.isInteger(seconds) || seconds <= 0) return '';
	const fit = UNITS.find(([size]) => seconds % size === 0);
	if (!fit) return '';
	const count = seconds / fit[0];
	return `${count} ${fit[1]}${count === 1 ? '' : 's'}`;
}

/** The last part of a path. */
export function basename(path: string | undefined): string {
	return (path ?? '').slice((path ?? '').lastIndexOf('/') + 1);
}

// Sonarr's season and episode, doubles included: S02E05, S01E01-E02. Anchored
// on the separator so a title of that shape is not mistaken for it.
const EPISODE = / - (S\d{1,3}E\d{1,3}(?:-E\d{1,3})*)(?: - |$)/;
// Only stripped where an episode follows, so a film keeps its year.
const YEAR = / \((?:19|20)\d\d\)$/;

/** A file as a truncating line names it: what it is, and which one. */
export type Named = { name: string; episode: string };

/**
 * A release file name as a person says it.
 *
 * The *arrs name files `<title> (<year>) - <episode> [tags...].<ext>`, and a
 * history line on a phone cut every episode of one series to the same title.
 * The episode comes back separately so a caller can keep it out of the
 * truncation; an episode drops its year, a film keeps it.
 */
export function named(path: string | undefined): Named {
	const base = basename(path);
	// The space keeps a title opening on a bracket, like [REC] (2007), whole.
	const tags = base.indexOf(' [');
	const dot = base.lastIndexOf('.');
	const whole = (tags > 0 ? base.slice(0, tags) : dot > 0 ? base.slice(0, dot) : base).trim();
	// Nothing but tags: the file name beats an empty line.
	if (!whole) return { name: base, episode: '' };
	const found = whole.match(EPISODE);
	if (!found) return { name: whole, episode: '' };
	return { name: whole.slice(0, found.index).replace(YEAR, ''), episode: found[1] };
}

/** Name and episode as one, for somewhere with room for both. */
export function titled(path: string | undefined): string {
	const { name, episode } = named(path);
	return episode ? `${name} ${episode}` : name;
}

/** A moment as date and time in the browser's clock, for the `title` on a
 * relative time. An unparseable stamp is returned as it came. */
export function stamp(ts: string): string {
	const at = new Date(ts);
	return isNaN(at.getTime()) ? ts : at.toLocaleString();
}

const DAY_MS = 86_400_000;

function startOfDay(at: Date): number {
	return new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
}

/** A moment ahead as a person says it: "at 4:00 am", "tomorrow at 4:00 am",
 * "Sunday at 4:00 am", or a date a week or more out. */
export function soon(ts: string): string {
	const at = new Date(ts);
	if (isNaN(at.getTime())) return ts;
	const time = at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
	const days = Math.round((startOfDay(at) - startOfDay(new Date())) / DAY_MS);
	if (days <= 0) return `at ${time}`;
	if (days === 1) return `tomorrow at ${time}`;
	if (days < 7) return `${at.toLocaleDateString(undefined, { weekday: 'long' })} at ${time}`;
	return `on ${at.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`;
}
