// The rules, as far as the demo needs them: enough of the service's planner to
// turn a file's tracks and the settings into the verdict, the plan and the
// reasons the sheet shows, so a settings change or a retag moves the library
// the way it would for real. Regeneration, remuxing, cover art and stray
// streams are left out; the sample files carry none of them.

import type { LibraryFile, Track, Verdict, Why } from '$lib/library';
import type { SettingValue } from '$lib/settings';

export type Settings = Record<string, SettingValue>;

export type Judgement = { status: Verdict; planned: Track[]; why: Why };

// Channel counts by layout name, and what a new row of each is made at; the
// same tables the settings snapshot carries.
const CHANNELS: Record<string, number> = { '1.0': 1, '2.0': 2, '5.1': 6, '6.1': 7, '7.1': 8 };
export const STOCK: Record<string, [string, string]> = {
	'1.0': ['aac', '160k'],
	'2.0': ['aac', '320k'],
	'5.1': ['ac3', '640k'],
	'6.1': ['aac', '704k'],
	'7.1': ['aac', '768k']
};

// The reserved LANGUAGES name for the title's own language.
export const ORIGINAL = 'original';

type Layout = { name: string; channels: number; action: string; codec: string; rate: string };

/** An AUDIO_LAYOUTS entry read by how many fields it holds. */
function layoutOf(entry: string): Layout {
	const [name, second, third] = entry.split(':');
	const [codec, rate] = STOCK[name] ?? ['aac', '320k'];
	if (third !== undefined)
		return { name, channels: CHANNELS[name], action: 'downmix', codec: second, rate: third };
	if (second === 'keep' || second === 'remove')
		return { name, channels: CHANNELS[name], action: second, codec, rate };
	return { name, channels: CHANNELS[name], action: 'downmix', codec, rate };
}

function list(settings: Settings, name: string): string[] {
	const value = settings[name];
	return Array.isArray(value) ? value : [];
}

function mode(settings: Settings, rule: string): string {
	return String(settings[`RULE_${rule.toUpperCase()}`] ?? 'never');
}

/** The bitrate a rate like `320k` names. */
function bitrate(rate: string): number {
	return Math.round(parseFloat(rate) * (rate.endsWith('k') ? 1000 : 1));
}

/** `index (detail, title)` as the service labels a stream in a reason. */
function label(track: Track, detail = ''): string {
	const parts = [detail, track.title].filter(Boolean);
	return parts.length ? `${track.index} (${parts.join(', ')})` : String(track.index);
}

class Plan {
	reasons: string[] = [];
	rules: string[] = [];
	incidental: string[] = [];
	incidentalRules: string[] = [];
	dropped = new Set<number>();
	cleared = new Set<number>();

	constructor(private settings: Settings) {}

	/** Record what a rule would do under its mode: a reason of its own, one
	 * that rides along, or nothing. Says whether the rule acts at all. */
	record(rule: string, reason: string, always = false): boolean {
		const how = always ? 'always' : mode(this.settings, rule);
		if (how === 'never') return false;
		const [reasons, rules] =
			how === 'always' ? [this.reasons, this.rules] : [this.incidental, this.incidentalRules];
		reasons.push(reason);
		if (!rules.includes(rule)) rules.push(rule);
		return true;
	}

	drop(rule: string, track: Track, reason: string) {
		if (this.record(rule, reason)) this.dropped.add(track.index);
	}
}

/** Every language kept, resolved against the title, and the ones downmixes are
 * made in. A row naming the original acts only where the *arr said what that
 * is. */
function languages(settings: Settings, titleLang?: string) {
	const kept: string[] = [];
	const downmixed: string[] = [];
	for (const entry of list(settings, 'LANGUAGES')) {
		const [name, action = 'downmix'] = entry.split(':');
		const lang = name === ORIGINAL ? titleLang : name;
		if (!lang || kept.includes(lang)) continue;
		kept.push(lang);
		if (action === 'downmix') downmixed.push(lang);
	}
	return { kept, downmixed };
}

function unsupported(ext: string): Judgement {
	return {
		status: 'unsupported',
		planned: [],
		why: { skip: `container ${ext} not in ALLOWED_EXTS` }
	};
}

function skipped(reason: string): Judgement {
	return { status: 'skip', planned: [], why: { skip: reason } };
}

/** The tracks in the order a rewrite writes them: video, audio in layout order
 * then other sizes largest first, subtitles, then anything else. */
function ordered(tracks: Track[], layouts: Layout[]): Track[] {
	const rank = (track: Track) => {
		const named = layouts.findIndex((layout) => layout.channels === track.channels);
		return named >= 0 ? named : layouts.length + (100 - (track.channels ?? 0));
	};
	const kinds = ['video', 'audio', 'subtitle'];
	const place = (track: Track) => {
		const at = kinds.indexOf(track.kind);
		return at < 0 ? kinds.length : at;
	};
	return [...tracks].sort(
		(a, b) => place(a) - place(b) || (a.kind === 'audio' ? rank(a) - rank(b) : 0)
	);
}

