<script lang="ts">
	import Disclosure from './Disclosure.svelte';
	import PathDetails from './PathDetails.svelte';
	import type { Runner } from './TitleSheet.svelte';
	import FileAccount, { type Editing } from './FileAccount.svelte';
	import type { FileActionAdapter } from '$lib/title-work.svelte';
	import FileWork from './FileWork.svelte';
	import QueuePlace from './QueuePlace.svelte';
	import { ago } from '$lib/events';
	import { named, titled } from '$lib/format';
	import { size, verdictLabel, verdictText, type LibraryFile } from '$lib/library';
	import { fileState, type TitleWork } from '$lib/queue';

	// One file of a title: what it is and where the queue has it, over the
	// account every panel shows.

	let {
		file,
		// The title holds more than one file, so this one carries its own queue
		// controls.
		manyFiles = false,
		embedded = false,
		sourced = false,
		admin = false,
		work,
		runner,
		unavailable = false,
		busy = $bindable(false),
		onchanged,
		onaction,
		editing = null,
		// The title's other files, for an edit offered across twins.
		siblings = [],
		series = false
	}: {
		file: LibraryFile;
		manyFiles?: boolean;
		embedded?: boolean;
		// The title is held in more than one instance, so the row names the file's.
		sourced?: boolean;
		admin?: boolean;
		work?: TitleWork;
		runner?: Runner;
		unavailable?: boolean;
		busy?: boolean;
		onchanged: () => Promise<void>;
		onaction?: FileActionAdapter;
		editing?: Editing | null;
		siblings?: LibraryFile[];
		series?: boolean;
	} = $props();

	// The queue's word leads the row in place of the verdict, which a file being
	// worked on has outgrown. A film says both in its header and its one row says
	// neither; see `manyFiles` below.
	const standing = $derived(fileState(work, file.path));
	const led = $derived(standing.label || verdictLabel(file.status));
	// Accent for work under way. Queued and paused are states, not things
	// happening; a place at the head lights itself.
	const tone = $derived(
		standing.label
			? standing.running.length
				? 'text-accent'
				: 'text-dim'
			: (verdictText[file.status] ?? 'text-dim')
	);

	// Films start open, episodes folded. Folding cancels an open tag form, or the
	// sheet would hold Escape for a hidden one.
	// svelte-ignore state_referenced_locally
	let open = $state(!series);
	const id = $props.id();
	function toggle() {
		if (open && editing?.at?.path === file.path) editing.cancel();
		open = !open;
	}

	// The sheet names the series, so an episode leads with its number and an
	// extra with its name.
	const episode = $derived(series ? named(file.path) : null);

	// A refused corner press, shown under the name.
	let error = $state('');

	// What the rewrite did to the file's size, as the history's chips spell it.
	const shrank = $derived.by(() => {
		const { bytes_before: before, bytes_after: after } = file.modified ?? {};
		if (before === undefined || after === undefined) return '';
		const delta = after - before;
		return `${delta < 0 ? '−' : '+'}${size(delta)}`;
	});
</script>

<!-- Positioned, so the name's press can cover the head. -->
<svelte:element
	this={embedded ? 'div' : 'li'}
	class={`relative ${embedded ? 'p-3' : 'rounded-xl border border-line bg-sunken p-3'}`}
>
	<!-- The press covers the head. The path glyph and queue controls sit above
	     it. -->
	<Disclosure
		id={`file-${id}`}
		{open}
		ontoggle={toggle}
		class="group flex min-h-8 min-w-0 items-center gap-1.5 text-left after:absolute after:inset-0 after:content-['']"
		mark={12}
		align="start"
		panelClass=""
	>
		{#snippet summary(chevron)}
			<span class="flex text-faint transition-colors group-hover:text-fg"
				>{@render chevron(true)}</span
			>
			<span class="min-w-0 truncate text-[13px] font-medium">
				{#if episode?.episode}<span class="mr-1.5 text-dim tabular-nums">{episode.episode}</span
					>{episode.episodeName ?? ''}{:else}{series ? titled(file.path) : file.name}{/if}
			</span>
		{/snippet}
		{#snippet trail()}
			<PathDetails path={file.path} label={file.name} glyph />
		{/snippet}
		{#snippet after()}
			<FileWork
				path={file.path}
				{standing}
				ready={!!work}
				{admin}
				{unavailable}
				bind:busy
				bind:error
				{onchanged}
				{onaction}
				onrun={runner?.onfile ? (mode) => runner!.onfile!(file.path, mode) : undefined}
				mayRewrite={runner?.mayRewrite ?? false}
				runDisabled={!!runner?.starting || !!runner?.run}
				refuses={runner?.refuses ?? ''}
			/>
		{/snippet}
		{#snippet aside()}
			{@render facts()}
			{#if error}<p role="alert" class="text-[12px] text-danger">{error}</p>{/if}
		{/snippet}
		{#snippet panel()}
			<FileAccount {file} {editing} {siblings} {series} />
		{/snippet}
	</Disclosure>
</svelte:element>

<!-- Aligned under the name past the chevron, which scans down a season faster
     than a ragged right edge. A flex row, since template whitespace between
     blocks is unreliable. -->
{#snippet facts()}
	<p class="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 pl-[18px] text-[11.5px] text-faint">
		{#if manyFiles}
			<span class={`font-semibold ${tone}`}>{led}</span>
			{#if standing.place}
				<QueuePlace place={standing.place} />
				{#if standing.waiting.length > 1}
					<span aria-hidden="true">·</span>
					<span>{standing.waiting.length} runs</span>
				{/if}
			{/if}
			<span aria-hidden="true">·</span>
		{/if}
		<span>{size(file.bytes)}</span>
		{#if (sourced || embedded) && file.source}
			<span aria-hidden="true">·</span>
			<span>{file.source}</span>
		{/if}
		{#if file.modified}
			<span aria-hidden="true">·</span>
			<span class="text-ok">Modified {ago(file.modified.at)}</span>
			{#if shrank}
				<span class="font-mono">{shrank}</span>
			{/if}
		{/if}
	</p>
{/snippet}
