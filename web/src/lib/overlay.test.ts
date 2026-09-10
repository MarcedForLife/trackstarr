import { beforeEach, expect, test, vi } from 'vitest';

// Enough of a browser for the module to stand on: the history entries it
// pushes, the state the current one carries, and the two listeners it answers
// through. Everything here turns on the order of those entries, so they are
// kept as a list and back() pops one the way a gesture does.
const browser = vi.hoisted(() => ({
	page: { state: {} as App.PageState },
	entries: [{}] as App.PageState[],
	handlers: new Map<string, Set<(event: unknown) => void>>(),
	unmounts: [] as (() => void)[]
}));

vi.mock('$app/state', () => ({ page: browser.page }));

vi.mock('$app/navigation', () => ({
	pushState: (_url: string, state: App.PageState) => {
		browser.entries.push(state);
		browser.page.state = state;
	},
	replaceState: (_url: string, state: App.PageState) => {
		browser.entries[browser.entries.length - 1] = state;
		browser.page.state = state;
	}
}));

// An overlay registers its unmount during component init, and there is no
// component here.
vi.mock('svelte', () => ({ onDestroy: (fn: () => void) => browser.unmounts.push(fn) }));

function fire(type: string, event: unknown = {}) {
	for (const handler of [...(browser.handlers.get(type) ?? [])]) handler(event);
}

/** The back gesture: the entry on top goes, then the page is told. */
function back() {
	browser.entries.pop();
	browser.page.state = browser.entries.at(-1) ?? {};
	fire('popstate');
}

const escape = () => fire('keydown', { key: 'Escape' });

/** How many listeners the module is holding the page's keys through. */
const listening = () => browser.handlers.get('popstate')?.size ?? 0;

globalThis.window = {
	addEventListener: (type: string, handler: (event: unknown) => void) => {
		if (!browser.handlers.has(type)) browser.handlers.set(type, new Set());
		browser.handlers.get(type)?.add(handler);
	},
	removeEventListener: (type: string, handler: (event: unknown) => void) => {
		browser.handlers.get(type)?.delete(handler);
	}
} as unknown as Window & typeof globalThis;

globalThis.history = { back } as unknown as History;

let module: typeof import('$lib/overlay');

beforeEach(async () => {
	browser.page.state = {};
	browser.entries = [{}];
	browser.handlers.clear();
	browser.unmounts = [];
	// The stack belongs to the tab, so each test gets its own copy.
	vi.resetModules();
	module = await import('$lib/overlay');
});

/** One overlay and a record of every time it was told to close. */
function make(name: keyof App.PageState, onescape?: () => void) {
	const closed: number[] = [];
	const it = module.overlay({ name, close: () => closed.push(1), onescape });
	return { it, closed };
}

test('the same overlay raised twice costs one back press', () => {
	const sheet = make('sheet');
	sheet.it.raise();
	// A second poster tapped while the sheet is still up is the same sheet.
	sheet.it.raise();
	expect(browser.entries).toHaveLength(2);

	back();
	expect(sheet.closed).toHaveLength(1);
	expect(browser.page.state.sheet).toBeUndefined();
});

test('one back press closes the top entry and everything raised over it', () => {
	const tips: number[] = [];
	const sheet = make('sheet');
	const popover = module.overlay({ close: () => tips.push(1) });
	sheet.it.raise();
	// A popover inside the sheet takes no entry of its own: one of those would
	// cost a back press to close a tooltip.
	popover.raise();
	expect(browser.entries).toHaveLength(2);

	back();
	expect(tips).toHaveLength(1);
	expect(sheet.closed).toHaveLength(1);
	expect(listening()).toBe(0);
});

test('a buried overlay is dropped where it stands, and its flag with it', () => {
	const picking = make('picking');
	const sheet = make('sheet');
	picking.it.raise();
	sheet.it.raise();

	// Its own entry is under the sheet's and out of reach, so the flag goes from
	// the entry that is current rather than through a pop.
	expect(picking.it.lower()).toBe(false);
	expect(picking.closed).toHaveLength(1);
	expect(browser.entries).toHaveLength(3);
	expect(browser.page.state).toEqual({ sheet: true });

	// And the sheet still owns the top entry.
	back();
	expect(sheet.closed).toHaveLength(1);
});

test('lower() goes through the entry, so it is not left for the next press', () => {
	const sheet = make('sheet');
	sheet.it.raise();
	expect(sheet.it.lower()).toBe(true);
	expect(browser.entries).toHaveLength(1);
	expect(sheet.closed).toHaveLength(1);
});

test('one that came up on its own account spends no entry, and Escape still ends it', () => {
	const bar = make('picking');
	// An adopted run's bar: there is no press of the reader's to spend.
	bar.it.raise(false);
	expect(browser.entries).toHaveLength(1);

	escape();
	expect(bar.closed).toHaveLength(1);
	expect(listening()).toBe(0);
});

test('one raised without an entry takes the reader press that follows', () => {
	const bar = make('picking');
	bar.it.raise(false);
	bar.it.raise();
	expect(browser.entries).toHaveLength(2);

	back();
	expect(bar.closed).toHaveLength(1);
});

test('only the top overlay hears Escape, and it may mean something else', () => {
	const dismissed: string[] = [];
	const picking = module.overlay({
		name: 'picking',
		close: () => dismissed.push('closed'),
		// A bar that is a running progress bar has nothing to dismiss.
		onescape: () => dismissed.push('refused')
	});
	const sheet = make('sheet');
	picking.raise();
	sheet.it.raise();

	escape();
	expect(sheet.closed).toHaveLength(1);
	expect(dismissed).toEqual([]);

	escape();
	expect(dismissed).toEqual(['refused']);
});

test('a page that is navigated away from leaves nothing listening', () => {
	const sheet = make('sheet');
	sheet.it.raise();
	// Its entry is left behind under the destination where nothing can reach
	// it, so the overlay is dropped rather than closed.
	for (const unmount of browser.unmounts) unmount();
	expect(listening()).toBe(0);
	expect(sheet.closed).toHaveLength(0);

	// And a look still in flight when the page went does not put it back up.
	sheet.it.raise();
	expect(listening()).toBe(0);
});
