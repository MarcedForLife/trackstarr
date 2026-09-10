<script lang="ts">
	import HueSlider from '$lib/components/HueSlider.svelte';
	import { inlineTokens } from '$lib/hue';
	import { PALETTES, setHue, setPalette, theme } from '$lib/theme.svelte';

	// The SettingRow's paragraph ids.
	let { labelledBy, describedBy }: { labelledBy: string; describedBy: string } = $props();

	const at = $derived(PALETTES.findIndex((option) => option.value === theme.palette));

	// Custom is the last option, alone on the third row with the hue slider
	// filling the rest of it.
	const last = PALETTES.length - 1;

	let swatches: HTMLButtonElement[] = [];

	// Custom has no block in layout.css: its swatch paints itself from the
	// tokens $lib/hue derives, and the slider's thumb takes the same fill.
	const customTokens = $derived(theme.custom[theme.resolved]);
	const customStyle = $derived(inlineTokens(customTokens));

	// The segmented switch's tab stop and arrows, on a grid. Three matches the
	// column count in the class below.
	const COLUMNS = 3;

	function steer(event: KeyboardEvent) {
		const from = Math.max(at, 0);
		let to: number;
		switch (event.key) {
			case 'ArrowRight':
				to = (from + 1) % PALETTES.length;
				break;
			case 'ArrowLeft':
				to = (from - 1 + PALETTES.length) % PALETTES.length;
				break;
			// Down off the second row lands on Custom; down off Custom wraps to
			// the top.
			case 'ArrowDown':
				to = from === last ? 0 : Math.min(from + COLUMNS, last);
				break;
			// Up off the top row lands on Custom; up off Custom, the row above it.
			case 'ArrowUp':
				to = from < COLUMNS ? last : from - COLUMNS;
				break;
			case 'Home':
				to = 0;
				break;
			case 'End':
				to = last;
				break;
			default:
				return;
		}

		event.preventDefault();
		setPalette(PALETTES[to].value);
		swatches[to]?.focus();
	}

	// Moving the slider is choosing Custom.
	function pick(hue: number) {
		setHue(hue);
		if (theme.palette !== 'custom') setPalette('custom');
	}
</script>

<!-- A miniature page in whatever palette the span around it carries: a rail
     in the sunken tone with a dot, a card in the raised, and the accent where
     the app puts it, as a dot and as a bar. -->
{#snippet miniature()}
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
{/snippet}

<!-- Each swatch carries layout.css's two attributes, so it paints itself in its
     own palette; Custom carries its tokens inline instead. tabindex -1 makes the
     grid the element the arrows are heard on. The slider is its own tab stop. -->
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
		{@const custom = option.value === 'custom'}
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
				style={custom ? customStyle : undefined}
				class={`flex aspect-[3/2] w-full overflow-hidden rounded-lg border border-line-strong bg-surface text-fg outline-2 outline-offset-2 transition-[outline-color] ${
					selected ? 'outline-accent-fill' : 'outline-transparent group-hover:outline-line-strong'
				}`}
			>
				{@render miniature()}
			</span>
			<span
				class={`text-[12px] leading-none tabular-nums ${selected ? 'font-semibold text-fg' : 'font-medium text-dim group-hover:text-fg'}`}
			>
				{custom ? `${option.label} · ${theme.hue}°` : option.label}
			</span>
		</button>
	{/each}

	<!-- The rest of Custom's row. Centred on the swatch, with a caption on the
	     labels' line. -->
	<div class="col-span-2 flex flex-col items-stretch gap-1.5">
		<div class="flex flex-1 items-center px-1">
			<HueSlider
				value={theme.hue}
				fill={customTokens['--accent-fill']}
				theme={theme.resolved}
				onchange={pick}
			/>
		</div>
		<span class="text-center text-[12px] leading-none font-medium text-faint">
			Slide for any hue
		</span>
	</div>
</div>
