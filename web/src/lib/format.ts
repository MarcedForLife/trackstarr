// The spellings the whole app shares: bytes, seconds, a track, the last part of
// a path, a moment.

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

// Channel counts as the names AUDIO_LAYOUTS is written in, so a track and the
// setting that made it read alike. A table rather than the inverse of
// rules.channelsOf: 6 channels could be 5.1 or 6.0.
const LAYOUTS: Record<number, string> = {
	1: '1.0',
	2: '2.0',
	3: '2.1',
	6: '5.1',
	7: '6.1',
	8: '7.1'
};

/** A channel count as a layout name: 6 is "5.1". An unlisted count is itself. */
export function layout(channels: number | undefined): string {
	if (!channels) return '';
	return LAYOUTS[channels] ?? `${channels}ch`;
}

/** A bitrate as a person reads it: 640k, 3.4 Mbps. */
export function rate(bitrate: number | undefined): string {
	if (!bitrate) return '';
	if (bitrate >= 1_000_000) return `${(bitrate / 1_000_000).toFixed(1)} Mbps`;
	// A text subtitle runs at tens of bits a second, which rounded to "0k".
	if (bitrate < 1000) return `${bitrate} bps`;
	return `${Math.round(bitrate / 1000)}k`;
}

/** What a track of this rate takes over a running time, in bytes. Zero without
 * both, since neither a rate nor a runtime alone says anything. */
export function bytesFor(bitrate: number | undefined, seconds: number | undefined): number {
	if (!bitrate || !seconds) return 0;
	return Math.round((bitrate / 8) * seconds);
}

// Nothing tags a video stream, so its row says nothing rather than `und`, which
// reads as a tag to go and fix and is the one thing here nobody can set.
const LANGUAGE_KINDS = ['audio', 'subtitle'];

/** Whether the rules read a language on this kind of track. */
export function carriesLanguage(kind: string): boolean {
	return LANGUAGE_KINDS.includes(kind);
}

/** A track on one line: codec, layout, language. The same shape for current and
 * planned tracks so the two columns compare. */
export function describe(track: {
	kind: string;
	codec?: string;
	channels?: number;
	lang?: string;
}): string {
	const parts = [track.codec?.toUpperCase()];
	if (track.kind === 'audio') parts.push(layout(track.channels));
	if (carriesLanguage(track.kind)) parts.push(track.lang ?? 'und');
	return parts.filter(Boolean).join(' · ');
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
// A media extension, not any dot: these names reach here as folders too, and
// "Mr. Robot (2015)" cut at its last dot reads "Mr".
const EXTENSION = /\.[a-z0-9]{2,4}$/i;
// What the *arrs hang off a title's own folder: "Mindhunter (2017) {tvdb-328708}".
const PROVIDER_ID = /(?: \{[^{}]*\})+$/;

/** A file as a truncating line names it: what it is, and which one. */
export type Named = { name: string; episode: string };

/**
 * A release file name as a person says it.
 *
 * The *arrs name files `<title> (<year>) - <episode> [tags...].<ext>`, and a
 * history line on a phone cut every episode of one series to the same title.
 * The episode comes back separately so a caller can keep it out of the
 * truncation; an episode drops its year, a film keeps it.
 *
 * A title's own folder comes through here too, where a hold is placed on one,
 * so the provider's id goes the way the tags do.
 */
export function named(path: string | undefined): Named {
	const base = basename(path);
	// The space keeps a title opening on a bracket, like [REC] (2007), whole.
	const tags = base.indexOf(' [');
	const whole = (tags > 0 ? base.slice(0, tags) : base.replace(EXTENSION, ''))
		.replace(PROVIDER_ID, '')
		.trim();
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
