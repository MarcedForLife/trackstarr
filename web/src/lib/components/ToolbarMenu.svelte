<script lang="ts">
	import type { Snippet } from 'svelte';
	import Glyph, { type GlyphName } from '$lib/components/Glyph.svelte';
	import { control, frosted, radius } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';

	let {
		label,
		icon,
		detail = '',
		active = false,
		compact = false,
		children
	}: {
		label: string;
		icon: GlyphName;
		detail?: string;
		active?: boolean;
		compact?: boolean;
		children: Snippet;
	} = $props();

	const id = $props.id();
	let trigger: HTMLButtonElement;
	let panel: HTMLDivElement;
	const menu = popover();

	// Custom date fields can grow a menu after it opens. Keep it in the viewport.
	function fit(node: HTMLElement) {
		const observer = new ResizeObserver(() => {
			if (menu.open) menu.place();
		});
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	}
</script>

<svelte:window
	onpointerdown={(event) => menu.open && menu.outside(event.target as Node) && menu.lower()}
	onresize={() => menu.open && menu.lower()}
/>

<button
	bind:this={trigger}
	type="button"
	onclick={() => menu.toggle(trigger, panel)}
	aria-expanded={menu.open}
	aria-controls={id}
	aria-label={detail ? `${label}: ${detail}` : label}
	class={`relative flex ${control} ${radius} min-w-0 flex-1 items-center gap-2 border px-3 text-[13px] transition-colors ${active ? 'border-accent-fill/50 bg-accent-soft text-fg' : 'border-line-strong bg-field text-dim'} hover:text-fg sm:flex-none ${compact ? 'max-sm:w-11 max-sm:flex-none max-sm:justify-center max-sm:px-0' : ''}`}
>
	<Glyph name={icon} size={14} />
	<span class={`font-medium ${compact ? 'max-sm:hidden' : ''}`}>{label}</span>
	{#if detail}<span class="max-w-32 truncate text-dim max-sm:hidden">{detail}</span>{/if}
	{#if active}<span
			class={`h-1.5 w-1.5 flex-none rounded-full bg-accent-fill ${compact ? 'max-sm:absolute max-sm:top-2 max-sm:right-2' : ''}`}
			aria-hidden="true"
		></span>{/if}
	<!-- Turns on the panel's own timing, see [popover] in layout.css. -->
	<span
		class={`${compact ? 'max-sm:hidden' : ''} ml-auto transition-transform duration-150 ease-(--reveal-ease) sm:ml-0 ${menu.open ? '-rotate-90' : 'rotate-90'}`}
		><Glyph name="chevron" size={10} /></span
	>
</button>
<div
	bind:this={panel}
	use:fit
	{id}
	popover="manual"
	role="group"
	aria-label={`${label} options`}
	class={`fixed m-0 max-h-[calc(100dvh-1.5rem)] w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-2xl border border-line-strong ${frosted} p-3 text-fg shadow-lg`}
>
	<div class="mb-3 flex items-center justify-between">
		<span class="text-[13px] font-semibold">{label}</span>
		<button
			type="button"
			onclick={() => menu.lower()}
			class="min-h-11 rounded-lg px-2 text-[13px] font-medium text-dim hover:bg-sunken hover:text-fg"
			>Done</button
		>
	</div>
	{@render children()}
</div>
