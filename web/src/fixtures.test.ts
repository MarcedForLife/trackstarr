// The service's answers, as tests/test_contract.py wrote them down, held
// against the types the pages read them through. A key the service has renamed
// or added fails `npm run check` here; a count or an event the pages have no
// words for fails `npm test`.

import { describe, expect, test } from 'vitest';
import { fileRows, fileStatus, progressLabel, remaining, verdicts } from '$lib/runs';
import type { Activity, Run, Stage } from '$lib/runs';
import { asVerdict, FILTERS, pip, tint, VERDICTS, verdictLabel } from '$lib/library';
import type { Shelf, Summary, TitleDetail } from '$lib/library';
import { chips, detail, details, headline, verdicts as counted } from '$lib/events';
import type { EventPage } from '$lib/events';
import type { Hold } from '$lib/holds';
import type { SettingsSnapshot } from '$lib/settings';
import { SERVICES } from '$lib/connections';
import type { ConnectionResult } from '$lib/connections';
import { KINDS } from '$lib/stream';
import { FLOW } from '$lib/order.svelte';
import { sift, tally } from '$lib/shelfview';
import type { View } from '$lib/shelfview';
import runsJson from './fixtures/runs.json';
import libraryJson from './fixtures/library.json';
import summaryJson from './fixtures/summary.json';
import titleJson from './fixtures/title.json';
import eventsJson from './fixtures/events.json';
import holdsJson from './fixtures/holds.json';
import settingsJson from './fixtures/settings.json';
import connectionJson from './fixtures/connection.json';
import vocabulary from './fixtures/vocabulary.json';

/**
 * A JSON import as the file spells it. TypeScript reads an array of unalike
 * objects as a union and gives each member the others' keys as `?: undefined`,
 * which no `Record<string, number>` will take. Those keys were never there,
 * so they are dropped again here.
 */
type Spelled<Value> = Value extends readonly (infer Item)[]
	? Spelled<Item>[]
	: Value extends object
		? { [Key in keyof Value as undefined extends Value[Key] ? never : Key]: Spelled<Value[Key]> }
		: Value;

function spelled<Value>(value: Value): Spelled<Value> {
	return value as Spelled<Value>;
}

const wire = spelled(runsJson);
const shelf = spelled(libraryJson);
const summary = spelled(summaryJson);
const title = spelled(titleJson);
const page = spelled(eventsJson);
const held = spelled(holdsJson);
const settings = spelled(settingsJson);
const connection = spelled(connectionJson);

/**
 * `Value` with every key `Shape` has no name for turned to `never`, at every
 * depth. `satisfies` on its own lets an unknown key through, and most fields
 * in these types are optional, so a key the service renamed would pass: the
 * old name is allowed to be missing and the new one is never looked at.
 */
type Exactly<Shape, Value> = unknown extends Shape
	? Value
	: Value extends readonly (infer Item)[]
		? Shape extends readonly (infer Want)[]
			? readonly Exactly<Want, Item>[]
			: never
		: Value extends object
			? Shape extends object
				? {
						[Key in keyof Value]: Key extends keyof Shape ? Exactly<Shape[Key], Value[Key]> : never;
					}
				: never
			: Value;

/** Pin a fixture to a type: nothing missing, nothing mistyped, nothing extra. */
function pins<Shape>() {
	return <Value extends Shape>(value: Value & Exactly<Shape, Value>): Value => value;
}

// A JSON import widens every string, and two fields here are narrower than
// that; what they may hold is asserted below. `seen` is stamped on arrival by
// getActivity, so the wire never carries it.
const activity = pins<Activity>()({
	...wire,
	runs: wire.runs.map((run) => ({
		...run,
		kind: run.kind as Run['kind'],
		active: run.active.map((file) => ({ ...file, stage: file.stage as Stage })),
		seen: 0
	}))
});

// A card's verdict is narrower than the string it arrives as too, and the
// fetchers narrow it the same way. A word the service has that the union has
// not falls back here rather than failing, so the test below holds them equal.
const verdicted = <Item extends { state: string }>(card: Item) => ({
	...card,
	state: asVerdict(card.state)
});

