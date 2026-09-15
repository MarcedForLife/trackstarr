<script lang="ts">
	import { arrival, coverShow, type Arrival } from '$lib/covers';
	import { display } from '$lib/display.svelte';
	import { coverUrl, initials } from '$lib/library';
	import { pressGesture } from '$lib/press';
	import type { FileCover } from '$lib/queue';

	// Wider on a file being worked on, never cropped to meet the row: two by three
	// at the height of the row's three lines, which truncate rather than wrap so
	// the cover ends where the chips do.
	let {
		cover,
		name,
		prominent = false,
		onopen,
		// Whether this file is picked, and by being defined at all, whether a tap
		// picks rather than opens. The grid's gesture, on the row's own artwork.
		selected,
		onpick,
		// A long press released in place, as the grid selects. Absent means none.
		onhold,
		// Whether lifting now would land it. The row draws that, beside its
		// chevron, so this only reports.
		onarm,
		// A press on the cover, so the row it belongs to raises with it.
		onpress,
		disabled = false
	}: {
		cover?: FileCover;
		name: string;
		prominent?: boolean;
		onopen?: () => void;
		selected?: boolean;
		onpick?: () => void;
		onhold?: () => void;
		onarm?: (on: boolean) => void;
		onpress?: (on: boolean) => void;
		disabled?: boolean;
	} = $props();
	const shape = $derived(prominent ? 'w-16' : 'w-10');
	const art = $derived(cover ? coverUrl(cover.id) : '');
	let failed = $state('');
	let loaded = $state('');
	let arrivalState = $state<Arrival>('coming');

	const picking = $derived(selected !== undefined);

	// A cover is the title this file belongs to, so without one there is nothing
	// to open. The tile still stands for the file, which is what a hold picks.
	const opens = $derived(!!onopen && !!cover);

	// The gesture is $lib/press's, the same one the grid's posters answer. What
	// it raises is the row, not the cover.
	const gesture = $derived({
		ontap: () => (picking ? onpick?.() : opens ? onopen?.() : undefined),
		onhold: () => onhold?.(),
		canHold: () => !!onhold && !picking,
		onarm,
		onpress
	});
</script>

{#snippet artwork()}
	<span
		aria-hidden="true"
		class={`relative block aspect-[2/3] shrink-0 self-center overflow-hidden rounded-md border border-line bg-sunken ${shape}`}
	>
		<span
			class="absolute inset-0 flex items-center justify-center text-[11px] font-medium text-faint"
		>
			{initials(cover?.name || name)}
		</span>
		{#if art && failed !== art && display.art !== 'hide'}
			<img
				data-cover
				src={art}
				alt=""
				width="500"
				height="750"
				loading="lazy"
				decoding="async"
				draggable="false"
				onload={() => {
					loaded = art;
					arrivalState = arrival(art);
				}}
				onerror={() => (failed = art)}
				class={`absolute inset-0 h-full w-full object-cover ${coverShow(loaded === art ? arrivalState : 'coming')}`}
			/>
		{/if}
	</span>
{/snippet}

{#if picking || opens}
	<!-- While picking it is the row's press under another finger, so it is out of
	     the tab order and silent: the row itself carries the tick and the words. -->
	<button
		type="button"
		use:pressGesture={gesture}
		disabled={picking && disabled}
		tabindex={picking ? -1 : undefined}
		aria-hidden={picking ? 'true' : undefined}
		aria-label={picking ? undefined : `View ${cover?.name || name} details`}
		class="poster relative z-10 -m-1 flex shrink-0 rounded-lg p-1 disabled:opacity-(--disabled)"
	>
		{@render artwork()}
	</button>
{:else if onhold}
	<!-- No title to open, so no button and no label promising one. The hold is
	     the only gesture left, and a keyboard reaches picking from the Select
	     press instead. -->
	<span use:pressGesture={gesture} class="poster relative z-10 -m-1 flex shrink-0 p-1"
		>{@render artwork()}</span
	>
{:else}
	{@render artwork()}
{/if}

<style>
	/* pan-y, not none: a vertical drag is the list scrolling. The rest stops a
	   phone selecting text and flashing a grey box on a held finger; the
	   long-press menu cannot be turned off from here on Chromium, so $lib/press
	   answers it. */
	.poster {
		touch-action: pan-y;
		user-select: none;
		-webkit-touch-callout: none;
		-webkit-tap-highlight-color: transparent;
	}
</style>