export function judge(
	file: Pick<LibraryFile, 'tracks'> & { ext: string },
	titleLang: string | undefined,
	settings: Settings
): Judgement {
	if (!list(settings, 'ALLOWED_EXTS').includes(file.ext)) return unsupported(file.ext);
	if (!file.tracks.length) return { status: 'unchecked', planned: [], why: {} };

	const plan = new Plan(settings);
	const { kept, downmixed } = languages(settings, titleLang);
	const layouts = list(settings, 'AUDIO_LAYOUTS').map(layoutOf);
	const audio = file.tracks.filter((track) => track.kind === 'audio');
	const subtitles = file.tracks.filter((track) => track.kind === 'subtitle');

	// Languages: anything tagged with one the settings do not name. Untagged
	// tracks stay, and a file left with no audio is left whole.
	if (mode(settings, 'languages') !== 'never') {
		const foreign = (track: Track) => !!track.lang && !kept.includes(track.lang);
		if (audio.length && audio.every(foreign)) return skipped('would remove every audio track');
		for (const track of [...audio, ...subtitles].filter(foreign)) {
			plan.drop('languages', track, `drop ${track.kind} ${label(track, track.lang)}`);
		}
	}

	for (const track of audio) {
		if (track.flags?.includes('commentary'))
			plan.drop('commentary', track, `drop commentary audio ${label(track)}`);
	}

	// SDH: only where the same language keeps a full subtitle.
	for (const track of subtitles) {
		if (!track.flags?.includes('sdh') || plan.dropped.has(track.index)) continue;
		const full = subtitles.some(
			(other) =>
				other.lang === track.lang && !other.flags?.includes('sdh') && !plan.dropped.has(other.index)
		);
		if (full) plan.drop('sdh', track, `drop SDH subtitle ${label(track, track.lang)}`);
	}

	const tags = new RegExp(String(settings.RELEASE_TAG_PATTERN || '(?!)'), 'i');
	for (const track of file.tracks) {
		if (track.title && tags.test(track.title) && !plan.dropped.has(track.index)) {
			const reason = `clear release tags on ${track.kind} ${track.index} ('${track.title}')`;
			if (plan.record('release_tags', reason)) plan.cleared.add(track.index);
		}
	}

	const surviving = () =>
		audio.filter((track) => !plan.dropped.has(track.index) && !track.flags?.includes('commentary'));

	// Removed sizes go after the downmixes are chosen from them, and never the
	// last audio track.
	const removed = layouts.filter((layout) => layout.action === 'remove');

	const generated: Track[] = [];
	for (const layout of layouts) {
		if (layout.action !== 'downmix') continue;
		for (const lang of downmixed) {
			const pool = surviving().filter((track) => track.lang === lang);
			if (pool.some((track) => track.channels === layout.channels)) continue;
			const source = pool
				.filter((track) => (track.channels ?? 0) > layout.channels)
				.sort(
					(a, b) => (b.channels ?? 0) - (a.channels ?? 0) || (b.bitrate ?? 0) - (a.bitrate ?? 0)
				)[0];
			if (!source) continue;
			plan.record(
				'downmix',
				`add ${layout.name} downmix from stream ${source.index} (${source.channels}ch ${lang})`,
				true
			);
			generated.push({
				index: -1,
				kind: 'audio',
				codec: layout.codec,
				channels: layout.channels,
				lang,
				title: layout.name === '2.0' ? 'Stereo' : layout.name,
				bitrate: bitrate(layout.rate),
				flags: ['generated'],
				src: source.index
			});
		}
	}

	for (const layout of removed) {
		const sized = surviving().filter((track) => track.channels === layout.channels);
		if (sized.length && sized.length + generated.length < surviving().length + generated.length) {
			for (const track of sized) {
				plan.drop(
					'drop_layouts',
					track,
					`drop ${layout.name} audio ${label(track, track.lang ?? 'und')}`
				);
			}
		}
	}

	const why: Why = {};
	if (plan.reasons.length) {
		why.reasons = plan.reasons;
		why.rules = plan.rules;
	}
	if (plan.incidental.length) {
		why.incidental = plan.incidental;
		why.incidental_rules = plan.incidentalRules;
	}
	if (!plan.reasons.length) return { status: 'conform', planned: [], why };

	// A rewrite: every surviving track in output order, each naming the stream
	// it copies, and the generated ones among them.
	const kept_tracks = file.tracks
		.filter((track) => !plan.dropped.has(track.index))
		.map((track) => ({
			...track,
			src: track.index,
			title: plan.cleared.has(track.index) ? undefined : track.title
		}));
	const planned = ordered([...kept_tracks, ...generated], layouts).map((track, at) => ({
		...track,
		index: at
	}));
	return { status: 'pending', planned, why };
}

/** The layouts a plan's generated tracks add, by their titles, as a card names
 * them. */
export function added(planned: Track[]): string[] {
	return planned
		.filter((track) => track.flags?.includes('generated') && track.title)
		.map((track) => track.title!);
}

/** The layouts a plan generates, as the history names them: `2.0`, `5.1`. */
export function downmixes(planned: Track[]): string[] {
	const names = Object.entries(CHANNELS);
	return planned
		.filter((track) => track.flags?.includes('generated'))
		.map(
			(track) =>
				names.find(([, channels]) => channels === track.channels)?.[0] ?? `${track.channels}ch`
		);
}
