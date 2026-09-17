<script lang="ts">
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import TitleThumb from '$lib/components/TitleThumb.svelte';
	import {
		ago,
		badge,
		detail,
		details,
		dot,
		headline,
		layouts,
		marker,
		measures,
		notes,
		outcome,
		release,
		type Event
	} from '$lib/events';
	import { named, stamp } from '$lib/format';
	import { changed, CHIP, PLAIN_TONE } from '$lib/library';
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

	// Lines about one file, which name it the way a queue row does, and of those
	// the ones whose plan the chips carry.
	const FILES = new Set(['modified', 'pending', 'failed', 'deferred', 'skipped']);
	const REWRITES = new Set(['modified', 'pending']);

	// Both or neither: a thumb with nowhere to go swallows a tap.
	const poster = $derived(card && onopen ? card : undefined);

	// What stands where the poster would on a line about the service: its kind's
	// mark, so the list can be read down the column.
	const mark = $derived(poster ? '' : badge(entry));

	// What became of the file is named beside the chips.
	const ending = $derived(FILES.has(entry.event) ? outcome(entry) : undefined);

	// The chips say what the rewrite changed, the layouts it wrote in the
	// library's colour and what it cost in the plain one.
	const marks = $derived([
		...changed(layouts(entry)),
		...measures(entry).map((chip) => ({
			chip,
			tone: PLAIN_TONE,
			label:
				chip === `−${entry.drops}`
					? `Drops ${entry.drops} track${entry.drops === 1 ? '' : 's'}`
					: chip
		}))
	]);

	// A line about a file leads with what the file is. A rewrite's words only
	// repeat the chips beside them, so they go; a problem's say why the row is
	// here, and a plan the chips cannot carry keeps its own.
	const said = $derived.by(() => {
		if (!FILES.has(entry.event)) return detail(entry);
		const words = marks.length && REWRITES.has(entry.event) ? '' : detail(entry);
		return [release(entry), words].filter(Boolean).join(' · ');
	});
	// How long and under what terms ride the end of it, as a file row's clock does.
	const line = $derived([said, ...notes(entry)].filter(Boolean).join(' · '));
	const episode = $derived(marker(entry));
	// File outcomes have their own label below, leaving the title easy to scan.
	const name = $derived(
		FILES.has(entry.event) ? card?.name || named(entry.path).name : headline(entry, card?.name)
	);

	const lineClass = $derived(
		compact
			? 'mt-1 line-clamp-2 text-[12px] leading-relaxed text-dim sm:line-clamp-1'
			: 'mt-1 line-clamp-2 text-[12px] leading-relaxed wrap-anywhere text-dim sm:line-clamp-1'
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
				class="relative z-10 flex h-11 w-11 flex-none items-center justify-center self-center rounded-full border border-line bg-sunken text-dim transition-colors hover:border-line-strong hover:text-fg active:bg-raised"
			>
				<Glyph name={mark} size={14} />
			</button>
		{/if}
	</span>
{/snippet}

<!-- The panel takes the full width; everything else sits in the line's column.
     `after` spreads the press over the caller's padded row. -->
<Disclosure
	{id}
	{open}
	{ontoggle}
	class="group flex h-full min-h-11 w-full items-start rounded-md text-left after:absolute after:inset-0 after:content-['']"
	panelClass="mt-3"
	beside={poster || mark ? thumb : undefined}
>
	{#snippet summary(chevron)}
		<span class="flex w-full items-baseline gap-2">
			<!-- A fallback mark for an event without a poster or service icon.
			     Nudged up, since a 6px circle on the baseline sits low. -->
			{#if !poster && !mark}
				<span
					aria-hidden="true"
					class={`h-1.5 w-1.5 flex-none translate-y-[-2px] rounded-full ${dot(entry)}`}
				></span>
			{/if}
			<!-- One column, as a file row has: the line under the title starts where
			     the title does rather than under the dot. -->
			<span class="min-w-0 flex-1">
				<!-- The title alone, truncating, with the episode kept whole beside it
				     and the time on the same line. The card's name where there is one:
				     a title's folder is named for the *arr that made it. -->
				<span class="flex items-baseline gap-1.5 text-[13px] font-medium text-fg">
					<span class="truncate" title={name}>{name}</span>
					{#if episode}
						<span class="flex-none text-faint tabular-nums">{episode}</span>
					{/if}
					<time
						datetime={entry.ts}
						title={stamp(entry.ts)}
						class="ml-auto flex-none text-[11px] whitespace-nowrap text-faint"
					>
						{ago(entry.ts)}
					</time>
					<!-- On the clock's line, not centred against the row. Words and chips
					     under the title leave a centred chevron adrift from the time. -->
					{@render chevron()}
				</span>
				{#if line}
					<span class={lineClass} title={line}>{line}</span>
				{/if}
				{#if marks.length || ending}
					<span class="mt-2 flex flex-wrap items-center gap-1.5">
						{#if ending}
							<span
								class={`mr-1 inline-flex items-center gap-1.5 text-[11px] font-medium ${ending.text}`}
							>
								<span aria-hidden="true" class={`h-1.5 w-1.5 rounded-full ${dot(entry)}`}></span>
								{ending.word}
							</span>
						{/if}
						{#each marks as change (change.chip)}
							<span class={`${CHIP} ${change.tone}`} title={change.label} aria-label={change.label}
								>{change.chip}</span
							>
						{/each}
					</span>
				{/if}
			</span>
		</span>
	{/snippet}

	<!-- The row's full width: paths were wrapping early against an empty column. -->
	{#snippet panel()}
		<dl
			class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2.5 rounded-lg border border-line bg-sunken p-3 text-[12px] leading-relaxed sm:p-4"
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
