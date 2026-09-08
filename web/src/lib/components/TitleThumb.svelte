<script lang="ts">
	import { arrival, coverShow, type Arrival } from '$lib/covers';
	import { display } from '$lib/display.svelte';
	import { coverUrl, initials, verdictLabel, type Card } from '$lib/library';

	// A title's poster at list-row size, opening the title from a line of
	// history. Not PosterCard, whose tilt, sheen and long press are for a grid.
	let { card, onopen }: { card: Card; onopen: (card: Card) => void } = $props();

	let missing = $state(false);

	// The picture is the top layer here, so it does its own fading. A cover the
	// reader has been shown already arrives without one; see $lib/covers.
	let cover = $state<Arrival>('coming');
	const showing = $derived(coverShow(cover));

	const mark = $derived(initials(card.name));
	const art = $derived(coverUrl(card.id));
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
		class="relative block aspect-[2/3] w-10 overflow-hidden rounded-md border border-line bg-sunken shadow-[0_1px_2px_rgb(0_0_0/0.25)]"
	>
		<!-- The initials until the cover lands, and instead of one that never
		     does: the picture fades in over them, so a cover that fails to load
		     leaves the row exactly as it started. -->
		<span
			class="flex h-full w-full items-center justify-center bg-raised text-[11px] font-semibold text-faint"
		>
			{mark}
		</span>
		{#if !missing && display.art !== 'hide'}
			<!-- Dimensions declared so the row lays out before the picture lands.
			     data-cover lets $lib/covers call it off on navigation. -->
			<img
				data-cover
				src={art}
				alt=""
				width="500"
				height="750"
				loading="lazy"
				decoding="async"
				draggable="false"
				onload={() => (cover = arrival(art))}
				onerror={() => (missing = true)}
				class={`absolute inset-0 h-full w-full object-cover ${showing}`}
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
