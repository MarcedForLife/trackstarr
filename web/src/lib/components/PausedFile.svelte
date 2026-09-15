<script lang="ts">
	import { named, duration, stamp } from '$lib/format';
	import type { Pause } from '$lib/pauses';
	import type { FileCover } from '$lib/queue';
	import Disclosure from './Disclosure.svelte';
	import FilePoster from './FilePoster.svelte';
	import FilePlan from './FilePlan.svelte';
	import PlanLine from './PlanLine.svelte';
	import type { FileChanges } from '$lib/queue';
	import { fileRow, rowWord } from '$lib/controls';

	let {
		pause,
		cover,
		// What a rewrite would do, kept from the queue while the file waits out
		// its pause. A title folder has none.
		plan,
		current = true,
		admin = false,
		disabled = false,
		resuming = false,
		onopen,
		onresume
	}: {
		pause: Pause;
		cover?: FileCover;
		plan?: FileChanges;
		current?: boolean;
		admin?: boolean;
		disabled?: boolean;
		resuming?: boolean;
		onresume: () => void;
		onopen?: () => void;
	} = $props();
	const id = $props.id();
	const title = $derived(named(pause.path));
	const art = $derived(cover ?? (pause.title ? { id: pause.title, name: pause.name } : undefined));
	const shown = $derived(art?.name || pause.name || title.name);
	let open = $state(false);
</script>

{#snippet poster()}<FilePoster cover={art} name={title.name} {onopen} />{/snippet}

<!-- The `<li>` is the caller's, which is where the list animates it from. -->
<div class={`${fileRow()} py-3 text-[12px]`}>
	<Disclosure
		{id}
		{open}
		ontoggle={() => (open = !open)}
		class="group block min-h-11 w-full text-left"
		mark={12}
		turn="half"
		align="start"
		panelClass="mt-3"
		beside={poster}
	>
		{#snippet summary(chevron)}
			<span class="flex items-start gap-3">
				<span class="min-w-0 flex-1">
					<!-- The title alone, as a working row draws it: its episode beside
					     it, the rest on the line under. -->
					<span class="flex items-baseline gap-1.5 text-[13px] leading-snug font-medium text-fg">
						<span class="truncate" title={shown}>{shown}</span>
						{#if !pause.title && title.episode}<span class="flex-none text-dim"
								>{title.episode}</span
							>{/if}
					</span>
					<!-- One line: the release words truncate rather than wrap. -->
					<span class="mt-1 flex items-center gap-x-2 overflow-hidden text-[11.5px] text-dim">
						<span class="flex-none text-faint tabular-nums"
							>{pause.seconds !== null ? `${duration(pause.seconds)} left` : 'Until resumed'}</span
						>
						<!-- Last, so it is the part that gives way. -->
						{#if !pause.title && title.detail}<span class="min-w-0 truncate text-faint"
								>{title.detail}</span
							>{/if}
					</span>
					<!-- A folder has nothing judged under it to draw. -->
					{#if !pause.title}<PlanLine {plan} {current} />{/if}
				</span>
				{@render chevron()}
			</span>
		{/snippet}
		{#snippet after()}
			{#if admin}<button
					onclick={onresume}
					{disabled}
					aria-label={`Resume ${pause.name || title.name}`}
					class={rowWord}>{resuming ? 'Resuming…' : 'Resume'}</button
				>{/if}
		{/snippet}
		{#snippet panel()}
			<div
				class="rounded-lg border border-line bg-sunken px-3 py-2.5 text-[12px] leading-relaxed text-dim"
			>
				{#if pause.reason}<p class="mb-2 wrap-anywhere">{pause.reason}</p>{/if}
				<p>Paused{pause.at ? ` · ${stamp(pause.at)}` : ''}</p>
				{#if pause.by}<p>By {pause.by}</p>{/if}
				<p class="mb-3">
					{pause.until ? `Pause ends ${stamp(pause.until)}.` : 'Paused until you resume.'}
				</p>
				{#if !pause.title}<FilePlan path={pause.path} />{/if}
				<p class="mb-1 border-t border-line pt-3 text-[11px] font-medium">
					{pause.title ? 'Title folder' : 'File path'}
				</p>
				<p class="font-mono text-[11px] wrap-anywhere text-faint select-text">{pause.path}</p>
			</div>
		{/snippet}
	</Disclosure>
</div>
