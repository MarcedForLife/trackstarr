// The demo's library: titles anybody may copy. The Blender Foundation's open
// movies are CC BY, Sita Sings the Blues is CC0, and the rest have entered the
// public domain. The files, tracks and verdicts are invented around them, so
// the grid can show every state the rules reach; the runtimes and years are
// the real ones.

import type { Track } from '$lib/library';

export type Kind = 'movie' | 'series' | 'folder';

/** What a file's history left on it that the rules alone would not: a rewrite
 * this tool made, a download client still holding it, or a file no sweep has
 * opened. */
export type History =
	| { kind: 'rewritten'; daysAgo: number }
	| { kind: 'deferred'; daysAgo: number; detail: string }
	| { kind: 'unchecked' };

export type FileSpec = {
	/** The name without its extension, in the *arr's own shape. */
	name: string;
	ext: string;
	seconds: number;
	tracks: Track[];
	history?: History;
};

export type TitleSpec = {
	id: string;
	name: string;
	year?: number;
	kind: Kind;
	/** ISO 639-2/B, as the *arr reports it. A folder no *arr claims has none. */
	lang?: string;
	rating?: number;
	/** Days before now the title joined the library. */
	addedDays: number;
	folder: string;
	files: FileSpec[];
	arr?: 'radarr' | 'sonarr';
	/** The IMDb id the *arr carried, which the sheet's IMDb button opens. A
	 * folder no *arr claims has none. */
	imdb?: string;
};

const MOVIES = '/data/media/movies';
const TV = '/data/media/tv';

function video(bitrate: number, codec = 'h264'): Track {
	return { index: 0, kind: 'video', codec, bitrate };
}

function audio(
	index: number,
	codec: string,
	channels: number,
	lang: string | undefined,
	bitrate: number,
	extra: Partial<Track> = {}
): Track {
	return { index, kind: 'audio', codec, channels, lang, bitrate, ...extra };
}

function subtitle(index: number, lang: string, extra: Partial<Track> = {}): Track {
	return { index, kind: 'subtitle', codec: 'subrip', lang, bitrate: 40, ...extra };
}

const DEFAULT = { flags: ['default'] };

/** A disc rip carrying only its surround mix: the downmix rule's whole
 * reason to exist. */
function surroundOnly(lang: string): Track[] {
	return [
		video(9_500_000),
		audio(1, 'eac3', 6, lang, 768_000, DEFAULT),
		subtitle(2, lang),
		subtitle(3, lang, { flags: ['forced'] })
	];
}

/** A rip already carrying both mixes. */
function both(lang: string): Track[] {
	return [
		video(8_000_000, 'hevc'),
		audio(1, 'dts', 6, lang, 1_509_000, DEFAULT),
		audio(2, 'aac', 2, lang, 192_000),
		subtitle(3, lang)
	];
}

/** A web release: stereo and nothing to make a surround mix from. */
function stereo(lang: string): Track[] {
	return [video(2_500_000), audio(1, 'aac', 2, lang, 192_000, DEFAULT)];
}

/** A silent film with a score nobody tagged, which the language rule leaves
 * alone. */
function score(): Track[] {
	return [
		video(3_000_000),
		audio(1, 'flac', 2, undefined, 900_000, { ...DEFAULT, title: 'Score' })
	];
}

/** The stereo track this tool would make, as it sits in a rewritten file. */
function generated(index: number, lang: string): Track {
	return audio(index, 'aac', 2, lang, 320_000, { title: 'Stereo', flags: ['generated'] });
}

type MovieSpec = {
	id: number;
	name: string;
	year: number;
	lang: string;
	rating?: number;
	imdb: string;
	addedDays: number;
	minutes: number;
	/** Nothing downloaded yet: Radarr tracks it and that is all. */
	missing?: boolean;
	ext?: string;
	source?: string;
	tracks?: Track[];
	history?: History;
};

function movie(spec: MovieSpec): TitleSpec {
	const folder = `${MOVIES}/${spec.name} (${spec.year})`;
	const file: FileSpec = {
		name: `${spec.name} (${spec.year}) ${spec.source ?? 'Bluray-1080p'}`,
		ext: spec.ext ?? '.mkv',
		seconds: spec.minutes * 60,
		tracks: spec.tracks ?? both(spec.lang),
		history: spec.history
	};
	return {
		id: `arr:radarr:${spec.id}`,
		name: spec.name,
		year: spec.year,
		kind: 'movie',
		lang: spec.lang,
		rating: spec.rating,
		imdb: spec.imdb,
		addedDays: spec.addedDays,
		folder,
		files: spec.missing ? [] : [file],
		arr: 'radarr'
	};
}

