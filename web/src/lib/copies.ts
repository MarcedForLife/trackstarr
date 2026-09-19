import { size, verdictLabel, type LibraryFile } from '$lib/library';

export type FileGroup = { key: string; label: string; files: LibraryFile[] };

/** Keep independent files independent unless their episode numbers agree.
 * Multi-episode releases have a different key from either single episode.
 * Shared copy-groups.json fixtures enforce agreement with backend pagination. */
export function fileGroups(files: LibraryFile[], kind: string): FileGroup[] {
	const groups = new Map<string, FileGroup>();
	for (const file of files) {
		const episode = file.name
			.match(/\bS\d{1,3}E\d{1,3}(?:(?:-?E|-)\d{1,3})*\b/i)?.[0]
			.toUpperCase();
		const key = kind === 'movie' ? 'film' : kind === 'series' && episode ? episode : file.path;
		const group = groups.get(key) ?? { key, label: episode ?? 'Film', files: [] };
		group.files.push(file);
		groups.set(key, group);
	}
	return [...groups.values()];
}

/** Lead with the source when the files come from more than one, then quality
 * when the release name supplies it. Ambiguous choices retain the path, so
 * identical basenames in different folders stay distinct. */
export function copyOptions(files: LibraryFile[]): { value: string; label: string }[] {
	const sourced = new Set(files.map((file) => file.source)).size > 1;
	const labels = files.map((file) => {
		const match = file.name.match(/\b(?:2160|1080|720|576|480)[pi]\b/i);
		const quality = match?.[0];
		const suffix = match
			? file.name
					.slice(match.index! + match[0].length)
					.replace(/\.[a-z0-9]{2,4}$/i, '')
					.replaceAll(quality!, '')
					.replace(/^[\s[._-]+|[\s\]]+$/g, '')
			: '';
		const release = [quality ?? file.name, suffix].filter(Boolean).join(' · ');
		return [sourced ? file.source : '', release, size(file.bytes), verdictLabel(file.status)]
			.filter(Boolean)
			.join(' · ');
	});
	return files.map((file, at) => ({
		value: file.path,
		label:
			labels.filter((label) => label === labels[at]).length > 1
				? `${labels[at]} · ${file.path}`
				: labels[at]
	}));
}
