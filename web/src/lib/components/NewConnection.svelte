<script lang="ts">
	import Glyph from '$lib/components/Glyph.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import { SERVICES, type ServiceName } from '$lib/connections';
	import { button, frosted } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';

	// The four services a card can be made for. An *arr can be added again as a
	// named instance; a media server is one card, so its row dims once it has one.
	let {
		added,
		onpick
	}: {
		// The media servers that already have a card.
		added: Set<string>;
		onpick: (name: ServiceName) => void;
	} = $props();

	const id = $props.id();
	let trigger: HTMLButtonElement;
	let menu: HTMLDivElement;
	const chooser = popover({ edge: 'left' });

	function pick(name: ServiceName) {
		chooser.lower();
		onpick(name);
	}
</script>

<svelte:window
	onpointerdown={(event) =>
		chooser.open && chooser.outside(event.target as Node) && chooser.lower()}
	onresize={() => chooser.open && chooser.lower()}
/>

<button
	bind:this={trigger}
	type="button"
	onclick={() => chooser.toggle(trigger, menu)}
	aria-expanded={chooser.open}
	aria-controls={id}
	class={button}
>
	<Glyph name="plus" /> New connection
</button>
<div
	bind:this={menu}
	{id}
	popover="manual"
	role="group"
	aria-label="New connection"
	class={`fixed m-0 w-64 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-1 text-fg shadow-lg`}
>
	{#each SERVICES as service (service.name)}
		{@const taken = added.has(service.name)}
		<button
			type="button"
			onclick={() => pick(service.name as ServiceName)}
			disabled={taken}
			class="flex min-h-11 w-full items-center gap-3 rounded-lg px-2.5 text-left text-[13px] font-medium transition-colors hover:bg-sunken disabled:opacity-(--disabled)"
		>
			<ServiceIcon name={service.name as ServiceName} size={16} box={24} />
			<span class="flex-1">{service.label}</span>
			{#if taken}<span class="text-[11px] font-normal text-faint">Added</span>{/if}
		</button>
	{/each}
</div>
