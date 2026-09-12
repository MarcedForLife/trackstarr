<script lang="ts">
	import { onDestroy } from 'svelte';
	import { flip } from 'svelte/animate';
	import { cubicOut } from 'svelte/easing';
	import { page } from '$app/state';
	import { Snapshot } from '$lib/activity.svelte';
	import { refusalText } from '$lib/api';
	import { setHold } from '$lib/chrome.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import Page from '$lib/components/Page.svelte';
	import PosterCard from '$lib/components/PosterCard.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import Select from '$lib/components/Select.svelte';
	import SelectionBar from '$lib/components/SelectionBar.svelte';
	import TitleSheet from '$lib/components/TitleSheet.svelte';
	import VerdictChips from '$lib/components/VerdictChips.svelte';
	import { control, noteBox, primary, radius } from '$lib/controls';
	import { display, setScale, type Scale } from '$lib/display.svelte';
	import { tiltField } from '$lib/field';
	import {
		getShelf,
		kindLabel,
		kindName,
		verdictLabel,
		type Card,
		type Kind,
		type Verdict
	} from '$lib/library';
	import { moving } from '$lib/motion.svelte';
	import { FLOW, order, SORTS, type Flow, type Sort } from '$lib/order.svelte';
	import { poll } from '$lib/poll';
	import { Recheck } from '$lib/recheck.svelte';
	import { whenNear } from '$lib/reveal';
	import { startSweep } from '$lib/runs';
	import { Selection } from '$lib/selection.svelte';
	import {
		elsewhere,
		everything,
		headlines,
		kindsOn,
		ofKind,
		otherKinds,
		sift,
		tally,
		waiting
	} from '$lib/shelfview';
	import { told } from '$lib/stream';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Seeded from the loader, written over by the poll while a run rewrites the
	// verdicts. A refresh costs one request rather than a whole loader.
	let shelf = $derived(data.shelf);

	// Which verdicts the grid is cut to, empty being all. Opens on this browser's
	// default and changes for the visit, not for good, like the order below.
	let filters = $state<Verdict[]>(display.filters);

	// Films or series, empty being both. For the visit, like the search: a cut
	// this coarse is one press to undo and is named on screen while it holds.
	let kind = $state<Kind>('');

	let sort = $state<Sort>(order.grid);
	// Which way that order runs: the reader turning an order over.
	let flow = $state<Flow>(order.gridFlow);
	let search = $state('');

	const needle = $derived(search.trim().toLowerCase());

	// Everything $lib/shelfview needs to cut the shelf.
	const view = $derived({ filters, hidden: display.hidden, kind, needle, sort, flow });

	// How many posters go in before the reader scrolls. A whole library at once
	// is a second or two of layout before the page appears.
	const PAGE = 36;

	let limit = $state(PAGE);

	// A different filter, search or order is a different list, so it starts from
	// the top. Said by each handler rather than an effect, which ran a frame late.
	function fromTheTop() {
		limit = PAGE;
	}

	// A new order arrives the right way up.
	function reorder(next: Sort) {
		sort = next;
		flow = FLOW[next];
		fromTheTop();
	}

	function turn() {
		flow = flow === 'desc' ? 'asc' : 'desc';
		fromTheTop();
	}

	function filter(next: Verdict[]) {
		filters = next;
		fromTheTop();
	}

	// One state only, for the "Found under" buttons.
	const only = (state: Verdict) => filter([state]);

	function pickKind(next: Kind) {
		kind = next;
		// A verdict the new kind holds none of loses its chip below, which would
		// leave an empty grid with nothing left to unpress.
		const left = tally(shelf.titles.filter((card) => ofKind(card, next)));
		filters = filters.filter((state) => left[state]);
		fromTheTop();
	}

	// Only the kinds the shelf holds, so a Radarr-only install is offered no
	// control rather than a dead Series segment.
	const kinds = $derived(kindsOn(shelf.titles));

	// All is the empty cut, as it is on the chips above.
	const kindOptions = $derived([
		{ value: '', label: 'All' },
		...kinds.map((each) => ({ value: each, label: kindLabel(each) }))
	]);

	// One of the "Found under" buttons: a place the search did land, in the shape
	// of the chips above without their colour.
	const hint =
		'rounded-full border border-line px-2.5 py-0.5 font-medium text-fg transition-colors hover:bg-raised';

	// Single letters, beside the search box; the group carries the word.
	const SIZES = [
		{ value: 'small', label: 'S' },
		{ value: 'medium', label: 'M' },
		{ value: 'large', label: 'L' }
	];

	const shown = $derived(sift(shelf.titles, view));
	const grid = $derived(shown.slice(0, limit));

	// How long a card slides to its new place when the shelf is re-read.
	const SLIDE_MS = 320;

	// Most cards that slide at once; more is a wall of movement, and a stutter on
	// a phone.
	const SLIDE_MAX = 150;

	// The grid re-sorts under the reader as a sweep rewrites verdicts, and a
	// moved poster slides to say so. Whether it may move comes from $lib/motion.
	const slide = $derived({
		duration: moving() && grid.length <= SLIDE_MAX ? SLIDE_MS : 0,
		easing: cubicOut
	});

	// How many titles each verdict holds, and how many All stands for. Both count
	// the kind on screen, so a chip never promises titles the type filter then
	// withholds.
	const inKind = $derived(kind ? shelf.titles.filter((card) => ofKind(card, kind)) : shelf.titles);
	const counts = $derived(tally(inKind));
	const allCount = $derived(everything(inKind, display.hidden));

	// The whole library's headlines, for the wall below: that is the shelf's
	// state rather than the filter's.
	const shelfCounts = $derived(headlines(shelf.titles));

	// What All promises, naming the setting that narrows it.
	const allHint = $derived.by(() => {
		const noun = kind ? kindName(kind).toLowerCase() : 'title';
		if (!display.hidden.length) return `Every ${noun} in the library, in any state.`;
		const kept = display.hidden.map(verdictLabel).join(' and ');
		return `Every ${noun} except the ${kept} ones Appearance hides. Their own chips still reach them.`;
	});

	// Where a failed search would have landed without the filters. Counted only
	// once the list is empty, since each walks the whole shelf.
	const found = $derived(shown.length ? [] : elsewhere(shelf.titles, view));
	const foundKinds = $derived(shown.length ? [] : otherKinds(shelf.titles, view));

	// The sheet a poster raises.
	let sheet: TitleSheet;
	// Whether it is up and which title it holds: an open sheet is a reason to
	// keep polling, and a run started from it has a title to refresh.
	let sheetUp = $state(false);
	let sheetId = $state<string | null>(null);

	function look(card: Card) {
		sheetUp = true;
		sheetId = card.id;
		sheet.open(card);
		// Whether a run can start is the service's to answer.
		recheck.prod();
	}

	// The sheet has gone. A run still going carries on in the bar behind it.
	function sheetShut() {
		sheetUp = false;
		recheck.sheetShut();
	}

	// Picking titles and running them: what the service allows is $lib/recheck,
	// which posters are ticked is $lib/selection.

	const admin = $derived(page.data.user?.role === 'admin');

	let bar: SelectionBar;

	// The selection first: the Recheck arms its poller in its constructor and
	// reads `asking()`, which is this.
	const selection: Selection = new Selection({
		recheck: (): Recheck => recheck,
		// The whole cut, not the part laid out so far.
		showing: () => shown,
		fade: () => bar.fade(),
		stay: () => bar.stay(),
		dismiss: () => bar.dismiss()
	});

	// What the service is doing, seeded from the load so the run controls have an
	// answer before the first look. Only the re-check reads it here.
	// svelte-ignore state_referenced_locally
	const snapshot = new Snapshot(data.activity);

	const recheck: Recheck = new Recheck(snapshot, {
		// A selection and an open sheet both need to know whether a run may start.
		asking: () => selection.picking || sheetUp,
		onwritten: () => posters.now(),
		// Not while the sheet is up; it shows the receipt in its own panel.
		ondone: () => {
			if (!sheetUp) selection.showReceipt();
		}
	});

	// Hold the tab bar while this page's own bar is up, so the two never slide in
	// opposite directions. Here because an effect only exists during init. See
	// $lib/chrome.
	$effect(() => {
		setHold(selection.barUp);
		return () => setHold(false);
	});

	// How often the grid is re-read under a run. Slower than the strip: the answer
	// is a whole shelf.
	const SHELF_MS = 10000;

	// How often with nothing running, for a webhook import that began and ended
	// between two polls.
	const IDLE_SHELF_MS = 120000;

	/**
	 * Re-read the shelf and the sheet over it. One request rather than
	 * invalidateAll(), which re-ran every loader, reset page.state under the bar's
	 * history entry, and replaced the route with +error.svelte on failure.
	 */
	async function restock() {
		try {
			shelf = await getShelf();
			// The sheet is still showing the verdicts the run replaced. Not awaited.
			if (sheetUp && sheetId) sheet?.reload(shelf.titles.find((card) => card.id === sheetId));
		} catch {
			/* the posters on screen are still the last thing anyone knew */
		}
	}

	// The grid on its own clock, so a title already processed does not go on
	// reading Pending for the length of a walk.
	const posters = poll({
		ask: restock,
		pace: () => (recheck.walking ? told(SHELF_MS) : IDLE_SHELF_MS),
		gap: SHELF_MS,
		kinds: ['library']
	});

	onDestroy(posters.stop);

	// The wall of Unknown a fresh install or a cleared cache opens to, and the
	// press that clears it.

	const wall = $derived(waiting(shelfCounts, shelf.titles.length));

	// Only a sweep clears it; somebody else's re-check does not.
	const sweeping = $derived(recheck.otherRun?.kind === 'sweep');

	let planning = $state(false);
	let planRefusal = $state('');

	// What the line offers beside the news. A viewer is told nothing they cannot
	// act on.
	const clearing = $derived(
		sweeping ? 'A sweep is filling them in now.' : admin ? recheck.refuses : ''
	);

	// Plan rather than Process: a press offered in passing should find out, not
	// rewrite.
	async function plan() {
		planning = true;
		planRefusal = '';
		try {
			await startSweep('report');
		} catch (error) {
			planRefusal = refusalText(error);
		} finally {
			planning = false;
			// A refusal is nearly always a sweep that started since the last look,
			// and the line above says so better than a red sentence.
			recheck.prod();
		}
	}
