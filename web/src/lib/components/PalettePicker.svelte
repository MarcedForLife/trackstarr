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
     own palette: a rail in the sunken tone, a card in the raised, and the
     accent where the app puts it, as a dot and as a bar. tabindex -1 makes the
     grid the element the arrows are heard on. -->
<div
	role="radiogroup"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	onkeydown={steer}
	tabindex={-1}
	class="grid w-full grid-cols-3 gap-x-3 gap-y-3.5"
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
				class={`flex aspect-[3/2] w-full overflow-hidden rounded-lg border border-line-strong bg-surface text-fg outline-2 outline-offset-2 transition-[outline-color] ${
					selected ? 'outline-accent-fill' : 'outline-transparent group-hover:outline-line-strong'
				}`}
			>
				<span class="flex w-[30%] flex-none flex-col gap-1.5 border-r border-line bg-sunken p-1.5">
					<span class="h-1.5 w-1.5 rounded-full bg-accent-fill"></span>
					<span class="h-1 w-full rounded-full bg-dim"></span>
					<span class="h-1 w-3/4 rounded-full bg-faint"></span>
				</span>
				<span class="flex min-w-0 flex-1 flex-col justify-end gap-1.5 p-1.5">
					<span class="h-1 w-1/2 rounded-full bg-fg"></span>
					<span class="flex flex-col gap-1 rounded-[5px] border border-line bg-raised p-1.5">
						<span class="h-1 w-2/3 rounded-full bg-dim"></span>
						<span class="h-1 overflow-hidden rounded-full bg-sunken">
							<span class="block h-full w-3/5 rounded-full bg-accent-fill"></span>
						</span>
					</span>
				</span>
			</span>
			<span
				class={`text-[12px] leading-none ${selected ? 'font-semibold text-fg' : 'font-medium text-dim group-hover:text-fg'}`}
			>
				{option.label}
			</span>
		</button>
	{/each}
</div>
