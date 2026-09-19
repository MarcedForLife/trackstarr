<script lang="ts">
	import Glyph from './Glyph.svelte';

	let {
		path,
		label,
		prominent = false
	}: {
		path: string;
		label?: string;
		prominent?: boolean;
	} = $props();
	const name = $derived(label || path.split('/').filter(Boolean).at(-1) || path);
</script>

<details class="path-details min-w-0">
	<summary
		class={`flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded text-left ${prominent ? 'text-[13px] font-medium text-fg' : 'text-[11px] text-faint hover:text-dim'}`}
		title={path}
		aria-label={`Show full path for ${name}`}
	>
		<span class="min-w-0 truncate">{name}</span>
		<span class="path-chevron flex-none text-faint"><Glyph name="chevron" size={10} /></span>
	</summary>
	<p
		class="my-1 rounded-md border border-line bg-raised px-2.5 py-2 font-mono text-[11px] leading-relaxed break-all whitespace-pre-wrap text-dim select-text"
	>
		{path}
	</p>
</details>

<style>
	summary::-webkit-details-marker {
		display: none;
	}
	.path-details[open] > summary .path-chevron {
		transform: rotate(90deg);
	}
</style>
