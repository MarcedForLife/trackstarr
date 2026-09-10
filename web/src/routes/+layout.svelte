<script lang="ts">
	import './layout.css';
	// Side-effect import: keeps <html data-theme> tracking the OS on every page
	// while the preference is "system".
	import '$lib/theme.svelte';

	let { children } = $props();
</script>

{@render children()}
