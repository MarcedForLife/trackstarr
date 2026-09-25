<script lang="ts">
	import Spinner from '$lib/components/Spinner.svelte';
	import { dated, named, duration, stamp } from '$lib/format';
	import type { Pause } from '$lib/pauses';
	import type { FileCover } from '$lib/queue';
	import Disclosure from './Disclosure.svelte';
	import FilePoster from './FilePoster.svelte';
	import FilePlan from './FilePlan.svelte';
	import PlanLine from './PlanLine.svelte';
	import type { FileChanges } from '$lib/queue';
	import { dotted, fileRow, rowWord } from '$lib/controls';
	import { tint, tintOf } from '$lib/tint';

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
	// A title-wide pause has no release words.
	const release = $derived(pause.title_id ? { year: '', words: '' } : dated(title.detail));
	const art = $derived(
		cover ?? (pause.title_id ? { id: pause.title_id, name: pause.title_name } : undefined)
	);
	const shown = $derived(art?.name || pause.title_name || title.name);
	let open = $state(false);
</script>

{#snippet poster()}<FilePoster cover={art} name={title.name} {onopen} />{/snippet}

<!-- The `<li>` is the caller's, which is where the list animates it from. -->
<div class={`${fileRow()} py-3 text-[12px]`} use:tint={tintOf(art?.id)}>
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
		{#snippet summary()}
			<!-- The heading already says paused, so the last line starts with how long. -->
			<span class="block min-w-0">
				<span class="flex items-baseline gap-1.5 text-[14px] leading-snug font-medium text-fg">
					<span class="truncate" title={shown}>{shown}</span>
					{#if !pause.title_id && (title.episode || release.year)}<span
							class="flex-none text-[12.5px] font-normal text-faint tabular-nums"
							>{title.episode || release.year}</span
						>{/if}
				</span>
				{#if release.words}<span class={`mt-0.5 text-[12px] text-dim ${dotted}`}
						><span class="min-w-0 truncate">{release.words}</span></span
					>{/if}
				<span class={`mt-0.5 text-[12px] text-dim ${dotted}`}>
					<span class="flex-none tabular-nums"
						>{pause.seconds !== null ? `${duration(pause.seconds)} left` : 'Until resumed'}</span
					>
					<!-- A folder has nothing judged under it to draw. -->
					{#if !pause.title_id}<PlanLine {plan} {current} />{/if}
				</span>
			</span>
		{/snippet}
		{#snippet after()}
			{#if admin}<button
					onclick={onresume}
					{disabled}
					aria-label={`Resume ${pause.title_name || title.name}`}
					aria-busy={resuming}
					class={`${rowWord} gap-1.5`}
					><Spinner busy={resuming} />{resuming ? 'Resuming…' : 'Resume'}</button
				>{/if}
		{/snippet}
		{#snippet panel()}
			<div
				class="rounded-lg border border-line bg-sunken px-3 py-2.5 text-[12px] leading-relaxed text-dim"
			>
				<p>Paused{pause.at ? ` · ${stamp(pause.at)}` : ''}</p>
				{#if pause.by}<p>By {pause.by}</p>{/if}
				<p class="mb-3">
					{pause.until ? `Pause ends ${stamp(pause.until)}.` : 'Paused until you resume.'}
				</p>
				{#if !pause.title_id}<FilePlan path={pause.path} />{/if}
				<p class="mb-1 border-t border-line pt-3 text-[11px] font-medium">
					{pause.title_id ? 'Title folder' : 'File path'}
				</p>
				<p class="font-mono text-[11px] wrap-anywhere text-faint select-text">{pause.path}</p>
			</div>
		{/snippet}
	</Disclosure>
</div>
