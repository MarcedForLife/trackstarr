<script lang="ts">
	import type { Destination } from '$lib/nav';

	// The glyphs for the three primary destinations, in one place so the rail
	// and the tab bar cannot end up drawing different things for the same
	// page. The six settings icons stay inline in the sidebar: nothing else
	// draws them, so there is nothing for them to drift from.
	let { icon, size = 16 }: { icon: Destination['icon']; size?: number } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.8"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	{#if icon === 'overview'}
		<path d="M3 10.5 12 3l9 7.5"></path>
		<path d="M5 9.5V21h14V9.5"></path>
	{:else if icon === 'library'}
		<!-- Three posters on a shelf, which is what the page is. -->
		<rect x="3" y="4" width="5" height="16" rx="1"></rect>
		<rect x="10" y="4" width="5" height="16" rx="1"></rect>
		<path d="M17.5 5.6l3.2 1 -3.4 12.4 -3.2-1z"></path>
	{:else}
		<path d="M3 12h4l2.5-6 5 12 2.5-6h4"></path>
	{/if}
</svg>
