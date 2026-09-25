<script lang="ts">
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import TitleThumb from '$lib/components/TitleThumb.svelte';
	import { dotted } from '$lib/controls';
	import { ago, badge, details, dot, layouts, measures, parts, type Event } from '$lib/events';
	import { stamp } from '$lib/format';
	import { changed } from '$lib/library';
	import type { Card } from '$lib/library';

	// One line of history, opening to the full record.
	let {
		entry,
		open = false,
		// The previous settings event, which only the caller can find.
		before,
		// The title this line is about, with somewhere to take it: a poster that
		// does nothing is worse than none.
		card,
		onopen,
		// Ties the button to its panel, unique within the page.
		id,
		ontoggle
	}: {
		entry: Event;
		open?: boolean;
		before?: Event;
		card?: Card;
		onopen?: (card: Card) => void;
		id: string;
		ontoggle: () => void;
	} = $props();

	// Events about one file's rewrite.
	const FILES = new Set(['modified', 'pending', 'failed', 'deferred', 'skipped']);
	const REWRITES = new Set(['modified', 'pending']);

	// Both or neither: a thumb with nowhere to go swallows a tap.
	const poster = $derived(card && onopen ? card : undefined);

	// What stands where the poster would on a line about the service: its kind's
	// mark, so the list can be read down the column.
	const mark = $derived(poster ? '' : badge(entry));

	// The card's name where there is one, since the *arr names the folder.
	const shape = $derived(parts(entry, card?.name));

	const marks = $derived(
		FILES.has(entry.event)
			? [
					...changed(layouts(entry)),
					...measures(entry).map((chip) => ({
						chip,
						ink: 'text-dim',
						label:
							chip === `−${entry.drops}`
								? `Drops ${entry.drops} track${entry.drops === 1 ? '' : 's'}`
								: chip
					}))
				]
			: []
	);
	// A rewrite's reason only repeats its counts.
	const reason = $derived(marks.length && REWRITES.has(entry.event) ? '' : shape.reason);
	// The second line's item that truncates first.
	const gives = $derived(shape.file ? 0 : shape.meta.length - 1);
</script>

{#snippet thumb()}
	<span class="contents">
		{#if poster && onopen}
			<TitleThumb card={poster} {onopen} />
		{:else if mark}
			<!-- Opens the line like the summary beside it. Out of the tab order and
			     hidden from screen readers, since the summary carries the same action. -->
			<button
				type="button"
				tabindex="-1"
				aria-hidden="true"
				onclick={ontoggle}
				class="relative z-10 flex min-h-11 w-10 flex-none items-center justify-center self-stretch rounded-md bg-sunken text-dim transition-colors hover:text-fg"
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
	{#snippet summary()}
		<span class="flex w-full items-baseline gap-2">
			<!-- A fallback mark for an event without a poster or service icon.
			     Nudged up, since a 6px circle on the baseline sits low. -->
			{#if !poster && !mark}
				<span
					aria-hidden="true"
					class={`h-1.5 w-1.5 flex-none translate-y-[-2px] rounded-full ${dot(entry)}`}
				></span>
			{/if}
			<span class="block min-w-0 flex-1">
				<span class="flex items-baseline gap-1.5 text-[14px] leading-snug font-medium text-fg">
					<span class="truncate" title={shape.title}>{shape.title}</span>
					{#if shape.aside}
						<span class="flex-none text-[12.5px] font-normal text-faint tabular-nums"
							>{shape.aside}</span
						>
					{/if}
					<time
						datetime={entry.ts}
						title={stamp(entry.ts)}
						class="ml-auto flex-none pl-2 text-[11.5px] font-normal whitespace-nowrap text-faint"
					>
						{ago(entry.ts)}
					</time>
				</span>
				{#if shape.meta.length}
					<span class={`mt-0.5 text-[12px] text-dim ${dotted}`} title={shape.meta.join(' · ')}>
						{#each shape.meta as part, at (at)}
							<span class={at === gives ? 'min-w-0 truncate' : 'flex-none'}>{part}</span>
						{/each}
					</span>
				{/if}
				{#if shape.status || shape.counts.length || marks.length || reason}
					<span class={`mt-0.5 text-[12px] text-dim ${dotted}`}>
						{#if shape.status}
							<span class={`flex-none font-medium ${shape.status.text}`}>{shape.status.word}</span>
						{/if}
						{#each shape.counts as part (part.text)}
							<span class={`flex-none ${part.tone}`}>{part.text}</span>
						{/each}
						{#if marks.length}
							<span class="flex-none [&>*+*]:ml-1.5">
								{#each marks as change (change.chip)}
									<span
										class={`${/^[+−~]/.test(change.chip) ? 'font-mono text-[11.5px] tracking-tight' : ''} ${change.ink}`}
										title={change.label}
										aria-label={change.label}>{change.chip}</span
									>
								{/each}
							</span>
						{/if}
						{#if reason}<span class="min-w-0 truncate" title={reason}>{reason}</span>{/if}
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
