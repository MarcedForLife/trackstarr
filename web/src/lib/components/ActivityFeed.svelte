<script lang="ts">
	import { resolve } from '$app/paths';
	import EventRow from '$lib/components/EventRow.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
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

<section class="min-w-0" aria-labelledby="activity-heading">
	<h2 id="activity-heading" class="text-[11px] font-semibold tracking-wider text-faint uppercase">
		Activity
	</h2>
	<!-- No label of its own inside the card: the heading above it already says
	     what these lines are. -->
	<div class="mt-2.5 overflow-hidden rounded-xl border border-line bg-raised">
		{#if rows.length}
			<ol class="divide-y divide-line">
				{#each rows as { entry, id }, at (id)}
					<li class="relative px-4 py-3 transition-[background-color] hover:bg-sunken/50">
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
			<p class="px-4 py-6 text-[12.5px] text-dim">
				No activity yet. Every sweep, import and rewrite appears here.
			</p>
		{/if}
		<!-- More of the same list on the left, the way off it on the right. The
		     link holds that edge whether or not there is more to show. -->
		<div class="flex items-center gap-3 border-t border-line px-4 py-2">
			{#if hasMore}
				<button
					type="button"
					onclick={() => (extra += MORE)}
					class="inline-flex min-h-10 items-center gap-1 text-[12.5px] font-medium text-dim hover:text-fg"
				>
					Show more
					<!-- Turned down, or two right-pointing chevrons in one foot read as
					     two ways off the page. -->
					<span class="inline-flex rotate-90"><Glyph name="chevron" size={11} /></span>
				</button>
			{/if}
			<a
				href={resolve('/events')}
				class="ml-auto inline-flex min-h-10 items-center gap-1 text-[12.5px] font-medium text-accent hover:underline"
			>
				All events <Glyph name="next" size={11} />
			</a>
		</div>
	</div>
</section>
