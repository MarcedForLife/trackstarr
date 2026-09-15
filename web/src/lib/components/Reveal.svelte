<script lang="ts">
	import type { Snippet } from 'svelte';
	import { slide } from 'svelte/transition';
	import { blockSlide } from '$lib/motion.svelte';

	// A block that arrives and leaves on its own height, so the page under it
	// travels rather than jumping. The condition is a prop rather than an `{#if}`
	// at the call site: Svelte runs an outro only on its branch's own child.
	let {
		when,
		class: shape = '',
		children
	}: { when: boolean; class?: string; children: Snippet } = $props();
</script>

{#if when}
	<div class={shape} transition:slide={blockSlide()}>{@render children()}</div>
{/if}
