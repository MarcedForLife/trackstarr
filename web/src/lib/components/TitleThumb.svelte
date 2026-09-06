<script lang="ts">
	import { display } from '$lib/display.svelte';
	import { coverUrl, initials, verdictLabel, type Card } from '$lib/library';

	// A title's poster at list-row size, opening the title from a line of
	// history. Not PosterCard, whose tilt, sheen and long press are for a grid.
	let { card, onopen }: { card: Card; onopen: (card: Card) => void } = $props();

	let missing = $state(false);

	const mark = $derived(initials(card.name));
</script>

<!-- The padding is the tap target and the margin gives it back. self-start, or
     the button stretched to an open panel's full height. -->
<button
	type="button"
	onclick={() => onopen(card)}
	aria-label={`${card.name}${card.year ? ` (${card.year})` : ''} — ${verdictLabel(card.state)}`}
	class="thumb -m-1 block flex-none self-start p-1"
>
	<span
		class="block aspect-[2/3] w-10 overflow-hidden rounded-md border border-line bg-sunken shadow-[0_1px_2px_rgb(0_0_0/0.25)]"
	>
		{#if missing || display.art === 'hide'}
			<span
				class="flex h-full w-full items-center justify-center bg-raised text-[11px] font-semibold text-faint"
			>
				{mark}
			</span>
		{:else}
			<!-- Dimensions declared so the row lays out before the picture lands.
			     data-cover lets $lib/covers call it off on navigation. -->
			<img
				data-cover
				src={coverUrl(card.id)}
				alt=""
				width="500"
				height="750"
				loading="lazy"
				decoding="async"
				draggable="false"
				onerror={() => (missing = true)}
				class="h-full w-full object-cover"
			/>
		{/if}
	</span>
</button>

<style>
	/* No tap highlight: over artwork it reads as the poster changing. */
	.thumb {
		-webkit-tap-highlight-color: transparent;
	}
</style>