type Episode = { title: string; tracks?: Track[]; history?: History; ext?: string };

type SeriesSpec = {
	id: number;
	name: string;
	year: number;
	lang: string;
	rating?: number;
	imdb: string;
	addedDays: number;
	minutes: number;
	tracks: Track[];
	episodes: Episode[];
};

function series(spec: SeriesSpec): TitleSpec {
	const folder = `${TV}/${spec.name}`;
	return {
		id: `arr:sonarr:${spec.id}`,
		name: spec.name,
		year: spec.year,
		kind: 'series',
		lang: spec.lang,
		rating: spec.rating,
		imdb: spec.imdb,
		addedDays: spec.addedDays,
		folder,
		files: spec.episodes.map((episode, at) => ({
			name: `Season 01/${spec.name} - S01E${String(at + 1).padStart(2, '0')} - ${episode.title}`,
			ext: episode.ext ?? '.mkv',
			seconds: spec.minutes * 60,
			tracks: episode.tracks ?? spec.tracks,
			history: episode.history
		})),
		arr: 'sonarr'
	};
}

/** Every title, built on the first ask: a list built at import would be kept
 * in the normal build for the calls that make it. */
export function catalogue(): TitleSpec[] {
	return [
		// The Blender open movies.
		movie({
			id: 1,
			name: 'Big Buck Bunny',
			year: 2008,
			imdb: 'tt1254207',
			lang: 'eng',
			rating: 6.5,
			addedDays: 400,
			minutes: 10
		}),
		movie({
			id: 2,
			name: 'Sintel',
			year: 2010,
			imdb: 'tt1727587',
			lang: 'eng',
			rating: 7.3,
			addedDays: 380,
			minutes: 15,
			// Subtitles in four languages the settings stopped keeping.
			tracks: [
				video(9_000_000),
				audio(1, 'dts', 6, 'eng', 1_509_000, DEFAULT),
				subtitle(2, 'eng'),
				subtitle(3, 'fre'),
				subtitle(4, 'ger'),
				subtitle(5, 'spa')
			]
		}),
		movie({
			id: 3,
			name: 'Tears of Steel',
			year: 2012,
			imdb: 'tt2285752',
			lang: 'eng',
			rating: 6.0,
			addedDays: 370,
			minutes: 12,
			tracks: surroundOnly('eng')
		}),
		movie({
			id: 4,
			name: 'Elephants Dream',
			year: 2006,
			imdb: 'tt0807840',
			lang: 'eng',
			rating: 5.9,
			addedDays: 400,
			minutes: 11,
			tracks: [
				video(7_000_000),
				audio(1, 'ac3', 6, 'eng', 640_000, DEFAULT),
				generated(2, 'eng'),
				subtitle(3, 'eng')
			],
			history: { kind: 'rewritten', daysAgo: 0 }
		}),
		movie({
			id: 5,
			name: 'Cosmos Laundromat',
			year: 2015,
			imdb: 'tt4957236',
			lang: 'eng',
			rating: 6.8,
			addedDays: 300,
			minutes: 12,
			// An MP4: only Matroska is rewritten or retagged, so the rules skip it.
			ext: '.mp4',
			source: 'WEBDL-1080p',
			tracks: stereo('eng')
		}),
		movie({
			id: 6,
			name: 'Spring',
			year: 2019,
			imdb: 'tt9249278',
			lang: 'eng',
			rating: 7.1,
			addedDays: 200,
			minutes: 8,
			// A commentary track, kept while the commentary rule is off and never a
			// downmix source.
			tracks: [
				video(9_000_000),
				audio(1, 'eac3', 6, 'eng', 768_000, DEFAULT),
				audio(2, 'aac', 2, 'eng', 256_000),
				audio(3, 'aac', 2, 'eng', 192_000, {
					title: "Director's commentary",
					flags: ['commentary']
				}),
				subtitle(4, 'eng')
			]
		}),
		movie({
			id: 7,
			name: 'Sprite Fright',
			year: 2021,
			imdb: 'tt15804252',
			lang: 'eng',
			rating: 6.5,
			addedDays: 150,
			minutes: 10,
			source: 'Remux-2160p',
			tracks: [
				video(48_000_000, 'hevc'),
				audio(1, 'truehd', 8, 'eng', 4_100_000, DEFAULT),
				audio(2, 'ac3', 6, 'eng', 640_000),
				audio(3, 'aac', 2, 'eng', 256_000),
				subtitle(4, 'eng'),
				subtitle(5, 'eng', { title: 'SDH', flags: ['sdh'] })
			]
		}),
		movie({
			id: 9,
			name: 'Coffee Run',
			year: 2020,
			imdb: 'tt13716914',
			lang: 'eng',
			addedDays: 0,
			minutes: 3,
			// Delivered minutes ago and waiting on the sweep's rewrite slot.
			tracks: surroundOnly('eng'),
			history: { kind: 'unchecked' }
		}),
		movie({
			id: 10,
			name: 'Charge',
			year: 2022,
			imdb: 'tt24787066',
			lang: 'eng',
			addedDays: 2,
			minutes: 4,
			missing: true
		}),
		// Public domain films.
		movie({
			id: 21,
			name: 'Metropolis',
			year: 1927,
			imdb: 'tt0017136',
			lang: 'ger',
			rating: 8.3,
			addedDays: 310,
			minutes: 153,
			// The score in surround and a commentary in stereo: the original
			// language owes a stereo mix of its own, and a commentary is never
			// its source.
			tracks: [
				video(9_000_000),
				audio(1, 'dts', 6, 'ger', 1_509_000, { ...DEFAULT, title: 'Score' }),
				audio(2, 'aac', 2, 'eng', 160_000, { title: 'Commentary', flags: ['commentary'] }),
				subtitle(3, 'eng'),
				subtitle(4, 'ger')
			]
		}),
		movie({
			id: 22,
			name: 'The Cabinet of Dr. Caligari',
			year: 1920,
			imdb: 'tt0010323',
			lang: 'ger',
			rating: 8.0,
			addedDays: 305,
			minutes: 76,
			// Intertitles in five languages, three of them no longer kept.
			tracks: [
				video(4_000_000),
				audio(1, 'flac', 2, undefined, 900_000, { ...DEFAULT, title: 'Score' }),
				subtitle(2, 'eng'),
				subtitle(3, 'ger'),
				subtitle(4, 'fre'),
				subtitle(5, 'spa'),
				subtitle(6, 'ita')
			]
		}),
		movie({
			id: 23,
			name: 'Night of the Living Dead',
			year: 1968,
			imdb: 'tt0063350',
			lang: 'eng',
			rating: 7.8,
			addedDays: 290,
			minutes: 96,
			tracks: [
				video(6_000_000),
				audio(1, 'ac3', 2, 'eng', 224_000, DEFAULT),
				subtitle(2, 'eng'),
				subtitle(3, 'eng', { title: 'SDH', flags: ['sdh'] })
			]
		}),
		movie({
			id: 24,
			name: 'His Girl Friday',
			year: 1940,
			imdb: 'tt0032599',
			lang: 'eng',
			rating: 7.8,
			addedDays: 280,
			minutes: 92,
			tracks: stereo('eng')
		}),
		movie({
			id: 25,
			name: 'Charade',
			year: 1963,
			imdb: 'tt0056923',
			lang: 'eng',
			rating: 7.9,
			addedDays: 270,
			minutes: 113,
			// Dubs and subtitles in the two languages a house guest needed once.
			tracks: [
				video(8_500_000),
				audio(1, 'dts', 6, 'eng', 1_509_000, { ...DEFAULT, title: 'DTS-HD MA 5.1 1080p' }),
				audio(2, 'ac3', 2, 'eng', 224_000),
				audio(3, 'ac3', 2, 'fre', 224_000),
				audio(4, 'ac3', 2, 'spa', 224_000),
				subtitle(5, 'eng'),
				subtitle(6, 'fre'),
				subtitle(7, 'spa')
			]
		}),
		movie({
			id: 26,
			name: 'The General',
			year: 1926,
			imdb: 'tt0017925',
			lang: 'eng',
			rating: 8.1,
			addedDays: 265,
			minutes: 79,
			tracks: score()
		}),
		movie({
			id: 27,
			name: 'Sherlock Jr.',
			year: 1924,
			imdb: 'tt0015324',
			lang: 'eng',
			rating: 8.2,
			addedDays: 1,
			minutes: 45,
			tracks: surroundOnly('eng'),
			history: {
				kind: 'deferred',
				daysAgo: 0,
				detail: 'hard-linked 2 times. A download client still has it'
			}
		}),
		movie({
			id: 28,
			name: 'Plan 9 from Outer Space',
			year: 1957,
			imdb: 'tt0052077',
			lang: 'eng',
			rating: 4.0,
			addedDays: 250,
			minutes: 79,
			ext: '.avi',
			source: 'DVD',
			tracks: []
		}),
		movie({
			id: 29,
			name: 'Carnival of Souls',
			year: 1962,
			imdb: 'tt0055830',
			lang: 'eng',
			rating: 7.0,
			addedDays: 245,
			minutes: 78,
			tracks: stereo('eng')
		}),
		movie({
			id: 30,
			name: 'Sita Sings the Blues',
			year: 2008,
			imdb: 'tt1172203',
			lang: 'eng',
			rating: 7.5,
			addedDays: 230,
			minutes: 82,
			// A surround mix nobody tagged: not English as far as the rules can
			// tell, so no stereo is made from it until the tag is set.
			tracks: [video(8_000_000), audio(1, 'aac', 6, undefined, 448_000, DEFAULT)]
		}),
		movie({
			id: 31,
			name: 'Battleship Potemkin',
			year: 1925,
			imdb: 'tt0015648',
			lang: 'rus',
			rating: 7.9,
			addedDays: 225,
			minutes: 75,
			// A German release whose only track is its German narration: dropping
			// it would leave no audio, so the file is left alone.
			tracks: [
				video(4_000_000),
				audio(1, 'ac3', 2, 'ger', 192_000, { ...DEFAULT, title: 'Erzähler' }),
				subtitle(2, 'eng')
			]
		}),
		movie({
			id: 34,
			name: 'Detour',
			year: 1945,
			imdb: 'tt0037638',
			lang: 'eng',
			rating: 7.3,
			addedDays: 200,
			minutes: 68,
			tracks: stereo('eng')
		}),
		movie({
			id: 35,
			name: 'Safety Last!',
			year: 1923,
			imdb: 'tt0014429',
			lang: 'eng',
			rating: 8.1,
			addedDays: 5,
			minutes: 73,
			tracks: [
				video(4_500_000),
				audio(1, 'dts', 6, 'eng', 1_509_000, { ...DEFAULT, title: 'Score' }),
				generated(2, 'eng'),
				subtitle(3, 'eng')
			],
			history: { kind: 'rewritten', daysAgo: 5 }
		}),
		// Series.
		series({
			id: 40,
			name: 'Sherlock Holmes',
			year: 1954,
			imdb: 'tt0046642',
			lang: 'eng',
			rating: 7.6,
			addedDays: 180,
			minutes: 26,
			tracks: [video(3_500_000), audio(1, 'ac3', 2, 'eng', 192_000, DEFAULT), subtitle(2, 'eng')],
			episodes: [
				{ title: 'The Case of the Cunningham Heritage' },
				{ title: 'The Case of Lady Beryl' },
				{ title: 'The Case of the Pennsylvania Gun', tracks: surroundOnly('eng') },
				{ title: 'The Case of the Texas Cowgirl' },
				{ title: 'The Case of the Belligerent Ghost' },
				{ title: 'The Case of the Shy Ballerina' }
			]
		}),
		series({
			id: 43,
			name: 'Tales of Tomorrow',
			year: 1951,
			imdb: 'tt0043238',
			lang: 'eng',
			rating: 7.1,
			addedDays: 160,
			minutes: 25,
			// A restoration's surround remix, with the stereo this tool made when
			// the season landed.
			tracks: [
				video(3_000_000),
				audio(1, 'ac3', 6, 'eng', 448_000, DEFAULT),
				generated(2, 'eng'),
				subtitle(3, 'eng')
			],
			episodes: [
				{ title: 'Verdict from Space', history: { kind: 'rewritten', daysAgo: 12 } },
				{ title: 'Blunder', history: { kind: 'rewritten', daysAgo: 12 } },
				{ title: 'The Dark Angel', history: { kind: 'rewritten', daysAgo: 12 } },
				{ title: 'The Crystal Egg', history: { kind: 'rewritten', daysAgo: 12 } },
				// Copied in by hand after the sweep walked past.
				{
					title: 'Frankenstein',
					history: { kind: 'unchecked' },
					tracks: [video(3_000_000), audio(1, 'ac3', 6, 'eng', 448_000, DEFAULT)]
				}
			]
		}),
		{
			id: 'arr:sonarr:45',
			name: 'One Step Beyond',
			year: 1959,
			imdb: 'tt0052442',
			kind: 'series',
			lang: 'eng',
			rating: 7.7,
			addedDays: 4,
			folder: `${TV}/One Step Beyond`,
			files: [],
			arr: 'sonarr'
		}
	];
}
