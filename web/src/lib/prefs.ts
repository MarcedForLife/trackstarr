// Per-browser display choices, one localStorage key holding one word each.
// Removed while at the default, so a changed default reaches every browser
// that never chose.

export function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
	try {
		const found = localStorage.getItem(key);
		if (found && (allowed as readonly string[]).includes(found)) return found as T;
	} catch {
		/* storage blocked: the default is the right answer */
	}
	return fallback;
}

export function keep(key: string, value: string, isDefault: boolean) {
	try {
		if (isDefault) localStorage.removeItem(key);
		else localStorage.setItem(key, value);
	} catch {
		/* the choice still applies, it just will not survive a reload */
	}
}

/** A two-way choice: `1` when not the default, no key when it is. The word is
 * read, since an earlier spelling wrote `0`. */
export function storedFlag(key: string): boolean {
	return stored(key, ['1', '0'], '0') === '1';
}

export function keepFlag(key: string, on: boolean) {
	keep(key, '1', !on);
}

/** A multiple choice: one key of comma-separated words. Unknown words are
 * dropped so a retired verdict cannot wedge the page. Empty is the default. */
export function storedAll<T extends string>(key: string, allowed: readonly T[]): T[] {
	try {
		const found = localStorage.getItem(key);
		if (!found) return [];
		const known = new Set<string>(allowed);
		return [...new Set(found.split(',').filter((word) => known.has(word)))] as T[];
	} catch {
		return [];
	}
}

export function keepAll(key: string, values: readonly string[]) {
	keep(key, values.join(','), !values.length);
}
