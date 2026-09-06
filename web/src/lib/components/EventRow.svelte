<script lang="ts">
	import Disclosure from '$lib/components/Disclosure.svelte';
	import TitleThumb from '$lib/components/TitleThumb.svelte';
	import { ago, chips, detail, details, dot, headline, marker, type Event } from '$lib/events';
	import { stamp } from '$lib/format';
	import type { Card } from '$lib/library';

	// One line of history, opening to the full record. On the events page's
	// timeline, and in the overview's rail.
	let {
		entry,
		open = false,
		// The previous settings event, which only the caller can find.
		before,
		// The title this line is about, with somewhere to take it: a poster that
		// does nothing is worse than none.
		card,
		onopen,
		compact = false,
		id,
		ontoggle
	}: {
		entry: Event;
		open?: boolean;
		before?: Event;
		card?: Card;
		onopen?: (card: Card) => void;
		// The overview's rail: headline and time only until lg, where the row is
		// wide enough for the events page's line and chips.
		compact?: boolean;
		// Ties the button to the panel it opens; unique within the page.
		id: string;
		ontoggle: () => void;
	} = $props();

	// Both or neither: a thumb with nowhere to go swallows a tap.
	const poster = $derived(card && onopen ? card : undefined);

	const line = $derived(detail(entry));
	const marks = $derived(chips(entry));
	const episode = $derived(marker(entry));

	// By stylesheet, not a branch, so a resize across lg needs no measuring.
	// Spelled out whole: Tailwind never generates a runtime-pasted prefix.
	const lineClass = $derived(
		compact
			? 'mt-0.5 hidden text-[12.5px] text-dim lg:block'
			: 'mt-0.5 block text-[12.5px] text-dim'
	);
	const marksClass = $derived(
		compact ? 'mt-1.5 hidden flex-wrap gap-1.5 lg:flex' : 'mt-1.5 flex flex-wrap gap-1.5'
	);
</script>

{#snippet thumb()}
	{#if poster && onopen}
		<TitleThumb card={poster} {onopen} />
	{/if}
{/snippet}

<!-- The chips stay in the line's column; the panel takes the full width. -->
<Disclosure {id} {open} {ontoggle} beside={poster ? thumb : undefined}>
	{#snippet summary(chevron)}
		<span class="flex items-baseline gap-2.5">
			{#if compact}
				<!-- A compact row has no timeline rail, so it carries its own dot. -->
				<span
					aria-hidden="true"
					class={`h-1.5 w-1.5 flex-none translate-y-[-2px] rounded-full ${dot(entry)}`}
				></span>
			{/if}
			<!-- The episode sits outside the truncation, so a long series title
			     cannot take it over the end. -->
			<span
				class={`flex min-w-0 flex-1 items-baseline gap-1.5 font-medium ${
					compact ? 'text-[12.5px] text-dim lg:text-[13.5px] lg:text-fg' : 'text-[13.5px]'
				}`}
			>
				<span class="min-w-0 truncate">{headline(entry)}</span>
				{#if episode}
					<span class="flex-none text-faint tabular-nums">{episode}</span>
				{/if}
			</span>
			<time
				datetime={entry.ts}
				title={stamp(entry.ts)}
				class="flex-none text-[11px] whitespace-nowrap text-faint"
			>
				{ago(entry.ts)}
			</time>
			{@render chevron()}
		</span>
		{#if line}
			<span class={lineClass}>{line}</span>
		{/if}
	{/snippet}

	{#snippet aside()}
		{#if marks.length}
			<div class={marksClass}>
				{#each marks as chip, at (at)}
					<span class="rounded border border-line px-1.5 py-0.5 font-mono text-[11px] text-faint">
						{chip}
					</span>
				{/each}
			</div>
		{/if}
	{/snippet}

	<!-- The row's full width: paths were wrapping early against an empty column. -->
	{#snippet panel()}
		<dl
			class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 rounded-lg border border-line bg-sunken px-3 py-2.5 text-[12px]"
		>
			{#each details(entry, before) as row (row.label)}
				<dt class="whitespace-nowrap text-faint">{row.label}</dt>
				<dd class={`min-w-0 wrap-anywhere text-dim ${row.mono ? 'font-mono' : ''}`}>
					{#each row.values as value, at (at)}
						<span class="block">{value}</span>
					{/each}
				</dd>
			{/each}
		</dl>
	{/snippet}
</Disclosure>
