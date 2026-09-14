<script lang="ts">
	import { named, duration, stamp } from '$lib/format';
	import type { Pause } from '$lib/pauses';
	import type { FileCover } from '$lib/queue';
	import Disclosure from './Disclosure.svelte';
	import FilePoster from './FilePoster.svelte';
	import FilePlan from './FilePlan.svelte';
	import { fileRow } from '$lib/controls';

	let {
		pause,
		cover,
		admin = false,
		disabled = false,
		resuming = false,
		onopen,
		onresume
	}: {
		pause: Pause;
		cover?: FileCover;
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

<li class={`${fileRow()} py-3`}>
	<Disclosure
		{id}
		{open}
		ontoggle={() => (open = !open)}
		class="group block min-h-11 w-full text-left"
		mark={10}
		panelClass="mt-3"
		beside={poster}
	>
		{#snippet summary(chevron)}
			<span class="flex items-center gap-3">
				<span class="min-w-0 flex-1">
					<!-- One line, so a list stands to one height. -->
					<span class="block truncate text-[13px] font-medium" title={shown}>{shown}</span>
					<span class="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px] text-faint">
						{#if !pause.title && title.episode}<span
								class="font-mono text-[12px] font-medium text-dim">{title.episode}</span
							>{/if}
						<span
							>{pause.seconds !== null ? `${duration(pause.seconds)} left` : 'Until resumed'}</span
						>
					</span>
				</span>
				{@render chevron()}
			</span>
		{/snippet}
		{#snippet after()}
			{#if admin}<button
					onclick={onresume}
					{disabled}
					aria-label={`Resume ${pause.name || title.name}`}
					class="min-h-11 shrink-0 rounded-lg px-2 text-[12px] font-medium text-accent hover:bg-sunken disabled:opacity-(--disabled)"
					>{resuming ? 'Resuming…' : 'Resume'}</button
				>{/if}
		{/snippet}
		{#snippet panel()}
			<div
				class="rounded-lg border border-line bg-sunken px-3 py-2.5 text-[12px] leading-relaxed text-dim"
			>
				{#if pause.reason}<p class="mb-2 wrap-anywhere">{pause.reason}</p>{/if}
				<p>Paused{pause.by ? ` by ${pause.by}` : ''}{pause.at ? ` · ${stamp(pause.at)}` : ''}</p>
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
</li>
