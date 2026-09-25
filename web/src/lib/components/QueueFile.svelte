<script lang="ts">
	import { dated, named, duration, placeWord } from '$lib/format';
	import { dotted, fileRow, rowGlyph, rowMark, rowMenu } from '$lib/controls';
	import type { FileChanges, FileCover, QueueItem } from '$lib/queue';
	import { warm } from '$lib/plans.svelte';
	import { tint, tintOf } from '$lib/tint';
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
		// Whether a tap on the cover picks rather than opens the title.
		picking = false,
		selected = false,
		disabled = false,
		onopen,
		onpick,
		onhold,
		ontop,
		onskip,
		onpause
	}: {
		item: QueueItem;
		cover?: FileCover;
		plan?: FileChanges;
		current?: boolean;
		admin?: boolean;
		picking?: boolean;
		selected?: boolean;
		disabled?: boolean;
		onopen?: () => void;
		onpick: () => void;
		onhold?: () => void;
		/** Absent for the row already at the head, which has nowhere to move. */
		ontop?: () => Promise<void>;
		onskip: () => Promise<void>;
		onpause: (seconds: number) => Promise<void>;
	} = $props();
	const id = $props.id();
	// So the panel opens at its full height rather than growing into one. On the
	// path rather than the item, which is a new object every refresh.
	const warming = $derived(item.path);
	$effect(() => warm(warming));
	const title = $derived(named(item.path));
	const release = $derived(dated(title.detail));
	const shown = $derived(cover?.name || title.name);
	const label = $derived(`${title.name}${title.episode ? ` ${title.episode}` : ''}`);
	let open = $state(false);
	// Whether lifting now would pick this file, from the row or its cover.
	let armed = $state(false);
	// Whether a finger or button is down on either, which raises the whole row.
	let held = $state(false);
</script>

{#snippet selection()}
	<!-- The cover answers the row's presses: a tap picks or opens the title, a
	     hold starts picking without a reach back to the button. -->
	<FilePoster
		{cover}
		name={title.name}
		{onopen}
		{onpick}
		{disabled}
		selected={picking ? selected : undefined}
		onhold={admin ? onhold : undefined}
		onarm={(on) => (armed = on)}
		onpress={(on) => (held = on)}
	/>
{/snippet}

<!-- The `<li>` is the caller's, which is where the list animates it from. -->
<div
	class={`${fileRow({ picked: selected, held, armed })} relative py-3`}
	use:tint={tintOf(cover?.id)}
>
	<Disclosure
		{id}
		{open}
		ontoggle={() => (picking ? onpick() : (open = !open))}
		picked={picking ? selected : undefined}
		beside={selection}
		class="group block min-h-11 w-full text-left after:absolute after:inset-0 after:content-['']"
		mark={12}
		turn="half"
		align="start"
		panelClass="mt-3"
		hold={admin ? onhold : undefined}
		onarm={(on) => (armed = on)}
		onpress={(on) => (held = on)}
	>
		{#snippet summary()}
			<span class="block min-w-0">
				<span class="flex items-baseline gap-1.5 text-[14px] leading-snug font-medium">
					<span class="truncate" title={shown}>{shown}</span>
					{#if title.episode || release.year}<span
							class="flex-none text-[12.5px] font-normal text-faint tabular-nums"
							>{title.episode || release.year}</span
						>{/if}
				</span>
				{#if release.words}<span class={`mt-0.5 text-[12px] text-dim ${dotted}`}
						><span class="min-w-0 truncate">{release.words}</span></span
					>{/if}
				<span class={`mt-0.5 text-[12px] text-dim ${dotted}`}>
					<!-- The position in the whole queue, not in this filtered page. -->
					<span class={`flex-none ${item.position === 1 ? 'font-medium text-accent' : ''}`}
						>{placeWord(item.position)}</span
					>
					{#if item.expected}<span class="flex-none tabular-nums"
							>takes ~{duration(item.expected)}</span
						>{/if}
					<PlanLine {plan} {current} />
				</span>
			</span>
		{/snippet}
		{#snippet after(chevron)}
			<!-- The chevron shows only while picking, when a press on the row picks.
			     Deaf to presses, or the mark would swallow the hold that raised it. -->
			<span class="pointer-events-none flex flex-none items-start gap-1">
				{#if picking || armed}
					<span class={rowMark}><Tick on={selected || armed} /></span>
				{/if}
				{#if picking}
					<button
						type="button"
						onclick={() => (open = !open)}
						aria-expanded={open}
						aria-controls={id}
						aria-label={`Details for ${label}`}
						class={`group pointer-events-auto ${rowGlyph}`}>{@render chevron(true)}</button
					>
				{/if}
				{#if admin}
					<FileActions
						{label}
						{disabled}
						{ontop}
						{onskip}
						onchoose={onpause}
						hint="A later sweep can process these files after the pause ends."
						class={`pointer-events-auto ${rowMenu}`}
					/>
				{/if}
			</span>
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
</div>
