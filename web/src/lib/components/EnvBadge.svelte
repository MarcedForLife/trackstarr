<script lang="ts">
	// The one mark for "the environment owns this value", worn by a row's label
	// or by a control carrying a variable of its own.
	let { title }: { title?: string } = $props();
</script>

<span
	{title}
	class="flex items-center gap-1 rounded-[5px] bg-accent-soft px-1.5 py-px font-mono text-[10.5px] font-semibold tracking-wider text-accent"
>
	<svg
		width="10"
		height="10"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2.2"
		stroke-linecap="round"
	>
		<rect x="5" y="11" width="14" height="9" rx="2"></rect>
		<path d="M8 11V7a4 4 0 0 1 8 0v4"></path>
	</svg>
	ENV
</span>
