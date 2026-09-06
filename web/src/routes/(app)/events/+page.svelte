<script lang="ts">
	import EventRow from '$lib/components/EventRow.svelte';
	import { button } from '$lib/controls';
	import Page from '$lib/components/Page.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import TitleSheet from '$lib/components/TitleSheet.svelte';
	import { count, CUSTOM, dot, key, said, SPANS, type Event } from '$lib/events';
	import { control, quiet, radius } from '$lib/controls';
	import { History } from '$lib/history.svelte';
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

	// A poster opens the library's own sheet, with its Open in buttons.
	let sheet: TitleSheet;
	let filter = $state('all');

	const FILTERS: { value: string; label: string; kinds: string[] }[] = [
		{ value: 'all', label: 'All', kinds: [] },
		{ value: 'rewrites', label: 'Rewrites', kinds: ['fixed', 'would-fix'] },
		// Not Failures: the tab holds `deferred` too, which is Waiting.
		{ value: 'issues', label: 'Issues', kinds: ['failed', 'deferred'] },
		{
			value: 'runs',
			label: 'Runs',
			kinds: ['sweep', 'recheck', 'webhook', 'config', 'settings', 'paused', 'resumed']
		}
	];

	const kinds = $derived(FILTERS.find((option) => option.value === filter)?.kinds ?? []);
	// `at` is the position, which a config line is diffed against; `id` is what
	// an open panel is remembered by. Position alone moved open rows as lines
	// arrived.
	const rows = $derived(
		history.entries
			.map((entry, at) => ({ entry, at, id: key(entry) }))
			.filter(({ entry }) => !kinds.length || kinds.includes(entry.event))
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
<Page wide title="Events" lead="Every sweep, import and rewrite, newest first.">
	<!-- A grid, so Refresh is last in source (a button between the switches read
	     as a label on a phone) and still beside the first row from sm up. -->
	<div class="mt-6 grid gap-3 sm:grid-cols-[auto_1fr] sm:items-center">
		<div class="sm:col-start-1 sm:row-start-1">
			<Segmented options={FILTERS} value={filter} onchange={choose} />
		</div>
		<!-- Under the kinds: nine segments is more than a phone fits, and the two
		     are different questions. Custom range sits outside the track, set
		     apart by the gap; with it held Segmented hides its thumb on -1. -->
		<div class="flex min-w-0 items-center gap-2 sm:col-start-1 sm:row-start-2">
			<div class="min-w-0 flex-1 sm:flex-none">
				<Segmented
					options={SPANS}
					value={history.span}
					tight
					label="How far back"
					onchange={(chosen) => history.look(chosen)}
				/>
			</div>
			<button
				aria-pressed={history.span === CUSTOM}
				aria-label="A range you pick"
				onclick={() => history.look(history.span === CUSTOM ? 'all' : CUSTOM)}
				class={`${control} ${radius} flex-none border border-line-strong px-3 text-[13px] whitespace-nowrap transition-colors ${
					history.span === CUSTOM
						? 'bg-raised font-semibold text-fg shadow-sm'
						: 'bg-sunken font-medium text-dim hover:text-fg'
				}`}
			>
				Range
			</button>
		</div>
		{#if history.span === CUSTOM}
			<!-- Native pickers: the platform's wheel beats anything drawn here, and
			     the value is local wall-clock. -->
			<div
				class="flex flex-col gap-2 sm:col-start-1 sm:row-start-3 sm:flex-row sm:items-center sm:gap-3"
			>
				{#each [{ id: 'from', label: 'From' }, { id: 'to', label: 'To' }] as end (end.id)}
					<label class="flex min-w-0 flex-1 items-center gap-2.5 sm:flex-none">
						<span class="w-10 flex-none text-[13px] font-medium text-dim sm:w-auto">
							{end.label}
						</span>
						<input
							type="datetime-local"
							value={end.id === 'from' ? history.from : history.to}
							onchange={(picked) => {
								const value = picked.currentTarget.value;
								if (end.id === 'from') history.from = value;
								else history.to = value;
								history.refresh();
							}}
							class={`${control} ${radius} w-full min-w-0 border border-line-strong bg-field px-3 text-base sm:w-auto sm:text-[13px]`}
						/>
					</label>
				{/each}
				<!-- Both ends at once: an empty end is an open one, which the row
				     cannot otherwise reach, and Android's picker has no clear. -->
				<button
					onclick={() => {
						history.from = '';
						history.to = '';
						history.refresh();
					}}
					disabled={!history.from && !history.to}
					class={`${quiet} self-start sm:self-auto`}
				>
					Clear
				</button>
			</div>
		{/if}
		<button
			onclick={() => history.refresh()}
			disabled={history.busy}
			class="justify-self-start text-[13px] font-medium text-dim hover:text-fg disabled:opacity-50 sm:col-start-2 sm:row-start-1 sm:justify-self-end"
		>
			Refresh
		</button>
	</div>

	{#if history.failure}
		<p class="mt-5 text-sm text-danger">{history.failure}</p>
	{/if}

	{#if !rows.length}
		<p class="mt-8 text-sm text-dim">
			{#if history.entries.length}
				<!-- The kind filter hid everything loaded. Whether there is more is
				     the cursor's answer. -->
				Nothing loaded matches that. {history.cursor === null
					? 'Widen the filter.'
					: 'Load more, or widen the filter.'}
			{:else if history.bounded}
				<!-- Definite: the window was read to its end. -->
				Nothing in {said(history.span)}.
			{:else}
				Nothing has happened yet.
			{/if}
		</p>
	{:else}
		<!-- overflow-anchor: none, or scroll anchoring carries a tapped row out
		     from under the finger as it expands. -->
		<ol class="mt-6 [overflow-anchor:none]">
			{#each shown as { entry, at, id }, index (id)}
				<!-- Rows off screen are skipped whole. 5rem is the guess for an unseen
				     row. -->
				<li
					class="relative flex gap-3.5 pb-5 [contain-intrinsic-size:auto_5rem] [content-visibility:auto]"
				>
					<!-- The rail, stopped short on the last row. -->
					{#if index < shown.length - 1}
						<span aria-hidden="true" class="absolute top-3.5 bottom-0 left-[3.5px] w-px bg-line"
						></span>
					{/if}
					<span class={`relative mt-1.5 h-2 w-2 flex-none rounded-full ${dot(entry)}`}></span>
					<div class="min-w-0 flex-1">
						<EventRow
							{entry}
							id={`event-${at}`}
							open={!!open[id]}
							before={entry.event === 'config' ? earlierConfig(at) : undefined}
							card={entry.title ? history.titles[entry.title] : undefined}
							onopen={(chosen) => sheet.open(chosen)}
							ontoggle={() => toggle(id)}
						/>
					</div>
				</li>
			{/each}
		</ol>
		<!-- Only while loaded rows remain. Reaching further back is a press. -->
		{#if limit < rows.length}
			<div use:whenNear={() => (limit = Math.min(limit + PAGE, rows.length))} class="h-px"></div>
		{/if}
	{/if}

	<div class="mt-2 flex items-center gap-3">
		{#if history.cursor !== null}
			<button onclick={() => history.more()} disabled={history.busy} class={button}>
				{history.busy ? 'Loading…' : 'Load more'}
			</button>
		{/if}
		<!-- The kind filter only sees what is loaded; the window was applied by
		     the service. "Nothing earlier" must say which. No count when nothing
		     is loaded: the sentence above already said it. -->
		{#if history.entries.length}
			<p class="text-[12px] text-faint">
				{rows.length === history.entries.length
					? `${count(history.entries.length, 'event')} loaded`
					: `${rows.length} of ${count(history.entries.length, 'event')} loaded`}{history.cursor ===
				null
					? history.bounded
						? `, nothing earlier in ${said(history.span)}`
						: ', nothing earlier'
					: ''}
			</p>
		{/if}
	</div>
</Page>

<!-- No runner: this page owns no poll or progress bar. -->
<TitleSheet bind:this={sheet} />
