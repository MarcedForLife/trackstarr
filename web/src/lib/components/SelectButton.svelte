<script lang="ts">
	import { control, radius } from '$lib/controls';

	// The press that turns a tap from opening into picking, beside whatever the
	// list is searched with. The word only where there is room; the tick is the
	// mark the rows grow.
	let {
		picking,
		// What is being picked, for the tooltip a phone reads instead of the word.
		what,
		onclick,
		disabled = false,
		showLabel = false,
		quiet = false
	}: {
		picking: boolean;
		what: string;
		onclick: () => void;
		disabled?: boolean;
		showLabel?: boolean;
		quiet?: boolean;
	} = $props();
</script>

<button
	type="button"
	aria-pressed={picking}
	{onclick}
	{disabled}
	title={picking ? `Stop selecting ${what}` : `Select ${what}`}
	class={`flex ${control} ${radius} flex-none items-center gap-1.5 border px-2.5 text-[13px] font-medium transition-colors disabled:opacity-(--disabled) sm:px-3 ${
		picking
			? 'border-accent-fill/50 bg-accent-fill/12 text-fg'
			: quiet
				? 'border-transparent text-dim hover:bg-raised hover:text-fg'
				: 'border-line-strong bg-field text-dim'
	}`}
>
	<svg
		viewBox="0 0 14 14"
		width="12"
		height="12"
		fill="none"
		stroke="currentColor"
		stroke-width="1.7"
		stroke-linecap="round"
		stroke-linejoin="round"
		aria-hidden="true"
		class="flex-none"
	>
		<path d="M2 7.4 5.2 10.6 12 3.6" />
	</svg>
	<span class={showLabel ? '' : 'hidden sm:inline'}>{picking ? 'Cancel' : 'Select'}</span>
</button>
