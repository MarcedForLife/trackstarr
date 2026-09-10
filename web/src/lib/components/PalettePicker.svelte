<script lang="ts">
	import { PALETTES, setPalette, theme } from '$lib/theme.svelte';

	// The SettingRow's paragraph ids.
	let { labelledBy, describedBy }: { labelledBy: string; describedBy: string } = $props();

	const at = $derived(PALETTES.findIndex((option) => option.value === theme.palette));

	let swatches: HTMLButtonElement[] = [];

	// The segmented switch's tab stop and arrows, on a grid: down is a row. Three
	// matches the column count in the class below.
	const COLUMNS = 3;
	const STEPS: Record<string, number> = {
		ArrowRight: 1,
		ArrowLeft: -1,
		ArrowDown: COLUMNS,
		ArrowUp: -COLUMNS
	};

	function steer(event: KeyboardEvent) {
		const step = STEPS[event.key];
		let to: number;
		if (event.key === 'Home') to = 0;
		else if (event.key === 'End') to = PALETTES.length - 1;
		else if (step === undefined) return;
		else to = (Math.max(at, 0) + step + PALETTES.length) % PALETTES.length;

		event.preventDefault();
		setPalette(PALETTES[to].value);
		swatches[to]?.focus();
	}
</script>

<!-- Each swatch carries layout.css's two attributes, so it paints itself in its
     own palette. tabindex -1 makes the grid the element the arrows are heard on. -->
<div
	role="radiogroup"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	onkeydown={steer}
	tabindex={-1}
	class="grid w-full grid-cols-3 gap-x-3 gap-y-3.5 sm:w-60 sm:gap-x-2.5"
>
	{#each PALETTES as option, index (option.value)}
		{@const selected = theme.palette === option.value}
		<button
			bind:this={swatches[index]}
			role="radio"
			aria-checked={selected}
			tabindex={index === Math.max(at, 0) ? 0 : -1}
			onclick={() => setPalette(option.value)}
			class="group flex flex-col items-center gap-1.5"
		>
			<span
				data-palette={option.value}
				data-theme={theme.resolved}
				class={`flex aspect-[3/2] w-full flex-col justify-between rounded-lg border border-line-strong bg-surface p-2 outline-2 outline-offset-2 transition-[outline-color] ${
					selected ? 'outline-accent' : 'outline-transparent group-hover:outline-line-strong'
				}`}
			>
				<span class="flex items-center gap-1.5">
					<span class="h-2 w-2 flex-none rounded-full bg-accent"></span>
					<span class="h-1 flex-1 rounded-full bg-fg"></span>
				</span>
				<span class="h-1 w-2/3 rounded-full bg-dim"></span>
				<span class="block h-3.5 rounded-[5px] border border-line-strong bg-raised"></span>
			</span>
			<span
				class={`text-[12px] leading-none ${selected ? 'font-semibold text-fg' : 'font-medium text-dim group-hover:text-fg'}`}
			>
				{option.label}
			</span>
		</button>
	{/each}
</div>
