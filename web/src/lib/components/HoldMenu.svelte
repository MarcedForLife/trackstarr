<script lang="ts">
	import { tick } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { SPANS } from '$lib/holds';
	import { refusalText } from '$lib/api';
	import { overlay } from '$lib/overlay';

	let {
		label,
		hint = '',
		disabled = false,
		class: shape = '',
		onchoose
	}: {
		label: string;
		hint?: string;
		disabled?: boolean;
		class?: string;
		onchoose: (seconds: number) => Promise<void>;
	} = $props();

	const id = $props.id();
	let trigger: HTMLButtonElement;
	let menu: HTMLDivElement;
	let open = $state(false);
	let busy = $state<number | null>(null);
	let error = $state('');
	let left = $state(0);
	let top = $state(0);
	const chooser = overlay({
		close: () => {
			open = false;
			menu?.hidePopover();
			trigger?.focus();
		}
	});

	async function toggle() {
		if (open) {
			chooser.lower();
			return;
		}
		open = true;
		error = '';
		chooser.raise();
		await tick();
		menu.showPopover();
		const rect = trigger.getBoundingClientRect();
		left = Math.max(12, Math.min(rect.left, innerWidth - menu.offsetWidth - 12));
		top = Math.max(12, Math.min(rect.bottom + 8, innerHeight - menu.offsetHeight - 12));
		menu.querySelector('button')?.focus();
	}

	async function choose(seconds: number) {
		busy = seconds;
		error = '';
		try {
			await onchoose(seconds);
			chooser.lower();
		} catch (failure) {
			error = refusalText(failure);
		} finally {
			busy = null;
		}
	}
</script>

<svelte:window
	onpointerdown={(event) =>
		open &&
		!menu?.contains(event.target as Node) &&
		!trigger?.contains(event.target as Node) &&
		chooser.lower()}
	onresize={() => open && chooser.lower()}
/>

<button
	bind:this={trigger}
	onclick={toggle}
	{disabled}
	aria-label={`Hold ${label}`}
	aria-expanded={open}
	aria-controls={id}
	class={shape}
>
	<Glyph name="pause" /> Hold
</button>
<div
	bind:this={menu}
	{id}
	popover="manual"
	role="group"
	aria-label={`Hold ${label} for`}
	style:left={`${left}px`}
	style:top={`${top}px`}
	class="menu fixed m-0 w-64 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong bg-raised p-1 text-fg shadow-lg"
>
	<p class="px-2.5 pt-1.5 pb-1 text-[11px] text-faint">Hold for</p>
	{#each SPANS as span (span.seconds)}
		<button
			onclick={() => choose(span.seconds)}
			disabled={busy !== null || disabled}
			class="flex min-h-11 w-full items-center rounded-lg px-2.5 text-[13px] font-medium transition-colors hover:bg-sunken disabled:opacity-(--disabled)"
		>
			{busy === span.seconds ? 'Holding…' : span.label}
		</button>
	{/each}
	{#if hint}<p class="border-t border-line px-2.5 py-2 text-[11.5px] leading-relaxed text-faint">
			{hint}
		</p>{/if}
	{#if error}<p role="alert" class="px-2.5 py-2 text-[12px] text-danger">{error}</p>{/if}
</div>
