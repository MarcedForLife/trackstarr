import { beforeEach, expect, test, vi } from 'vitest';
import type { Card } from '$lib/library';
import type { Recheck } from '$lib/recheck.svelte';
import { Selection } from '$lib/selection.svelte';

//: The bar's own fade, which is how long the drop is held back for.
const FADE = 160;

// The overlay is a history entry and two window listeners, none of which says
// anything about what the selection does. Held here so a test can be the back
// gesture itself: the pop closes the top entry, which is `close`.
const harness = vi.hoisted(() => ({
	raised: [] as boolean[],
	// Whether this overlay owns an entry to spend. False is one raised on its own
	// account — an adopted run's bar — which is dropped where it stands.
	entry: true,
	close: () => {},
	escape: () => {}
}));

vi.mock('$lib/overlay', () => ({
	overlay: (config: { close: () => void; onescape?: () => void }) => {
		harness.close = config.close;
		harness.escape = config.onescape ?? config.close;
		return {
			raise: (entry = true) => harness.raised.push(entry),
			// Through the entry, where there is one: the pop is what closes it, and
			// that lands a tick later. Anything else is closed on the spot.
			lower: () => {
				if (harness.entry) return true;
				config.close();
				return false;
			}
		};
	}
}));

/** The back gesture, or the hardware key: the entry goes, then the page hears. */
const back = () => harness.close();

// Enough of a window for the scroll restore to be observable.
let scrolledTo: number[];
globalThis.window = {
	scrollY: 0,
	scrollTo: (_x: number, y: number) => scrolledTo.push(y)
} as unknown as Window & typeof globalThis;
globalThis.requestAnimationFrame = ((run: () => void) => {
	run();
	return 0;
}) as typeof requestAnimationFrame;

const card = (id: string) => ({ id }) as Card;

let recheck: Recheck;
let reads: number;
let faded: number;
let stayed: number;
let dismissed: number;
let showing: Card[];

/** A selection over three posters, counting what it asks of the bar. */
function selecting(): Selection {
	return new Selection({
		recheck: () => {
			reads += 1;
			return recheck;
		},
		showing: () => showing,
		fade: () => faded,
		stay: () => (stayed += 1),
		dismiss: () => (dismissed += 1)
	});
}

beforeEach(() => {
	vi.useFakeTimers();
	harness.raised = [];
	harness.entry = true;
	reads = 0;
	scrolledTo = [];
	faded = FADE;
	stayed = 0;
	dismissed = 0;
	showing = [card('a'), card('b'), card('c')];
	window.scrollY = 0;
	// Only the four things the selection reads or writes on it.
	recheck = {
		running: null,
		summary: null,
		refusal: 'the last press was turned down',
		prod: () => {}
	} as unknown as Recheck;
});

test('making one reads nothing of the run controls it belongs to', () => {
	// The page makes this first, because the Recheck it is given arms a poller
	// inside its own constructor and asks the selection whether anything is being
	// picked as it does. Made the other way round, that read reached a const that
	// did not exist yet and took the whole page down with it.
	selecting();
	expect(reads).toBe(0);
});

test('the bar asked for again inside its own fade keeps what that press makes', () => {
	const selection = selecting();
	selection.enter();
	selection.toggle(card('a'));

	// Dismissed by a back gesture, which arrives with the bar at full opacity:
	// the fade starts here and the drop that clears the selection is 160ms out.
	back();
	expect(selection.picking).toBe(true);

	// Back into picking inside that window. Left alone the fade would leave the
	// bar invisible and refusing presses, and its clean-up is already on its way
	// to clear the selection this is about to make.
	selection.enter();
	expect(stayed).toBe(1);
	selection.toggle(card('b'));

	vi.advanceTimersByTime(FADE * 2);
	expect(selection.picking).toBe(true);
	expect([...selection.picked]).toEqual(['a', 'b']);
});