</script>

<Page
	wide
	lead="Every title Radarr and Sonarr know about, with the last sweep's verdict on its files."
>
	{#if !shelf.current}
		<p class={`mt-5 ${noteBox} text-dim`}>
			The rules have changed since these verdicts, so the next sweep will redo them.
		</p>
	{/if}
	{#if !shelf.complete}
		<p class="mt-3 text-[12.5px] text-danger">
			Radarr or Sonarr could not be listed, so some titles are missing from this grid, not from the
			library.
		</p>
	{/if}
	{#if wall}
		<!-- With the shelf's other notes: this is the library's state, not the
		     filter's. -->
		<div
			class={`${
				// The lead's own gap when this is the only thing under it.
				shelf.current && shelf.complete ? 'mt-5' : 'mt-3'
			} ${noteBox} flex flex-wrap items-center gap-x-3 gap-y-2 text-dim`}
		>
			<p class="min-w-0 flex-1">
				{wall.titles === wall.judgeable
					? 'No title has a verdict yet, so every poster reads Unknown.'
					: `${wall.titles.toLocaleString()} of ${wall.judgeable.toLocaleString()} titles have no verdict yet, so their posters read Unknown.`}
				{clearing}
			</p>
			{#if !clearing && admin}
				<button type="button" onclick={plan} disabled={planning} class={`flex-none ${primary}`}>
					<Glyph name="doc" />
					{planning ? 'Planning…' : 'Plan now'}
				</button>
			{/if}
		</div>
		{#if planRefusal && !clearing}
			<!-- Only until the snapshot lands and the box above says why. -->
			<p class="mt-2 text-[12.5px] text-danger">{planRefusal}</p>
		{/if}
	{/if}

	<!-- The verdicts first: this page's own navigation. The same row Appearance
	     offers for the default. -->
	<div class="mt-6">
		<VerdictChips
			label="Filter the library"
			chosen={filters}
			{counts}
			{allHint}
			{allCount}
			onchange={filter}
		/>
	</div>

	<!-- How the grid is shown: what the reader is looking for on one line, how it
	     is laid out on the other. The two sit side by side once there is room and
	     wrap back apart rather than squeezing the search past its placeholder. -->
	<div
		class="mt-2.5 flex min-w-0 flex-col gap-2 sm:mt-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3"
	>
		<!-- The box keeps room for its placeholder, so a shelf with folders in it as
		     well drops the types to their own line rather than crushing the search. -->
		<div class="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
			<input
				bind:value={search}
				oninput={fromTheTop}
				type="search"
				placeholder="Find a title"
				aria-label="Find a title"
				class={`${control} ${radius} min-w-36 flex-1 border border-line-strong bg-field px-3 text-base placeholder:text-faint sm:max-w-52 sm:text-[13px]`}
			/>
			{#if kinds.length > 1}
				<!-- Beside the search, not among the verdict chips: the two of them are
				     what the reader is after, where a verdict is what state it is in. -->
				<div class="flex-none">
					<Segmented
						label="Filter by type"
						options={kindOptions}
						value={kind}
						onchange={(next) => pickKind(next as Kind)}
					/>
				</div>
			{/if}
		</div>
		<div class="flex min-w-0 items-center gap-2 sm:gap-3">
			<!-- The menu takes whatever the fixed controls leave, so the row ends
			     at the screen's edge. -->
			<Select
				grow
				label="Order"
				options={SORTS}
				value={sort}
				onchange={(next) => reorder(next as Sort)}
			/>
			<!-- The arrow is the state; the label is what pressing does. -->
			<button
				type="button"
				onclick={turn}
				aria-label={flow === 'desc' ? 'Sort ascending' : 'Sort descending'}
				title={flow === 'desc' ? 'Sort ascending' : 'Sort descending'}
				class={`flex ${control} ${radius} flex-none items-center justify-center border border-line-strong bg-field px-2.5 text-dim transition-colors hover:text-fg`}
			>
				<span
					class={`flex transition-transform duration-200 ${flow === 'desc' ? 'rotate-90' : '-rotate-90'}`}
				>
					<Glyph name="arrow" />
				</span>
			</button>
			<div class="flex-none">
				<Segmented
					label="Poster size"
					options={SIZES}
					value={display.scale}
					tight
					onchange={(value) => setScale(value as Scale)}
				/>
			</div>
			{#if admin}
				<!-- The word only where there is room; the tick is the mark the
				     posters grow. -->
				<button
					type="button"
					aria-pressed={selection.picking}
					onclick={() => (selection.picking ? bar.dismiss() : selection.enter())}
					title={selection.picking ? 'Stop selecting titles' : 'Select titles to plan or process'}
					class={`flex ${control} ${radius} flex-none items-center gap-1.5 border px-2.5 text-[13px] font-medium transition-colors sm:px-3 ${
						selection.picking
							? 'border-accent-fill/50 bg-accent-fill/12 text-fg'
							: 'border-line-strong bg-field text-dim'
					}`}
				>
					<svg
						viewBox="0 0 14 14"
						width="12"
						height="12"
						fill="none"
						stroke="currentColor"
						stroke-width="1.7"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
						class="flex-none"
					>
						<path d="M2 7.4 5.2 10.6 12 3.6" />
					</svg>
					<span class="hidden sm:inline">{selection.picking ? 'Cancel' : 'Select'}</span>
				</button>
			{/if}
		</div>
	</div>

	{#if !shelf.titles.length}
		<p class="mt-8 text-sm text-dim">
			Nothing to show yet. Connect Radarr or Sonarr, then run a sweep to fill this grid.
		</p>
	{:else if !shown.length}
		<p class="mt-8 text-sm text-dim">No title matches that.</p>
		{#if found.length || foundKinds.length}
			<!-- Named and tappable rather than described. The kind first: it is the
			     coarser cut, and pressing it is what makes the verdicts below real. -->
			<p class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-dim">
				<span>Found under</span>
				{#each foundKinds as hit (hit.kind)}
					<button type="button" onclick={() => pickKind(hit.kind)} class={hint}>
						{kindLabel(hit.kind)}
						<span class="text-faint tabular-nums">{hit.count.toLocaleString()}</span>
					</button>
				{/each}
				{#each found as hit (hit.state)}
					<button type="button" onclick={() => only(hit.state)} class={hint}>
						{verdictLabel(hit.state)}
						<span class="text-faint tabular-nums">{hit.count.toLocaleString()}</span>
					</button>
				{/each}
			</p>
		{/if}
	{:else}
		<ul
			use:tiltField={{
				active: moving,
				reach: () => display.reach,
				strength: () => display.strength,
				lights: () => display.lights
			}}
			style={`--scale: ${display.tile}`}
			class="shelf mt-6 grid gap-x-3 gap-y-5"
		>
			{#each grid as card (card.id)}
				<li class="tile" animate:flip={slide}>
					<!-- The same tap means open or toggle; the grid decides which. -->
					<PosterCard
						{card}
						selected={selection.picking ? selection.picked.has(card.id) : undefined}
						onopen={(title) => (selection.picking ? selection.toggle(title) : look(title))}
						onhold={admin ? (title) => selection.holdPick(title) : undefined}
					/>
				</li>
			{/each}
		</ul>
		{#if limit < shown.length}
			<!-- The next page arrives as this comes within a screen of the bottom. -->
			<div use:whenNear={() => (limit = Math.min(limit + PAGE, shown.length))} class="h-px"></div>
		{/if}
		<p class="mt-6 text-[12px] text-faint">
			{shown.length === shelf.titles.length
				? `${shelf.titles.length} titles`
				: `${shown.length} of ${shelf.titles.length} titles`} · {shelf.swept.toLocaleString()} files judged
		</p>
	{/if}

	<!-- Last, so a screen reader meets the posters before what acts on them. -->
	<SelectionBar
		bind:this={bar}
		up={selection.barUp}
		picking={selection.picking}
		run={recheck.running}
		stopping={recheck.stopping}
		picked={selection.picked.size}
		selectable={!!shown.length}
		mayRewrite={recheck.mayRewrite}
		refuses={recheck.refuses}
		warming={recheck.warming}
		busy={recheck.busy}
		refusal={recheck.fromSheet ? '' : recheck.refusal}
		offline={recheck.offline}
		done={recheck.line('the selected titles')}
		onall={() => selection.all()}
		onclear={() => selection.clear()}
		onrun={(mode) => recheck.start(selection.ids, mode)}
		onstop={() => recheck.stop()}
		ondismiss={() => selection.leave()}
	/>
</Page>

<!-- A viewer starts nothing, so no run panel. -->
<TitleSheet bind:this={sheet} runner={admin ? recheck.runner : undefined} onshut={sheetShut} />

<style>
	/* As many columns as fit: the size setting multiplies the tile and the
	   window decides how many go across. The tile itself grows with the
	   viewport, 110px on a phone to 136px, then clamps so an ultrawide gets a
	   laptop's posters rather than a wall of stamps. */
	.shelf {
		--tile: calc(var(--scale, 1) * clamp(6.875rem, 5rem + 5vw, 8.5rem));
		grid-template-columns: repeat(auto-fill, minmax(var(--tile), 1fr));
		/* Or a re-sort carries the page to wherever the anchored tile landed. */
		overflow-anchor: none;
	}

	/* Tiles the reader has not reached are not styled, laid out or painted.
	   The size hint follows the size setting, or the scrollbar would describe
	   a grid that is no longer there. */
	.tile {
		content-visibility: auto;
		contain-intrinsic-size: auto calc(var(--tile) * 1.5 + 34px);
		/* Room inside the paint-contained box for a turned card's keystone,
		   handed back to the layout so the grid's pitch is unchanged. Six is
		   half the gutter, so cells meet and never overlap. Always there rather
		   than under `:has(.is-near)`, which re-ran invalidation every frame
		   and cost 1.3ms on a 400 poster grid. */
		padding: 6px;
		margin: -6px;
	}

	/* The raised card is scaled and shadowed, which six pixels cannot hold, so
	   containment comes off it. Affordable because there is only ever one and
	   it changes on a crossing, not every frame. */
	.tile:global(:has(.is-raised)) {
		content-visibility: visible;
	}
</style>
