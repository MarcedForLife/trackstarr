// Editing a track's tags in place: the language, and the flags the rules read.
// The service rewrites the Matroska header with mkvpropedit and judges the
// file again, so the sheet reloads to the verdict the tag change earns.

import { request } from '$lib/api';
import type { LibraryFile, Track } from '$lib/library';

// The flags on offer, per kind. The service reads each on one kind: a
// commentary 2.0 must not count as the stereo track, and forced and SDH are
// what the subtitle rules go by.
export type Flag = 'commentary' | 'forced' | 'sdh';

export type FlagOffer = { name: Flag; label: string; hint: string };

export const FLAGS: Record<string, FlagOffer[]> = {
	audio: [
		{
			name: 'commentary',
			label: 'Commentary',
			hint: 'Not the main mix. Never stands in for the stereo track, and the commentary rule may drop it.'
		}
	],
	subtitle: [
		{
			name: 'forced',
			label: 'Forced',
			hint: 'Shown even with subtitles off. The SDH rule never drops it.'
		},
		{
			name: 'sdh',
			label: 'SDH',
			hint: 'For the deaf and hard-of-hearing. The SDH rule drops it where the same language keeps a full subtitle.'
		}
	]
};

// What to set on every track named. A language left out is left alone; only
// the flags named move.
export type Edit = { lang?: string; flags?: Partial<Record<Flag, boolean>> };

// One track: the file it is in and its stream index there.
export type Target = { path: string; index: number };

// What became of one target. `verdict` is the file's status once judged again.
export type Outcome = {
	path: string;
	status: 'retagged' | 'unchanged' | 'refused' | 'failed';
	detail?: string;
	verdict?: string;
};

// The tag written for "no language", as the service spells it.
export const UNDEFINED = 'und';

/**
 * What a form set against what the track has: only what moved goes over the
 * wire, so a flag left alone stays as the file has it whatever the row's badge
 * read into it, and a flag toggled and toggled back is no edit at all.
 */
export function changes(track: Track, lang: string, flags: Partial<Record<Flag, boolean>>): Edit {
	const edit: Edit = {};
	if (lang !== (track.lang ?? UNDEFINED)) edit.lang = lang;
	const moved = (FLAGS[track.kind] ?? []).filter(
		(flag) => flags[flag.name] !== undefined && flags[flag.name] !== held(track, flag.name)
	);
	if (moved.length) {
		edit.flags = Object.fromEntries(moved.map((flag) => [flag.name, !!flags[flag.name]]));
	}
	return edit;
}

/** Whether the row reads the flag as on: the rules' reading, title included. */
export function held(track: Track, name: Flag): boolean {
	return (track.flags ?? []).includes(name);
}

export async function retagTracks(tracks: Target[], edit: Edit): Promise<Outcome[]> {
	const answer = await request<{ results?: Outcome[] }>('/api/library/retag', {
		method: 'POST',
		body: JSON.stringify({ tracks, ...edit })
	});
	return answer.results ?? [];
}

/** Whether the file can be edited in place at all: the service does Matroska
 * only, and says so itself for anything else that stops it. */
export function editable(file: { name: string }): boolean {
	return file.name.toLowerCase().endsWith('.mkv');
}

/** The tracks of one kind in a file, in order: what "the second audio track"
 * counts along. */
function ofKind(tracks: Track[], kind: string): Track[] {
	return tracks.filter((track) => track.kind === kind);
}

/**
 * Whether `other` is the same track in another file as `track` is in its own:
 * the same kind at the same place among that kind, with the same codec,
 * channels, language and title. What a season ripped alike has in every
 * episode, and what one edit is applied across.
 */
export function alike(track: Track, place: number, other: Track, otherPlace: number): boolean {
	return (
		track.kind === other.kind &&
		place === otherPlace &&
		track.codec === other.codec &&
		track.channels === other.channels &&
		track.lang === other.lang &&
		(track.title ?? '') === (other.title ?? '')
	);
}

/**
 * Every file in the title carrying a track like this one, the file it was
 * picked in first. Read off the tracks the sheet already has, which is the
 * file as last probed; the service checks each file has not changed since.
 */
export function matching(files: LibraryFile[], from: LibraryFile, track: Track): Target[] {
	const place = ofKind(from.tracks, track.kind).indexOf(track);
	if (place < 0) return [];
	const found: Target[] = [{ path: from.path, index: track.index }];
	for (const file of files) {
		if (file === from || !editable(file)) continue;
		const twin = ofKind(file.tracks, track.kind).find((other, at) =>
			alike(track, place, other, at)
		);
		if (twin) found.push({ path: file.path, index: twin.index });
	}
	return found;
}

// What a batch came to: the counts, one line saying them, and a line per file
// that could not be changed, so a hardlinked episode is named rather than
// counted.
export type Summary = { changed: number; same: number; line: string; problems: string[] };

export function summarise(outcomes: Outcome[]): Summary {
	const changed = outcomes.filter((outcome) => outcome.status === 'retagged').length;
	const same = outcomes.filter((outcome) => outcome.status === 'unchanged').length;
	const problems = outcomes
		.filter((outcome) => outcome.status === 'refused' || outcome.status === 'failed')
		.map((outcome) => `${basename(outcome.path)}: ${outcome.detail ?? outcome.status}`);
	const parts: string[] = [];
	if (changed) parts.push(`Retagged ${count(changed, 'file')}`);
	if (same) parts.push(`${count(same, 'file')} already tagged`);
	if (problems.length) parts.push(`${count(problems.length, 'file')} could not be changed`);
	return { changed, same, line: parts.join(' · ') || 'Nothing changed', problems };
}

function count(number: number, noun: string): string {
	return `${number} ${noun}${number === 1 ? '' : 's'}`;
}

function basename(path: string): string {
	return path.slice(path.lastIndexOf('/') + 1);
}
