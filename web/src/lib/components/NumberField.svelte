<script lang="ts">
	import { cell } from '$lib/controls';

	// A counted setting: the box, its unit, and a line saying what is wrong or
	// what the entry comes to. A string, so what was typed travels untouched.
	let {
		value,
		onchange,
		// The SettingRow's paragraph ids.
		labelledBy,
		describedBy,
		unit = '',
		// What startup would refuse, said while the field is open.
		problem = '',
		// The number in a readable unit: 7200 is not two hours at a glance.
		note = '',
		disabled = false
	}: {
		value: string;
		onchange: (value: string) => void;
		labelledBy: string;
		describedBy: string;
		unit?: string;
		problem?: string;
		note?: string;
		disabled?: boolean;
	} = $props();

	// The line under the box, read after the row's sentence. One id, since only
	// one is ever drawn.
	const lineId = $props.id();
	const described = $derived(problem || note ? `${describedBy} ${lineId}` : describedBy);

	// Narrow: three or four digits.
	const box = `${cell} w-24 flex-none sm:w-20`;
</script>

<div class="flex flex-col items-end gap-1.5">
	<div class="flex items-center gap-2">
		<input
			{value}
			oninput={(event) => onchange(event.currentTarget.value)}
			inputmode="numeric"
			autocapitalize="none"
			autocorrect="off"
			spellcheck="false"
			aria-labelledby={labelledBy}
			aria-describedby={described}
			{disabled}
			class={box}
		/>
		{#if unit}
			<span class="text-[13px] text-faint">{unit}</span>
		{/if}
	</div>
	{#if problem}
		<p id={lineId} class="text-[12.5px] text-danger">{problem}</p>
	{:else if note}
		<p id={lineId} class="text-[12.5px] text-faint">{note}</p>
	{/if}
</div>
