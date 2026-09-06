// The protocol every settings page follows: edit a clone of the snapshot and
// send only what differs, so an invalidation mid-edit cannot clobber a draft.

import type { Setting, SettingValue } from '$lib/settings';

export function cloneValues(settings: Record<string, Setting>): Record<string, SettingValue> {
	return Object.fromEntries(
		Object.entries(settings).map(([name, setting]) => [
			name,
			Array.isArray(setting.value) ? [...setting.value] : setting.value
		])
	);
}

// Arrays compare as sets unless order is meaningful.
function normalised(value: SettingValue | undefined, ordered: boolean): string {
	if (Array.isArray(value) && !ordered) return JSON.stringify([...value].sort());
	return JSON.stringify(value);
}

/** Every name the draft changed, plus null for each it dropped, which is how a
 * removed layout takes its bitrate with it. Env-pinned names are skipped. */
export function changes(
	draft: Record<string, SettingValue>,
	baseline: Record<string, Setting>,
	ordered: Set<string> = new Set()
): Record<string, SettingValue | null> {
	const out: Record<string, SettingValue | null> = {};
	for (const [name, value] of Object.entries(draft)) {
		const base = baseline[name];
		if (base?.env) continue;
		const inOrder = ordered.has(name);
		if (normalised(value, inOrder) !== normalised(base?.value, inOrder)) out[name] = value;
	}
	for (const [name, setting] of Object.entries(baseline)) {
		if (!(name in draft) && !setting.env) out[name] = null;
	}
	return out;
}
