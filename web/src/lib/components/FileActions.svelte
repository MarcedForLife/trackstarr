<script lang="ts">
	import { tick } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { SPANS } from '$lib/pauses';
	import { refusalText } from '$lib/api';
	import { frosted } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';
	import { REPORT_ONLY_NOTE, type RunMode } from '$lib/library';

	let {
		label,
		hint = '',
		active = false,
		onskip,
		ontop,
		onrun,
		mayRewrite = false,
		runDisabled = false,
		refuses = '',
		disabled = false,
		class: shape = '',
		onchoose
	}: {
		label: string;
		hint?: string;
		active?: boolean;
		onskip?: () => void | Promise<void>;
		ontop?: () => void | Promise<void>;
		onrun?: (mode: RunMode) => Promise<void>;
		mayRewrite?: boolean;
		runDisabled?: boolean;
		refuses?: string;
		disabled?: boolean;
		class?: string;
		onchoose: (seconds: number) => Promise<void>;
	} = $props();

	const id = $props.id();
	let trigger: HTMLButtonElement;
	let menu: HTMLDivElement;
	let choosingPause = $state(false);
	let busy = $state<number | null>(null);
	let error = $state('');
	const chooser = popover();

	function toggle() {
		if (!chooser.open) {
			choosingPause = false;
			error = '';
		}
		void chooser.toggle(trigger, menu);
	}

	// The spans are a taller pane than the actions, so the menu is placed again.
	async function showSpans() {
		choosingPause = true;
		await tick();
		chooser.place();
		menu.querySelector('button')?.focus();
	}

	async function act(action: () => void | Promise<void>) {
		busy = -1;
		error = '';
		try {
			await action();
			chooser.lower();
		} catch (failure) {
			error = refusalText(failure);
		} finally {
			busy = null;
		}
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
		chooser.open && chooser.outside(event.target as Node) && chooser.lower()}
	onresize={() => chooser.open && chooser.lower()}
/>

<button
	bind:this={trigger}
	onclick={toggle}
	{disabled}
	aria-label={`Actions for ${label}`}
	title={`Actions for ${label}`}
	aria-expanded={chooser.open}
	aria-controls={id}
	class={shape}
>
	<span class="inline-flex min-w-7 items-center justify-center leading-none">
		<Glyph name="more" size={16} />
	</span>
</button>
<div
	bind:this={menu}
	{id}
	popover="manual"
	role="group"
	aria-label={`Actions for ${label}`}
	class={`pointer-events-auto fixed m-0 max-h-[calc(100dvh-1.5rem)] w-64 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl border border-line-strong ${frosted} p-1 text-fg shadow-lg`}
>
	{#if choosingPause}
		<!-- No way back: the spans are the whole pane, and a dismissed menu reopens
		     on the actions. -->
		<p class="px-2.5 pt-2 pb-1.5 text-[11px] text-faint">Pause for</p>
		{#each SPANS as span (span.seconds)}
			<button
				onclick={() => choose(span.seconds)}
				disabled={busy !== null || disabled}
				class="flex min-h-11 w-full items-center rounded-lg px-2.5 text-[13px] font-medium transition-colors hover:bg-sunken disabled:opacity-(--disabled)"
			>
				{busy === span.seconds ? 'Pausing…' : span.label}
			</button>
		{/each}
	{:else}
		{#if onrun}
			<button
				onclick={() => act(() => onrun!('report'))}
				disabled={busy !== null || disabled || runDisabled || !!refuses}
				class="flex min-h-11 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[13px] font-medium hover:bg-sunken disabled:opacity-(--disabled)"
				><Glyph name="doc" /> Plan</button
			>
			<button
				onclick={() => (mayRewrite ? act(() => onrun!('apply')) : (error = REPORT_ONLY_NOTE))}
				disabled={busy !== null || disabled || runDisabled || !!refuses}
				aria-disabled={!mayRewrite || undefined}
				class="flex min-h-11 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[13px] font-medium hover:bg-sunken disabled:opacity-(--disabled) aria-disabled:opacity-(--disabled)"
				><Glyph name="play" /> Process</button
			>
			{#if refuses}<p class="px-2.5 py-2 text-[12px] text-faint">{refuses}</p>{/if}
		{/if}
		{#if ontop}<button
				onclick={() => act(ontop!)}
				disabled={busy !== null || disabled}
				class="min-h-11 w-full rounded-lg px-2.5 text-left text-[13px] font-medium hover:bg-sunken"
				>Prioritise</button
			>{/if}
		<button
			onclick={showSpans}
			disabled={busy !== null || disabled}
			class="flex min-h-11 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[13px] font-medium hover:bg-sunken"
			><Glyph name="pause" /> Pause…</button
		>
		{#if onskip}<button
				onclick={() => act(onskip!)}
				disabled={busy !== null || disabled}
				class="min-h-11 w-full rounded-lg border-t border-line px-2.5 text-left text-[13px] font-medium text-danger hover:bg-sunken"
				>{active ? 'Cancel' : 'Skip'}</button
			>{/if}
	{/if}
	{#if choosingPause && hint}<p
			class="border-t border-line px-2.5 py-2 text-[11.5px] leading-relaxed text-faint"
		>
			{hint}
		</p>{/if}
	{#if error}<p role="alert" class="px-2.5 py-2 text-[12px] text-danger">{error}</p>{/if}
</div>
