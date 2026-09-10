<script lang="ts">
	import { control, radius } from '$lib/controls';

	let {
		options,
		value,
		label,
		labelledBy,
		describedBy,
		grow = false,
		disabled = false,
		onchange
	}: {
		options: { value: string; label: string }[];
		value: string;
		// The label for a menu standing alone; in a SettingRow the paragraph ids
		// instead.
		label?: string;
		labelledBy?: string;
		describedBy?: string;
		// Whether the menu takes the room left in its row.
		grow?: boolean;
		disabled?: boolean;
		onchange: (value: string) => void;
	} = $props();
</script>

<!-- A native select: on a phone the platform's own wheel. -->
<label class={`relative ${grow ? 'min-w-0 flex-1' : 'flex-none'}`}>
	{#if label}
		<span class="sr-only">{label}</span>
	{/if}
	<select
		{value}
		{disabled}
		aria-labelledby={labelledBy}
		aria-describedby={describedBy}
		onchange={(event) => onchange(event.currentTarget.value)}
		class={`${control} ${radius} appearance-none border border-line-strong bg-field pr-7 pl-2.5 text-[13px] disabled:opacity-50 sm:pr-8 sm:pl-3 ${
			grow ? 'w-full' : ''
		}`}
	>
		{#each options as option (option.value)}
			<option value={option.value}>{option.label}</option>
		{/each}
	</select>
	<svg
		viewBox="0 0 12 12"
		width="10"
		height="10"
		fill="none"
		stroke="currentColor"
		stroke-width="1.6"
		stroke-linecap="round"
		stroke-linejoin="round"
		aria-hidden="true"
		class="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-faint sm:right-3"
	>
		<path d="M2.5 4.5 6 8l3.5-3.5" />
	</svg>
</label>

<style>
	/* The options carry the page's colours, or the dark theme opens a white
	   menu. */
	option {
		background: var(--raised);
		color: var(--fg);
	}
</style>
