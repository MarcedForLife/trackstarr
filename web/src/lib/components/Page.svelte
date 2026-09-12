<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';

	// One header and one set of gutters for every page.
	let {
		eyebrow,
		lead,
		wide = false,
		children
	}: {
		eyebrow?: string;
		lead?: string;
		// The settings pages read best at a middle width; a grid of posters wants
		// the screen.
		wide?: boolean;
		children: Snippet;
	} = $props();
	const title = $derived(page.data.pageTitle ?? 'Trackstarr');
</script>

<!-- Svelte assigns document.title, so this replaces the shell's name. -->
<svelte:head><title>{title} · Trackstarr</title></svelte:head>

<!-- Centred in the space beside the sidebar, or a 42rem column sits flush left
     on a wide display. One step wider from xl for the two poster pages, which
     is another grid column; past 1200px a header stops reading as one control. -->

<div
	class={`mx-auto w-full px-5 ${lead ? 'pt-5' : 'pt-0'} pb-[calc(2.5rem+env(safe-area-inset-bottom)+var(--tabbar))] sm:px-8 lg:px-16 lg:pt-11 ${
		wide ? 'max-w-6xl xl:max-w-7xl' : 'max-w-4xl'
	}`}
>
	{#if eyebrow}
		<p class="hidden text-xs font-medium text-faint lg:block">{eyebrow}</p>
	{/if}
	<h1
		class={`hidden text-xl font-semibold tracking-tight sm:text-[22px] lg:block ${eyebrow ? 'mt-1' : ''}`}
	>
		{title}
	</h1>
	{#if lead}
		<p class="text-sm text-dim sm:text-[13.5px] lg:mt-1.5">{lead}</p>
	{/if}
	{@render children()}
</div>
