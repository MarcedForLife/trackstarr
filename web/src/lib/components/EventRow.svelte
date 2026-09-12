<script lang="ts">
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import TitleThumb from '$lib/components/TitleThumb.svelte';
	import {
		ago,
		badge,
		chips,
		detail,
		details,
		dot,
		headline,
		marker,
		type Event
	} from '$lib/events';
	import { stamp } from '$lib/format';
	import type { Card } from '$lib/library';

	// One line of history, opening to the full record. On the events page's list,
	// and in the overview's activity panel.
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
		// The overview shows a short preview; the events page keeps the full line.
		compact?: boolean;
		// Ties the button to the panel it opens; unique within the page.
		id: string;
		ontoggle: () => void;
	} = $props();

	// Both or neither: a thumb with nowhere to go swallows a tap.
	const poster = $derived(card && onopen ? card : undefined);

	// What stands where the poster would on a line about the service: its kind's
	// mark, so the list can be read down the column.
	const mark = $derived(poster ? '' : badge(entry));

	const line = $derived(detail(entry));
	const marks = $derived(chips(entry));
	const episode = $derived(marker(entry));

	const lineClass = $derived(
		compact
			? 'mt-1 line-clamp-2 text-[12px] leading-relaxed text-dim sm:line-clamp-1'
			: 'mt-1 block text-[12px] leading-relaxed wrap-anywhere text-dim'
	);
</script>

{#snippet thumb()}
	<span class="contents">
		{#if poster && onopen}
			<TitleThumb card={poster} {onopen} />
		{:else if mark}
			<!-- Round, and as wide as a poster: it fills the column so the headlines
			     line up, and its shape says a mark rather than a cover that failed
			     to load, which an empty slot of the same size did. Centred against
			     the lines, since a run's line carries chips under it and half a
			     poster's height left the mark stranded at the top of them.

			     It opens the line, like the summary beside it, since a mark sitting
			     next to something that opens looks as though it should. Out of the
			     tab order and hidden from a reader who is told: the summary already
			     carries this action and says what it does, so a second stop here
			     would be the same press twice. -->
			<button
				type="button"
				tabindex="-1"
				aria-hidden="true"
				onclick={ontoggle}
				class="flex h-10 w-10 flex-none items-center justify-center self-center rounded-full border border-line bg-sunken text-dim transition-colors hover:border-line-strong hover:text-fg active:bg-raised"
			>
				<Glyph name={mark} size={14} />
			</button>
		{/if}
	</span>
{/snippet}

<!-- The chips stay in the line's column; the panel takes the full width. -->
<Disclosure
	{id}
	{open}
	{ontoggle}
	class="group block min-h-11 w-full text-left"
	beside={poster || mark ? thumb : undefined}
>
	{#snippet summary(chevron)}
		<span class="flex items-baseline gap-2">
			<!-- The verdict's colour, in the line rather than in a column of its own:
			     beside the round mark, a dot in its own column read as a second
			     badge. Nudged up, since a 6px circle on the baseline sits low. -->
			<span
				aria-hidden="true"
				class={`h-1.5 w-1.5 flex-none translate-y-[-2px] rounded-full ${dot(entry)}`}
			></span>
			<!-- Titles wrap while the episode stays intact and the time stays aligned. -->
			<span
				class="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-1.5 text-[13px] font-medium text-fg"
			>
				<!-- The card's name where there is one: a title's folder is named for
				     the *arr that made it. -->
				<span class="min-w-0 break-words">{headline(entry, card?.name)}</span>
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
		{#if marks.length && !compact}
			<div class="mt-1.5 flex flex-wrap gap-1.5">
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
		{#if compact && marks.length}
			<div class="mb-2 flex flex-wrap gap-1.5">
				{#each marks as chip, at (at)}
					<span class="rounded border border-line px-1.5 py-0.5 font-mono text-[11px] text-dim"
						>{chip}</span
					>
				{/each}
			</div>
		{/if}
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
