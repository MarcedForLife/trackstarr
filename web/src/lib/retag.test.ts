import { describe, expect, test, vi } from 'vitest';
import type { LibraryFile, Track } from '$lib/library';
import { changes, editable, matching, retagTracks, summarise, type Outcome } from '$lib/retag';

const door = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('$lib/api', () => ({ request: door.request }));

function track(index: number, kind: string, extra: Partial<Track> = {}): Track {
	return { index, kind, codec: kind === 'audio' ? 'ac3' : 'subrip', ...extra };
}

function file(name: string, tracks: Track[]): LibraryFile {
	return {
		path: `/tv/Show/${name}`,
		name,
		status: 'conform',
		bytes: 1,
		seconds: 3600,
		tracks,
		planned: [],
		why: {}
	};
}

// A season ripped alike: an untagged 5.1 and an English 2.0 in every episode.
const one = file('one.mkv', [
	track(0, 'video'),
	track(1, 'audio', { channels: 6 }),
	track(2, 'audio', { channels: 2, lang: 'eng' })
]);
const two = file('two.mkv', [
	track(0, 'video'),
	track(1, 'audio', { channels: 6 }),
	track(2, 'audio', { channels: 2, lang: 'eng' })
]);

describe('matching', () => {
	test('finds the same track in every other file, this file first', () => {
		expect(matching([two, one], one, one.tracks[1])).toEqual([
			{ path: one.path, index: 1 },
			{ path: two.path, index: 1 }
		]);
	});

	test('goes by the place among its kind, not the stream number', () => {
		// A cover art stream ahead of the audio shifts every index by one.
		const shifted = file('three.mkv', [
			track(0, 'video'),
			track(1, 'video', { codec: 'mjpeg' }),
			track(2, 'audio', { channels: 6 }),
			track(3, 'audio', { channels: 2, lang: 'eng' })
		]);
		expect(matching([shifted], one, one.tracks[2])).toEqual([
			{ path: one.path, index: 2 },
			{ path: shifted.path, index: 3 }
		]);
	});

	test('a track that differs in channels, language, title or codec is not a twin', () => {
		const other = file('four.mkv', [
			track(0, 'video'),
			track(1, 'audio', { channels: 8 }),
			track(2, 'audio', { channels: 2, lang: 'ger' }),
			track(3, 'audio', { channels: 2, lang: 'eng', title: 'Commentary' })
		]);
		expect(matching([other], one, one.tracks[1])).toEqual([{ path: one.path, index: 1 }]);
		expect(matching([other], one, one.tracks[2])).toEqual([{ path: one.path, index: 2 }]);
	});

	test('a file the service cannot edit in place is left out', () => {
		const mp4 = { ...two, name: 'two.mp4', path: '/tv/Show/two.mp4' };
		expect(matching([mp4], one, one.tracks[1])).toEqual([{ path: one.path, index: 1 }]);
		expect(editable(mp4)).toBe(false);
		expect(editable(one)).toBe(true);
	});

	test('a track the file does not hold matches nothing', () => {
		expect(matching([two], one, track(9, 'audio'))).toEqual([]);
	});
});

describe('summarise', () => {
	const done = (path: string, status: Outcome['status'], detail?: string): Outcome => ({
		path,
		status,
		detail
	});

	test('counts what changed and names what did not', () => {
		const told = summarise([
			done('/tv/Show/one.mkv', 'retagged'),
			done('/tv/Show/two.mkv', 'retagged'),
			done('/tv/Show/three.mkv', 'unchanged', 'already tagged that way'),
			done('/tv/Show/four.mkv', 'refused', 'hardlinked: no')
		]);
		expect(told.line).toBe(
			'Retagged 2 files · 1 file already tagged · 1 file could not be changed'
		);
		expect([told.changed, told.same]).toEqual([2, 1]);
		expect(told.problems).toEqual(['four.mkv: hardlinked: no']);
	});

	test('one file reads in the singular', () => {
		expect(summarise([done('/tv/Show/one.mkv', 'retagged')]).line).toBe('Retagged 1 file');
	});

	test('nothing at all is said so', () => {
		expect(summarise([]).line).toBe('Nothing changed');
	});
});

describe('changes', () => {
	const audio = track(1, 'audio', { lang: 'eng', flags: ['commentary'] });

	test('a language alone', () => {
		expect(changes(audio, 'jpn', { commentary: true })).toEqual({ lang: 'jpn' });
	});

	test('only the flags that moved, and a flag left as the row read it is no edit', () => {
		expect(changes(audio, 'eng', { commentary: false })).toEqual({ flags: { commentary: false } });
		expect(changes(audio, 'eng', { commentary: true })).toEqual({});
		expect(changes(audio, 'eng', {})).toEqual({});
	});

	test('an untagged track is und, so leaving it there is no edit', () => {
		expect(changes(track(1, 'audio'), 'und', {})).toEqual({});
		expect(changes(track(1, 'audio'), 'eng', {})).toEqual({ lang: 'eng' });
	});

	test('a flag of another kind is not offered, so it is not sent', () => {
		expect(changes(audio, 'eng', { forced: true })).toEqual({});
	});
});

describe('retagTracks', () => {
	test('posts the targets beside the edit and reads the results', async () => {
		door.request.mockResolvedValueOnce({
			results: [{ path: '/tv/Show/one.mkv', status: 'retagged', verdict: 'pending' }]
		});
		const targets = [{ path: '/tv/Show/one.mkv', index: 1 }];
		const outcomes = await retagTracks(targets, { lang: 'jpn', flags: { commentary: true } });
		expect(outcomes).toEqual([
			{ path: '/tv/Show/one.mkv', status: 'retagged', verdict: 'pending' }
		]);
		expect(door.request).toHaveBeenCalledWith('/api/library/retag', {
			method: 'POST',
			body: JSON.stringify({ tracks: targets, lang: 'jpn', flags: { commentary: true } })
		});
	});

	test('an answer without results is none', async () => {
		door.request.mockResolvedValueOnce({ status: 'done' });
		expect(await retagTracks([], {})).toEqual([]);
	});
});
