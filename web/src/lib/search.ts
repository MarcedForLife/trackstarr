// Matching what was typed against the words on a line. A filter, not a ranking.
// A feed is chronological and score order would throw that away.

// Release names are dotted where a reader types spaces. Letters of any script
// survive, only the punctuation between them goes.
const SEPARATORS = /[^\p{L}\p{N}]+/gu;

/** Text as plain words. Idempotent, so text already through it can go again. */
export function words(text: string): string {
	return text
		.toLowerCase()
		.normalize('NFD')
		.replace(/\p{Diacritic}/gu, '')
		.replace(SEPARATORS, ' ')
		.trim();
}

// Under this, scattered letters are found in nearly every word.
const LOOSE_FROM = 4;

// Letters in order but apart, "expnse" against "expanse". One word at a time,
// since over a whole line it is true of almost anything typed.
function scattered(term: string, word: string): boolean {
	let at = 0;
	for (const letter of term) {
		at = word.indexOf(letter, at) + 1;
		if (!at) return false;
	}
	return true;
}

/** What was typed, as the words a line must answer to. */
export function terms(query: string): string[] {
	const typed = words(query);
	return typed ? typed.split(' ') : [];
}

/**
 * Whether a line answers to every word typed, in any order and out of the
 * phrase they sit in. A long word found nowhere whole is allowed a misspelling
 * within one word of the line.
 *
 * `haystack` comes in already through `words`, and is split only on a miss,
 * since this runs over everything loaded at every keystroke.
 */
export function matches(haystack: string, terms: readonly string[]): boolean {
	let held: string[] | undefined;
	return terms.every((term) => {
		if (haystack.includes(term)) return true;
		if (term.length < LOOSE_FROM) return false;
		held ??= haystack.split(' ');
		return held.some((word) => scattered(term, word));
	});
}