test('a dismissal that has nothing to fade drops what the bar was drawing at once', () => {
	// Zero is a fade that has already run, or a run keeping the bar up as its
	// readout while only the picking half is put away.
	faded = 0;
	const selection = selecting();
	selection.enter();
	selection.all();

	back();
	expect(selection.picking).toBe(false);
	expect(selection.picked.size).toBe(0);
});

test('a fade left to run clears the selection when it ends', () => {
	const selection = selecting();
	selection.enter();
	selection.toggle(card('a'));

	back();
	expect(selection.picked.has('a')).toBe(true);
	vi.advanceTimersByTime(FADE);
	expect(selection.picking).toBe(false);
	expect(selection.picked.size).toBe(0);
});

test('select all reaches the whole cut, not the part of it laid out', () => {
	const selection = selecting();
	selection.all();
	expect([...selection.picked]).toEqual(['a', 'b', 'c']);

	// A narrower filter is a different list, and all() answers the one on screen.
	showing = [card('b')];
	selection.all();
	expect([...selection.picked]).toEqual(['b']);
});

test('changing what is asked for takes the last refusal down with it', () => {
	const selection = selecting();
	for (const change of [
		() => selection.toggle(card('a')),
		() => selection.clear(),
		() => selection.all(),
		() => selection.holdPick(card('c'))
	]) {
		recheck.refusal = 'a sweep is already running';
		change();
		expect(recheck.refusal).toBe('');
	}
});

test('a tap ticks a title and taps it again to untick it', () => {
	const selection = selecting();
	selection.toggle(card('a'));
	selection.toggle(card('b'));
	selection.toggle(card('a'));
	expect([...selection.picked]).toEqual(['b']);
	expect(selection.ids).toEqual(['b']);
});

test('leaving puts back the scroll the entry was pushed at', () => {
	const selection = selecting();
	window.scrollY = 40;
	selection.enter();

	// Several screens down by the time the bar is worth dismissing.
	window.scrollY = 3200;
	selection.leave();
	// Not yet: the entry is spent through a pop, and the position goes back as
	// that pop lands rather than when it was asked for.
	expect(scrolledTo).toEqual([]);

	back();
	// In the frame after the pop, which still lands before that frame is
	// painted: SvelteKit's own popstate listener runs first and would simply
	// overwrite a scroll set from inside the handler.
	vi.advanceTimersByTime(FADE);
	expect(scrolledTo).toEqual([3200]);
});

test('a bar standing on no entry of its own is put away where it stands', () => {
	// An adopted run's receipt: nobody pressed for it, so there is nothing of the
	// reader's to spend and back stays the page's to answer.
	harness.entry = false;
	const selection = selecting();
	recheck.summary = { event: 'recheck' } as unknown as Recheck['summary'];
	selection.showReceipt();
	expect(harness.raised).toEqual([false]);
	expect(selection.barUp).toBe(true);

	selection.leave();
	vi.advanceTimersByTime(FADE);
	expect(recheck.summary).toBeNull();
	expect(selection.barUp).toBe(false);
});

test('Escape while a run is going is not a thing anyone means as stop watching', () => {
	const selection = selecting();
	selection.enter();
	recheck.running = { id: 'r1' } as unknown as Recheck['running'];

	harness.escape();
	expect(dismissed).toBe(0);

	// Finished, with its receipt still up: now the key has something to put away.
	recheck.running = null;
	recheck.summary = { event: 'recheck' } as unknown as Recheck['summary'];
	harness.escape();
	expect(dismissed).toBe(1);
});

test('the bar is up for a run or a receipt as well as for a selection', () => {
	const selection = selecting();
	expect(selection.barUp).toBe(false);

	selection.enter();
	expect(selection.barUp).toBe(true);

	back();
	vi.advanceTimersByTime(FADE);
	expect(selection.barUp).toBe(false);

	recheck.running = { id: 'r1' } as unknown as Recheck['running'];
	expect(selection.barUp).toBe(true);
});
