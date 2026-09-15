<script lang="ts">
	import { page } from '$app/state';
	import EventRow from '$lib/components/EventRow.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import Page from '$lib/components/Page.svelte';
	import Select from '$lib/components/Select.svelte';
	import ToolbarMenu from '$lib/components/ToolbarMenu.svelte';
	import TitleSheet from '$lib/components/TitleSheet.svelte';
	import { Snapshot } from '$lib/activity.svelte';
	import { count, CUSTOM, key, said, searchable, SPANS, type Event } from '$lib/events';
	import { button, control, quiet, radius } from '$lib/controls';
	import { History } from '$lib/history.svelte';
	import type { Card } from '$lib/library';
	import { Recheck } from '$lib/recheck.svelte';
	import { matches, terms } from '$lib/search';
	import { whenNear } from '$lib/reveal';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// The window and every read that changes it. The page draws what it holds.
	// Read once at construction; everything after is the class's.
	// svelte-ignore state_referenced_locally
	const history = new History(data.history, {
		// A different list: old open ids and depth mean nothing in it.
		onfresh: () => {
			open = {};
			fromTheTop();
		}
	});

	// Only an admin may run anything, so only an admin is offered the buttons.
	const admin = $derived(page.data.user?.role === 'admin');

	// A poster opens the library's own sheet, runs and all: the same sheet should
	// not offer less for having been opened from here.
	let sheet: TitleSheet;
	// An open sheet is a reason to keep asking whether a run may start.
	let sheetUp = $state(false);

	// What the service is doing, seeded from the load so the sheet's buttons have
	// an answer before the first look. Only the re-check reads it here.
	// svelte-ignore state_referenced_locally
	const snapshot = new Snapshot(data.activity);

	const recheck: Recheck = new Recheck(snapshot, {
		asking: () => sheetUp,
		// A run wrote verdicts, and every one of them is a line here.
		onwritten: () => history.refresh(),
		// The sheet shows what the run came to in its own panel, and one already
		// shut has nowhere on this page to show it.
		ondone: () => {}
	});

	function look(card: Card) {
		sheetUp = true;
		sheet.open(card);
		// Whether a run can start is the service's to answer.
		recheck.prod();
	}

	// The sheet has gone; a run it started carries on without it.
	function sheetShut() {
		sheetUp = false;
		recheck.sheetShut();
	}

	let filter = $state('all');
	$effect(() => {
		const requested = page.url.searchParams.get('filter');
		filter = FILTERS.some((option) => option.value === requested) ? requested! : 'all';
	});

	const FILTERS: { value: string; label: string; kinds: string[] }[] = [
		{ value: 'all', label: 'All', kinds: [] },
		{ value: 'rewrites', label: 'Rewrites', kinds: ['modified', 'pending'] },
		// Not Failures: the tab holds `deferred` too, which is Waiting.
		{ value: 'issues', label: 'Issues', kinds: ['failed', 'deferred'] },
		{
			value: 'runs',
			label: 'Runs',
			kinds: ['sweep', 'recheck', 'webhook', 'config', 'settings', 'paused', 'resumed']
		}
	];

	const periods = [
		...SPANS.map((span) => ({
			value: span.value,
			label: span.value === 'all' ? 'All time' : span.said.replace('the last', 'Last')
		})),
		{ value: CUSTOM, label: 'Custom range' }
	];
	const typeLabel = $derived(FILTERS.find((option) => option.value === filter)?.label ?? 'All');
	const periodLabel = $derived(
		periods.find((option) => option.value === history.span)?.label ?? 'All time'
	);

	const kinds = $derived(FILTERS.find((option) => option.value === filter)?.kinds ?? []);

	let search = $state('');
	// The words a line has to answer to. Empty is no search.
	const needle = $derived(terms(search));

	// The library's name for what a line is about, where the page has the card.
	function titleOf(entry: Event): string {
		return (entry.title && history.titles[entry.title]?.name) || '';
	}

	// `at` is the position, which a config line is diffed against; `id` is what
	// an open panel is remembered by. Position alone moved open rows as lines
	// arrived.
	const rows = $derived(
		history.entries
			.map((entry, at) => ({ entry, at, id: key(entry) }))
			.filter(
				({ entry }) =>
					(!kinds.length || kinds.includes(entry.event)) &&
					// After the kinds, the cheaper cut of the two.
					(!needle.length || matches(searchable(entry, titleOf(entry)), needle))
			)
	);

	// Whether the page is holding lines back, which changes what the footer counts
	// and what a press reaches for.
	const cutting = $derived(!!kinds.length || !!needle.length);

	// What to loosen when nothing matches. The search cuts harder of the two.
	const loosen = $derived(
		needle.length
			? { sentence: 'Try fewer words.', phrase: 'try fewer words' }
			: { sentence: 'Widen the filter.', phrase: 'widen the filter' }
	);

	// How many rows go in before the reader scrolls: two phone screenfuls. A
	// hundred lines is 1430 DOM nodes, which content-visibility keeps off the
	// paint but not out of layout.
	const PAGE = 25;
	let limit = $state(PAGE);
	const shown = $derived(rows.slice(0, limit));

	// A different filter is a different list, so it starts from the top. In the
	// handler, since an effect ran a frame late.
	function fromTheTop() {
		limit = PAGE;
	}

	function choose(next: string) {
		filter = next;
		fromTheTop();
	}

	// Bounded, since each miss is a fetch and a file can go back years.
	const PAGES_PER_PRESS = 10;

	// A page the cuts then hide is a press that did nothing, so under a cut one
	// press keeps reading until something lands.
	async function reachBack() {
		const had = rows.length;
		for (let page = 0; page < (cutting ? PAGES_PER_PRESS : 1); page += 1) {
			await history.more();
			if (rows.length > had || history.cursor === null || history.failure) break;
		}
	}

	let open = $state<Record<string, boolean>>({});

	function toggle(id: string) {
		open = { ...open, [id]: !open[id] };
	}

	// The previous config line, which only the page can find. Older is further
	// down.
	function earlierConfig(id: number): Event | undefined {
		const { entries } = history;
		for (let at = id + 1; at < entries.length; at += 1) {
			if (entries[at].event === 'config') return entries[at];
		}
		return undefined;
	}
