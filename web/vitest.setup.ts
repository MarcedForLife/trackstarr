// Node has no localStorage, and $lib/prefs is written to survive a browser
// that refuses one, so a missing global would test the wrong branch.
const kept = new Map<string, string>();

globalThis.localStorage = {
	getItem: (key: string) => kept.get(key) ?? null,
	setItem: (key: string, value: string) => void kept.set(key, String(value)),
	removeItem: (key: string) => void kept.delete(key),
	clear: () => kept.clear()
} as unknown as Storage;
