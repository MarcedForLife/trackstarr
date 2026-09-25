<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';

	// One header and one set of gutters for every page.
	let {
		lead,
		wide = false,
		children
	}: {
		lead?: string;
		// The settings pages read best at a middle width; a grid of posters wants
		// the screen.
		wide?: boolean;
		children: Snippet;
	} = $props();
	const title = $derived(page.data.pageTitle ?? 'Trackstarr');
	const width = $derived(wide ? 'max-w-6xl xl:max-w-7xl' : 'max-w-4xl');
</script>

<!-- Svelte assigns document.title, so this replaces the shell's name. -->
<svelte:head><title>{title} · Trackstarr</title></svelte:head>

<!-- Screen readers only from lg, where the sidebar names the page. The phone
     header has its own. -->
<h1 class="sr-only max-lg:hidden">{title}</h1>

<!-- Centred in the space beside the sidebar, or a 42rem column sits flush left
     on a wide display. One step wider from xl for the two poster pages, which
     is another grid column; past 1200px a header stops reading as one control. -->
<div
	class={`mx-auto w-full px-5 ${lead ? 'pt-5' : 'pt-0'} pb-[calc(2.5rem+env(safe-area-inset-bottom)+var(--tabbar))] sm:px-8 lg:px-16 lg:pt-8 ${width}`}
>
	{#if lead}
		<p class="text-sm text-dim sm:text-[13.5px]">{lead}</p>
	{/if}
	{@render children()}
</div>
