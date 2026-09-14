<script lang="ts">
	import { arrival, coverShow, type Arrival } from '$lib/covers';
	import { display } from '$lib/display.svelte';
	import { coverUrl, initials } from '$lib/library';
	import type { FileCover } from '$lib/queue';

	// Wider on a file being worked on. A poster's own two by three either way,
	// never cropped to meet the row: sized to the three lines a row holds on a
	// phone, and centred against whatever a narrower column wraps them to.
	let {
		cover,
		name,
		prominent = false,
		onopen
	}: { cover?: FileCover; name: string; prominent?: boolean; onopen?: () => void } = $props();
	const shape = $derived(prominent ? 'w-16' : 'w-12');
	const art = $derived(cover ? coverUrl(cover.id) : '');
	let failed = $state('');
	let loaded = $state('');
	let arrivalState = $state<Arrival>('coming');
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

{#if onopen && cover}
	<button
		type="button"
		onclick={onopen}
		aria-label={`View ${cover.name} details`}
		class="-m-1 flex shrink-0 rounded-lg p-1"
	>
		{@render artwork()}
	</button>
{:else}
	{@render artwork()}
{/if}
