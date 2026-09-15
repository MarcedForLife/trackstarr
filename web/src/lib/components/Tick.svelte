<script lang="ts">
	import { scale } from 'svelte/transition';
	import { tickPop } from '$lib/motion.svelte';

	// The round mark a selection wears: on a poster in the grid, and beside a
	// file in the queue. Drawn, not a checkbox, so the two read as one gesture.
	let {
		on = false,
		// Some but not all of what the row covers, as a select-all row can be.
		some = false,
		// The empty ring. Over artwork it prints on whatever the poster is, so
		// the caller says; the default is for a ring on the page.
		off = 'border-line-strong bg-sunken',
		class: shape = ''
	}: { on?: boolean; some?: boolean; off?: string; class?: string } = $props();
</script>

<span
	aria-hidden="true"
	transition:scale={tickPop()}
	class={`flex h-5 w-5 items-center justify-center rounded-full border transition-colors ${
		on || some ? 'border-accent-fill bg-accent-fill text-on-accent' : `${off} text-transparent`
	} ${shape}`}
>
	<svg
		viewBox="0 0 12 12"
		width="9"
		height="9"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
	>
		<path d={some && !on ? 'M3 6h6' : 'M2.5 6.3 4.9 8.7 9.5 3.6'} />
	</svg>
</span>
