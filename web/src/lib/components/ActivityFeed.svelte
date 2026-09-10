<script lang="ts">
	import { resolve } from '$app/paths';
	import EventRow from '$lib/components/EventRow.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { key, THREAD, type Event } from '$lib/events';
	import type { Card } from '$lib/library';

	// The last few lines of history. Holds only how much is shown and which lines
	// are open; the page reads the history.
	let {
		entries,
		// The cards for the titles these lines name, so a line can carry its
		// poster and take a press to the sheet the page owns.
		titles = {},
		onopen
	}: {
		entries: Event[];
		titles?: Record<string, Card>;
		onopen?: (card: Card) => void;
	} = $props();

	// Which lines are open, keyed by content: an arriving line used to carry every
	// open panel down one.
	let opened = $state<Record<string, boolean>>({});

	// How many lines the feed opens on. The desktop rows are rendered and hidden
	// below lg.
	const ROWS = 16;
	const PHONE_ROWS = 8;
	// How many more each press adds. Past the loaded window is the events page.
	const MORE = 10;
	let extra = $state(0);

	const rows = $derived(entries.slice(0, ROWS + extra).map((entry) => ({ entry, id: key(entry) })));
	// Whether the button has anything left, at both lengths; CSS tells them apart.
	const morePhone = $derived(entries.length > PHONE_ROWS + extra);
	const moreDesk = $derived(entries.length > ROWS + extra);
</script>

<section class="min-w-0">
	<h2 class="text-[11px] font-semibold tracking-wider text-faint uppercase">Activity</h2>
	{#if rows.length}
		<!-- The page's width, like the two panels above it: capped to keep the time
		     near its line, the feed read as a column that had failed to fill.
		     overflow-anchor: none, or scroll anchoring fights an expanding row. -->
		<ol class="relative isolate mt-2.5 flex flex-col gap-2.5 [overflow-anchor:none]">
			<!-- With the column it threads, which is from lg. -->
			<span aria-hidden="true" class={`hidden lg:block ${THREAD}`}></span>
			{#each rows as { entry, id }, at (id)}
				<li class={at >= PHONE_ROWS + extra ? 'hidden lg:block' : ''}>
					<EventRow
						compact
						{entry}
						id={`activity-${at}`}
						open={!!opened[id]}
						card={entry.title ? titles[entry.title] : undefined}
						{onopen}
						ontoggle={() => (opened = { ...opened, [id]: !opened[id] })}
					/>
				</li>
			{/each}
		</ol>
	{:else}
		<p class="mt-2.5 text-[12.5px] text-dim">Nothing yet.</p>
	{/if}
	<!-- More lines without leaving the page, beside the way off it. -->
	<div class="mt-3 flex items-center gap-4">
		{#if morePhone}
			<button
				type="button"
				onclick={() => (extra += MORE)}
				class={`inline-flex items-center gap-1 text-[12.5px] font-medium text-dim hover:text-fg ${
					moreDesk ? '' : 'lg:hidden'
				}`}
			>
				Show more
			</button>
		{/if}
		<a
			href={resolve('/events')}
			class="inline-flex items-center gap-1 text-[12.5px] font-medium text-dim hover:text-fg"
		>
			All events
			<Glyph name="next" size={11} />
		</a>
	</div>
</section>
