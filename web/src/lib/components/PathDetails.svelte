<script lang="ts">
	import { frosted } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';
	import Glyph from './Glyph.svelte';

	let {
		path,
		label,
		// Only the glyph, beside a name the caller already shows.
		glyph = false
	}: {
		path: string;
		label?: string;
		glyph?: boolean;
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
	class={glyph
		? "relative -my-0.5 ml-1 inline-flex h-9 w-9 flex-none items-center justify-center rounded-full text-faint transition-colors after:absolute after:-inset-1 after:content-[''] hover:bg-raised hover:text-fg"
		: 'flex min-h-8 max-w-full min-w-0 shrink-0 items-center gap-1.5 rounded-md border border-line px-2 text-left text-[11px] text-dim hover:bg-raised hover:text-fg'}
>
	{#if glyph}
		<Glyph name="doc" size={14} />
	{:else}
		<span class="flex-none text-faint"><Glyph name="folder" /></span>
		<span class="min-w-0 truncate">{name}</span>
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
		<p class="text-[12px] font-medium">{glyph ? 'File path' : name}</p>
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
