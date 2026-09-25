<script lang="ts">
	import Spinner from '$lib/components/Spinner.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { SPANS } from '$lib/pauses';
	import { refusalText } from '$lib/api';
	import { frosted } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';

	let {
		label,
		hint = '',
		disabled = false,
		class: shape = '',
		// The trigger edge the menu aligns to, read once.
		edge = 'left',
		onchoose
	}: {
		label: string;
		hint?: string;
		disabled?: boolean;
		class?: string;
		edge?: 'left' | 'right';
		onchoose: (seconds: number) => Promise<void>;
	} = $props();

	const id = $props.id();
	let trigger: HTMLButtonElement;
	let menu: HTMLDivElement;
	let busy = $state<number | null>(null);
	let error = $state('');
	// svelte-ignore state_referenced_locally
	const chooser = popover({ edge, lightDismiss: true });

	function toggle() {
		if (!chooser.open) error = '';
		void chooser.toggle(trigger, menu);
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

<button
	bind:this={trigger}
	onclick={toggle}
	{disabled}
	aria-label={`Pause ${label}`}
	aria-expanded={chooser.open}
	aria-controls={id}
	class={shape}
>
	<Glyph name="pause" /> Pause
</button>
<div
	bind:this={menu}
	{id}
	popover="manual"
	role="group"
	aria-label={`Pause ${label} for`}
	class={`fixed m-0 w-64 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-1 text-fg shadow-lg`}
>
	<p class="px-2.5 pt-2 pb-1.5 text-[11px] text-faint">Pause for</p>
	{#each SPANS as span (span.seconds)}
		<button
			onclick={() => choose(span.seconds)}
			disabled={busy !== null || disabled}
			aria-busy={busy === span.seconds}
			class="flex min-h-11 w-full items-center justify-between rounded-lg px-2.5 text-[13px] font-medium transition-colors hover:bg-sunken disabled:opacity-(--disabled)"
		>
			{busy === span.seconds ? 'Pausing…' : span.label}
			<Spinner busy={busy === span.seconds} />
		</button>
	{/each}
	{#if hint}<p class="border-t border-line px-2.5 py-2 text-[11.5px] leading-relaxed text-faint">
			{hint}
		</p>{/if}
	{#if error}<p role="alert" class="px-2.5 py-2 text-[12px] text-danger">{error}</p>{/if}
</div>