const shelved = pins<Shelf>()({ ...shelf, titles: shelf.titles.map(verdicted) });
const summarised = pins<Summary>()({ ...summary, head: summary.head.map(verdicted) });
pins<TitleDetail>()({
	...title,
	files: title.files.map((file) => ({ ...file, status: asVerdict(file.status) }))
});
pins<EventPage>()({
	...page,
	titles: Object.fromEntries(Object.entries(page.titles).map(([id, card]) => [id, verdicted(card)]))
});
pins<{ holds: Hold[] }>()(held);
pins<SettingsSnapshot>()(settings);
pins<ConnectionResult>()(connection);

describe('the activity', () => {
	test('reads every run and file as one of its kinds', () => {
		for (const run of activity.runs) {
			expect(['sweep', 'import', 'recheck']).toContain(run.kind);
			for (const file of run.active)
				expect(['working', 'waiting', 'encoding']).toContain(file.stage);
		}
	});

	test('names every verdict a run counts', () => {
		for (const run of activity.runs) {
			expect(verdicts(run)).toHaveLength(Object.keys(run.counts).length);
		}
	});

	test('has a line for every run and file', () => {
		for (const run of activity.runs) {
			progressLabel(run);
			remaining(run, activity.paused, 30);
			for (const row of fileRows(run)) if (row.live) fileStatus(row.live, 30);
		}
	});
});

describe('the library', () => {
	const view: View = {
		filters: [],
		hidden: [],
		kind: '',
		needle: '',
		sort: 'processed',
		flow: FLOW.processed
	};

	test('counts the shelf to the summary the service counted', () => {
		expect(tally(shelved.titles)).toEqual(summarised.counts);
		expect(shelved.titles).toHaveLength(summarised.titles);
	});

	test('keeps the shelf in the order the strip is cut from', () => {
		const ordered = sift(shelved.titles, view).map((card) => card.id);
		expect(ordered).toHaveLength(shelved.titles.length);
		expect(ordered.slice(0, summarised.head.length)).toEqual(
			summarised.head.map((card) => card.id)
		);
	});

	// The narrowing above falls back rather than failing, so a verdict the
	// service has and the Verdict union has not would go unnoticed otherwise.
	test('has the union for every verdict the service wrote', () => {
		for (const state of [...vocabulary.states, vocabulary.mixed, ...vocabulary.filters]) {
			expect(asVerdict(state)).toBe(state);
		}
		for (const card of shelf.titles) expect(asVerdict(card.state)).toBe(card.state);
		for (const file of title.files) expect(asVerdict(file.status)).toBe(file.status);
		// A run says words the library never stores, so its counts are their own
		// list.
		for (const run of activity.runs)
			for (const word of Object.keys(run.counts)) expect(asVerdict(word)).toBe(word);
	});

	test('has a word and a colour for every state', () => {
		// The states a file can be in, the word a mixed card leads with, and the
		// one filter that is not a verdict at all.
		for (const state of [...vocabulary.states, vocabulary.mixed, ...FILTERS]) {
			expect(pip).toHaveProperty(state);
			// `unchecked` is the one state said as Unknown on purpose.
			if (state !== 'unchecked') expect(verdictLabel(asVerdict(state))).not.toBe('Unknown');
		}
	});

	// Every chip is drawn from `tint`, so one without an entry is an unstyled
	// button the moment something lands in that state.
	test('has a switched-on colour for every chip the grid offers', () => {
		for (const state of FILTERS) expect(tint).toHaveProperty(state);
	});
});

describe('the history', () => {
	test('has words for every event the service records', () => {
		for (const entry of page.events) {
			expect(headline(entry), entry.event).not.toBe(entry.event);
			detail(entry);
			chips(entry);
			expect(details(entry).length).toBeGreaterThan(0);
		}
	});

	test('names every verdict a summary counts', () => {
		for (const entry of page.events) {
			if (!('counts' in entry)) continue;
			expect(counted(entry)).toHaveLength(Object.keys(entry.counts).length);
		}
	});
});

describe('the vocabulary', () => {
	test('says the verdicts in the order the service sorts them', () => {
		expect(VERDICTS).toEqual(vocabulary.states);
	});

	test('offers the same chips the service counts', () => {
		expect(FILTERS).toEqual(vocabulary.filters);
	});

	test('names the same services', () => {
		expect(SERVICES.map(({ name, label }) => ({ name, label }))).toEqual(vocabulary.services);
	});

	test('listens for every kind the service publishes', () => {
		expect([...KINDS].sort()).toEqual([...vocabulary.kinds].sort());
	});
});
