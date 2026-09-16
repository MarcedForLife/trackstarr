<script lang="ts">
	import Count from './Count.svelte';
	import Glyph from './Glyph.svelte';

	// A place in the queue: the queue's own mark and the number, so a bare
	// figure is never read as a count or an id. Brass at the head, where the
	// file is next to go, and dim behind it.
	let {
		place,
		size = 10,
		class: extra = ''
	}: { place: number; size?: number; class?: string } = $props();
</script>

<span class={`whitespace-nowrap tabular-nums ${place === 1 ? 'text-accent' : 'text-dim'} ${extra}`}>
	<span class="sr-only">place </span>
	<!-- Dropped a fifth of the size, so the lines span the x-height. -->
	<span class="mr-[0.3em] inline-block align-[-0.2em]"><Glyph name="list" {size} /></span><Count
		value={place}
	/>
</span>
