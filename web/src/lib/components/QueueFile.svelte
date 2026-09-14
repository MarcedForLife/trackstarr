<script lang="ts">
	import { named, duration } from '$lib/format';
	import { fileRow } from '$lib/controls';
	import type { FileChanges, FileCover, QueueItem } from '$lib/queue';
	import { warm } from '$lib/plans.svelte';
	import FilePlan from './FilePlan.svelte';
	import PlanLine from './PlanLine.svelte';
	import Disclosure from './Disclosure.svelte';
	import FileActions from './FileActions.svelte';
	import FilePoster from './FilePoster.svelte';
	import Tick from './Tick.svelte';

	let {
		item,
		cover,
		// What a rewrite would do to this file, absent until a sweep has judged it.
		plan,
		// False where the rules have moved on since that check, which is the
		// whole page's answer and not this row's.
		current = true,
		admin = false,
		selected = false,
		disabled = false,
		onopen,
		onpick,
		ontop,
		onskip,
		onpause
	}: {
		item: QueueItem;
		cover?: FileCover;
		plan?: FileChanges;
		current?: boolean;
		admin?: boolean;
		selected?: boolean;
		disabled?: boolean;
		onopen?: () => void;
		onpick: () => void;
		ontop: () => Promise<void>;
		onskip: () => Promise<void>;
		onpause: (seconds: number) => Promise<void>;
	} = $props();
	const id = $props.id();
	// So the panel opens at its full height rather than growing into one. On the
	// path rather than the item, which is a new object every refresh.
	const warming = $derived(item.path);
	$effect(() => warm(warming));
	const title = $derived(named(item.path));
	const shown = $derived(cover?.name || title.name);
	const label = $derived(`${title.name}${title.episode ? ` ${title.episode}` : ''}`);
	let open = $state(false);
</script>

{#snippet selection()}
	<!-- Stretched, so the cover centres against the row rather than the tick. -->
	<div class="flex shrink-0 items-stretch gap-2 sm:gap-3">
		{#if admin}
			<!-- The grid's own mark, so picking files reads like picking titles.
			     Padded out from the 20px disc to a thumb's worth of target. -->
			<button
				type="button"
				role="checkbox"
				aria-checked={selected}
				aria-label={`Select ${label}`}
				{disabled}
				onclick={onpick}
				class="mt-2 -ml-2 flex-none p-2 disabled:opacity-(--disabled)"
			>
				<Tick on={selected} />
			</button>
		{/if}
		<FilePoster {cover} name={title.name} {onopen} />
	</div>
{/snippet}

<li class={`${fileRow(selected)} py-3`}>
	<Disclosure
		{id}
		{open}
		ontoggle={() => (open = !open)}
		beside={selection}
		class="group block min-h-11 w-full text-left"
		mark={12}
		turn="half"
		panelClass="mt-3"
	>
		{#snippet summary(chevron)}
			<span class="flex items-center gap-2 sm:gap-3">
				<span class="min-w-0 flex-1">
					<!-- One line, so a list stands to one height. -->
					<span class="block truncate text-[13px] leading-snug font-medium" title={shown}
						>{shown}</span
					>
					<span class="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
						<!-- Where it sits in the whole queue, which is not its place in
						     this page once a search has cut one. -->
						<span class="font-mono text-[11px] whitespace-nowrap text-faint tabular-nums"
							>#{item.position}</span
						>
						{#if title.episode}<span
								class="font-mono text-[12px] font-medium whitespace-nowrap text-dim"
								>{title.episode}</span
							>{/if}
						{#if item.expected}<span class="text-[11px] whitespace-nowrap text-faint"
								>~{duration(item.expected)}</span
							>{/if}
						<!-- Which file of this title, where it has more than one. -->
						{#if title.detail}<span class="text-[11px] text-faint">{title.detail}</span>{/if}
					</span>
					<!-- The plan takes a line of its own. The cover runs the height of
					     whatever that leaves. -->
					<PlanLine {plan} {current} />
				</span>
				{@render chevron()}
			</span>
		{/snippet}
		{#snippet after()}
			{#if admin}
				<FileActions
					{label}
					{disabled}
					{ontop}
					{onskip}
					onchoose={onpause}
					hint="A later sweep can process these files after the pause ends."
					class="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-lg px-2 text-[12px] font-medium text-dim hover:bg-sunken"
				/>
			{/if}
		{/snippet}
		{#snippet panel()}
			<div class="rounded-lg border border-line bg-sunken px-3 py-2.5">
				<p class="mb-1 text-[11px] font-medium text-dim">File path</p>
				<p class="font-mono text-[11px] leading-relaxed wrap-anywhere text-faint select-text">
					{item.path}
				</p>
				<div class="mt-3 border-t border-line pt-3">
					<FilePlan path={item.path} />
				</div>
			</div>
		{/snippet}
	</Disclosure>
</li>
