// What the sheet says of a file's audio sourcing: a line per LANGUAGES language
// the file lacks, and the few words a passing verdict carries for them.

import type { LibraryFile, Requirement, RequirementState } from '$lib/library';
import { langLabel } from '$lib/rules';

// Null while the names are still being read, when the codes stand in.
type Names = Record<string, string> | null;

// Python's Fraction writes a whole scale without a denominator.
const SAME_SPEED = new Set(['1', '1/1']);

/** How a source's timeline sits on the file's: "+1.250 s, 25/24 speed". Empty
 * for a source on the same timeline, and an offset under a millisecond is no
 * offset. */
export function timing(mapping: Requirement['mapping']): string {
	if (!mapping) return '';
	const parts: string[] = [];
	const millis = Math.round(mapping.offset_us / 1000);
	if (millis) parts.push(`${millis < 0 ? '−' : '+'}${(Math.abs(millis) / 1000).toFixed(3)} s`);
	if (!SAME_SPEED.has(mapping.scale)) parts.push(`${mapping.scale} speed`);
	return parts.join(', ');
}

function from(requirement: Requirement): string {
	return `from ${requirement.track?.source || 'another variant'}`;
}

function because(state: string, reason: string | undefined): string {
	return reason ? `${state}, ${reason}` : state;
}

// What follows "French audio" for each state a file is still short in.
const SAID: Record<Exclude<RequirementState, 'satisfied'>, (requirement: Requirement) => string> = {
	missing_source: () => 'missing, no variant has it',
	unavailable: (requirement) =>
		because('source unknown', requirement.reason || 'a possible source could not be read'),
	analysis_required: (requirement) => `${from(requirement)}, timing to be measured`,
	ready: (requirement) => {
		const timed = timing(requirement.mapping);
		return `${from(requirement)}, ready${timed ? ` (${timed})` : ''}`;
	},
	needs_review: (requirement) => because('needs review', requirement.reason),
	failed: (requirement) => because('not imported', requirement.reason)
};

function open(requirement: Requirement): boolean {
	return requirement.state !== 'satisfied';
}

/** One line per requirement the file is short of, in LANGUAGES order. A state
 * this build has no words for still says the file lacks the language. */
export function sourcingLines(
	file: Pick<LibraryFile, 'sourcing'>,
	names: Names
): { lang: string; text: string }[] {
	return (file.sourcing?.requirements ?? []).filter(open).map((requirement) => {
		const said = Object.hasOwn(SAID, requirement.state)
			? SAID[requirement.state as keyof typeof SAID](requirement)
			: because('missing', requirement.reason);
		return {
			lang: requirement.lang,
			text: `${langLabel(names ?? {}, requirement.lang)} audio ${said}`
		};
	});
}

/** "French", "French and German", "French, German and Spanish". */
function listed(words: string[]): string {
	return words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words.at(-1)}` : words[0];
}

/** What a passing file lacks, for beside its verdict: "French audio missing".
 * Empty for any other verdict, which says enough on its own. */
export function missingAudio(file: Pick<LibraryFile, 'status' | 'sourcing'>, names: Names): string {
	if (file.status !== 'conform' || !file.sourcing || file.sourcing.completeness === 'complete')
		return '';
	const missing = file.sourcing.requirements
		.filter(open)
		.map((requirement) => langLabel(names ?? {}, requirement.lang));
	return missing.length ? `${listed(missing)} audio missing` : '';
}