</script>

<!-- wide: release names and paths wrapped onto a third line at 42rem. -->
<Page wide lead="Every sweep, import and rewrite, newest first.">
	<!-- The same hierarchy as the library: search, two focused menus, then the
	     loaded results and the action that refreshes them. -->
	<div class="mt-6 flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
		<input
			bind:value={search}
			oninput={fromTheTop}
			type="search"
			placeholder="Find a file, rule or run"
			aria-label="Find in these events"
			class={`${control} ${radius} min-w-0 border border-line-strong bg-field px-3 text-base placeholder:text-faint sm:w-52 sm:flex-1 sm:text-[13px]`}
		/>
		<div class="flex min-w-0 items-center gap-2 sm:gap-3">
			<ToolbarMenu label="Event type" icon="sliders" detail={typeLabel} active={filter !== 'all'}>
				<div class="grid grid-cols-2 gap-2" role="group" aria-label="Filter event types">
					{#each FILTERS as option (option.value)}
						<button
							type="button"
							aria-pressed={filter === option.value}
							onclick={() => choose(option.value)}
							class={`${control} rounded-lg border px-3 text-left text-[13px] ${filter === option.value ? 'border-line-control bg-sunken font-semibold text-fg' : 'border-line text-dim hover:bg-sunken'}`}
						>
							{option.label}
						</button>
					{/each}
				</div>
			</ToolbarMenu>
			<ToolbarMenu label="Time range" icon="calendar" detail={periodLabel} active={history.bounded}>
				<div class="flex">
					<Select
						grow
						label="Time range"
						options={periods}
						value={history.span}
						onchange={(value) => history.look(value)}
					/>
				</div>
				{#if history.span === CUSTOM}
					<div class="mt-3 space-y-3">
						{#each [{ id: 'from', label: 'From' }, { id: 'to', label: 'To' }] as end (end.id)}
							<label class="block min-w-0">
								<span class="mb-1.5 block text-[12px] font-medium text-dim">{end.label}</span>
								<input
									type="datetime-local"
									value={end.id === 'from' ? history.from : history.to}
									onchange={(picked) => {
										const value = picked.currentTarget.value;
										if (end.id === 'from') history.from = value;
										else history.to = value;
										history.refresh();
									}}
									class={`${control} ${radius} w-full min-w-0 border border-line-strong bg-field px-2 text-base sm:text-[13px]`}
								/>
							</label>
						{/each}
						<button
							type="button"
							onclick={() => {
								history.from = '';
								history.to = '';
								history.refresh();
							}}
							disabled={!history.from && !history.to}
							class={quiet}>Clear dates</button
						>
					</div>
				{/if}
			</ToolbarMenu>
		</div>
	</div>
	<div class="mt-2 flex min-h-11 min-w-0 items-center gap-2 text-[12px] text-dim sm:min-h-10">
		<p class="min-w-0 flex-1">
			<span class="tabular-nums">{count(rows.length, 'event')}</span>{cutting
				? ' matched'
				: ' loaded'}
			{#if filter !== 'all'}
				{` · ${typeLabel}`}{/if}
			{#if history.bounded}
				{` · ${periodLabel}`}{/if}
		</p>
		<button
			type="button"
			onclick={() => history.refresh()}
			disabled={history.busy}
			class={`-mr-2.5 flex ${control} ${radius} flex-none items-center gap-1.5 px-2.5 text-[13px] font-medium hover:bg-raised hover:text-fg disabled:opacity-(--disabled)`}
		>
			<Glyph name="refresh" />
			{history.busy ? 'Reading…' : 'Refresh'}
		</button>
	</div>

	{#if history.failure}
		<p class="mt-5 text-sm text-danger">{history.failure}</p>
	{/if}

	{#if !rows.length}
		<p class="mt-6 rounded-xl border border-line bg-raised px-4 py-6 text-sm text-dim">
			{#if history.entries.length}
				<!-- The kind filter or the search hid everything loaded. Whether there
				     is more is the cursor's answer. -->
				Nothing loaded matches that. {history.cursor === null
					? loosen.sentence
					: `Look further back, or ${loosen.phrase}.`}
			{:else if history.bounded}
				<!-- Definite: the window was read to its end. -->
				Nothing in {said(history.span)}.
			{:else}
				Nothing has happened yet.
			{/if}
		</p>
	{:else}
		<ol
			aria-label="Events"
			class="mt-3 divide-y divide-line overflow-hidden rounded-xl border border-line bg-raised"
		>
			{#each shown as { entry, at, id } (id)}
				<!-- Rows off screen are skipped whole. 6rem is the guess for an unseen
				     row. -->
				<li
					class="px-4 py-3 transition-colors [contain-intrinsic-size:auto_6rem] [content-visibility:auto] hover:bg-sunken/50"
				>
					<EventRow
						{entry}
						id={`event-${at}`}
						open={!!open[id]}
						before={entry.event === 'config' ? earlierConfig(at) : undefined}
						card={entry.title ? history.titles[entry.title] : undefined}
						onopen={look}
						ontoggle={() => toggle(id)}
					/>
				</li>
			{/each}
		</ol>
		<!-- Only while loaded rows remain. Reaching further back is a press. -->
		{#if limit < rows.length}
			<div use:whenNear={() => (limit = Math.min(limit + PAGE, rows.length))} class="h-px"></div>
		{/if}
	{/if}

	<div class="mt-4 flex items-center gap-3">
		{#if history.cursor !== null}
			<!-- Says what it does. Under a cut it reads past pages that hold nothing. -->
			<button onclick={reachBack} disabled={history.busy} class={button}>
				{history.busy ? 'Loading…' : cutting ? 'Look further back' : 'Load more'}
			</button>
		{/if}
		<!-- What matched leads, and what was read is the number the presses move.
		     The window was the service's, so "nothing earlier" must say which. No
		     count when nothing is loaded, the sentence above already said it. -->
		{#if history.entries.length}
			<p class="text-[12px] text-faint">
				{cutting
					? `${rows.length} found in ${count(history.entries.length, 'event')} read`
					: `${count(history.entries.length, 'event')} loaded`}{history.cursor === null
					? history.bounded
						? `, nothing earlier in ${said(history.span)}`
						: ', nothing earlier'
					: ''}
			</p>
		{/if}
	</div>
</Page>

<!-- The library's own sheet, runs and all. -->
<TitleSheet bind:this={sheet} runner={admin ? recheck.runner : undefined} onshut={sheetShut} />
