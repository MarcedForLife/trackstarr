<script lang="ts">
	import { frosted } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';
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
	const id = $props.id();
	const location = popover({ edge: 'left' });
	let trigger: HTMLButtonElement;
	let panel: HTMLDivElement;

	$effect(() => {
		if (!location.open) return;
		const dismiss = (event: Event) => {
			if (!panel.contains(event.target as Node)) location.lower();
		};
		window.addEventListener('scroll', dismiss, true);
		return () => window.removeEventListener('scroll', dismiss, true);
	});
</script>

<svelte:window
	onpointerdown={(event) =>
		location.open && location.outside(event.target as Node) && location.lower()}
	onresize={() => location.open && location.lower()}
/>

<button
	bind:this={trigger}
	type="button"
	onclick={() => location.toggle(trigger, panel)}
	aria-label={`Show full path for ${name}`}
	aria-haspopup="dialog"
	aria-expanded={location.open}
	aria-controls={id}
	class={`flex min-h-8 max-w-full min-w-0 items-center gap-1.5 rounded text-left ${prominent ? 'text-[13px] font-medium text-fg hover:text-accent' : 'shrink-0 rounded-md border border-line px-2 text-[11px] text-dim hover:bg-raised hover:text-fg'}`}
>
	{#if !prominent}
		<span class="flex-none text-faint"><Glyph name="folder" /></span>
	{/if}
	<span class="min-w-0 truncate">{name}</span>
	{#if prominent}
		<span class="flex-none text-faint"><Glyph name="doc" size={14} /></span>
	{/if}
</button>
<div
	bind:this={panel}
	{id}
	popover="manual"
	role="dialog"
	aria-label={`Full path for ${name}`}
	class={`fixed m-0 w-96 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-3 text-fg shadow-lg`}
>
	<div class="mb-2 flex items-center justify-between gap-3">
		<p class="text-[12px] font-medium">{prominent ? 'File path' : name}</p>
		<button
			type="button"
			onclick={() => location.lower()}
			aria-label="Close path"
			class="-my-1 -mr-1 flex min-h-8 min-w-8 items-center justify-center rounded-md text-faint hover:bg-sunken hover:text-fg"
			><Glyph name="cross" size={12} /></button
		>
	</div>
	<p
		class="max-h-[50dvh] overflow-y-auto rounded-md bg-sunken px-2.5 py-2 font-mono text-[11px] leading-relaxed break-all whitespace-pre-wrap text-dim select-text"
	>
		{path}
	</p>
</div>
