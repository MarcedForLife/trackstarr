<script lang="ts">
	import { control, radius } from '$lib/controls';

	let {
		options,
		value,
		disabled = false,
		label,
		labelledBy,
		describedBy,
		tight = false,
		onchange
	}: {
		// An option may be off on its own, as Rewrite is on a report-only install.
		// Off means unofferable, never discouraged.
		options: { value: string; label: string; disabled?: boolean }[];
		value: string;
		disabled?: boolean;
		// Padding for a one-character label.
		tight?: boolean;
		// The label for a switch standing alone; in a SettingRow the paragraph ids
		// instead.
		label?: string;
		labelledBy?: string;
		describedBy?: string;
		onchange: (value: string) => void;
	} = $props();

	// The thumb's offset is the selected index; -1 hides it.
	const index = $derived(options.findIndex((option) => option.value === value));

	// A radio group is one tab stop: the selected option, or the first available.
	const stop = $derived(index >= 0 ? index : options.findIndex((option) => !option.disabled));

	let buttons: HTMLButtonElement[] = [];

	// Moving is choosing, as in a radio group.
	const STEPS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

	function steer(event: KeyboardEvent) {
		// Skip options that are off.
		const live = options.map((_, at) => at).filter((at) => !options[at].disabled);
		if (!live.length) return;

		const step = STEPS[event.key];
		let to: number;
		if (event.key === 'Home') to = 0;
		else if (event.key === 'End') to = live.length - 1;
		else if (step === undefined) return;
		else {
			const here = live.indexOf(index);
			// Nothing held: forward starts at the first option, back at the last.
			if (here < 0) to = step > 0 ? 0 : live.length - 1;
			else to = (here + step + live.length) % live.length;
		}

		event.preventDefault();
		const next = live[to];
		onchange(options[next].value);
		buttons[next]?.focus();
	}
</script>

<!-- Sunken track under a raised thumb; those tokens differ in both themes.
     tabindex -1 makes the group the element the arrows are heard on. -->
<div
	role="radiogroup"
	aria-label={label}
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	onkeydown={steer}
	tabindex={-1}
	class={`relative grid ${control} ${radius} w-full auto-cols-fr grid-flow-col border border-line-strong bg-sunken p-0.5 sm:w-auto ${
		disabled ? 'opacity-50' : ''
	}`}
>
	<!-- The thumb's corner is the track's less its inset, so the gap stays even. -->
	{#if index >= 0}
		<span
			aria-hidden="true"
			class="pointer-events-none absolute top-0.5 bottom-0.5 left-0.5 rounded-[10px] border border-line-strong bg-raised shadow-sm transition-transform duration-200 sm:rounded-lg"
			style={`width: calc((100% - 0.25rem) / ${options.length}); transform: translateX(${index * 100}%)`}
		></span>
	{/if}
	{#each options as option, at (option.value)}
		{@const off = disabled || !!option.disabled}
		<button
			bind:this={buttons[at]}
			role="radio"
			aria-checked={value === option.value}
			tabindex={at === stop ? 0 : -1}
			disabled={off}
			onclick={() => onchange(option.value)}
			class={`relative flex h-full min-w-0 items-center justify-center rounded-[10px] text-[13px] whitespace-nowrap transition-colors sm:rounded-lg ${
				tight ? 'px-2.5 sm:px-3' : 'px-3 sm:px-3.5'
			} ${off && !disabled ? 'text-faint line-through decoration-1' : ''} ${
				value === option.value ? 'font-semibold text-fg' : 'font-medium text-dim hover:text-fg'
			}`}
		>
			{option.label}
		</button>
	{/each}
</div>
