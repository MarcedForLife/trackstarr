<script lang="ts">
	import { resolve } from '$app/paths';
	import EventRow from '$lib/components/EventRow.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { fileRow } from '$lib/controls';
	import { tint, tintOf } from '$lib/tint';
	import { key, type Event } from '$lib/events';
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

	const ROWS = 8;
	const MORE = 8;
	let extra = $state(0);

	const rows = $derived(entries.slice(0, ROWS + extra).map((entry) => ({ entry, id: key(entry) })));
	const hasMore = $derived(entries.length > ROWS + extra);
</script>

<section class="min-w-0" aria-labelledby="events-heading">
	<div class="flex items-center gap-3 px-3 pb-3 sm:px-4">
		<h2
			id="events-heading"
			class="min-w-0 flex-1 text-[17px] leading-tight font-semibold tracking-tight"
		>
			Events
		</h2>
		<a
			href={resolve('/events')}
			class="relative -mr-1.5 flex-none rounded px-1.5 py-1.5 text-[12px] font-medium text-accent after:absolute after:-inset-2 after:content-[''] hover:underline"
		>
			View events
		</a>
	</div>
	<div class="rounded-2xl border border-line bg-sunken p-3 sm:p-4">
		{#if rows.length}
			<ol class="flex flex-col gap-2">
				{#each rows as { entry, id }, at (id)}
					<li
						class={`row-lift relative ${fileRow()} py-3`}
						use:tint={tintOf(entry.title ? titles[entry.title]?.id : undefined)}
					>
						<EventRow
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
			<p class="py-3 text-[12.5px] text-dim">
				No events yet. Every sweep, import and rewrite appears here.
			</p>
		{/if}
		{#if hasMore}
			<button
				type="button"
				onclick={() => (extra += MORE)}
				class="mt-2 -mb-1.5 flex min-h-10 w-full items-center justify-center gap-1 text-[12.5px] font-medium text-dim hover:text-fg"
			>
				Show more
				<span class="inline-flex rotate-90"><Glyph name="chevron" size={11} /></span>
			</button>
		{/if}
	</div>
</section>
