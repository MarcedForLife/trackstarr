<script lang="ts">
	import FileAccount, { type Editing } from './FileAccount.svelte';
	import FileWork from './FileWork.svelte';
	import QueuePlace from './QueuePlace.svelte';
	import { ago } from '$lib/events';
	import { size, verdictLabel, verdictText, type LibraryFile } from '$lib/library';
	import { fileState, type TitleWork } from '$lib/queue';

	// One file of a title: what it is and where the queue has it, over the
	// account every panel shows.

	let {
		file,
		// The title holds more than one file, so this one carries its own queue
		// controls.
		manyFiles = false,
		admin = false,
		work,
		unavailable = false,
		busy = $bindable(false),
		onchanged,
		editing = null,
		// The title's other files, for an edit offered across twins.
		siblings = [],
		series = false
	}: {
		file: LibraryFile;
		manyFiles?: boolean;
		admin?: boolean;
		work?: TitleWork;
		unavailable?: boolean;
		busy?: boolean;
		onchanged: () => Promise<void>;
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

	// What the rewrite did to the file's size, as the history's chips spell it.
	const shrank = $derived.by(() => {
		const { bytes_before: before, bytes_after: after } = file.modified ?? {};
		if (before === undefined || after === undefined) return '';
		const delta = after - before;
		return `${delta < 0 ? '−' : '+'}${size(delta)}`;
	});
</script>

<li class="rounded-xl border border-line bg-sunken p-3">
	{#if manyFiles}
		<FileWork
			path={file.path}
			{standing}
			ready={!!work}
			{admin}
			{unavailable}
			bind:busy
			{onchanged}
		>
			{@render head()}
		</FileWork>
	{:else}
		{@render head()}
	{/if}
	<FileAccount {file} {editing} {siblings} {series} />
</li>

<!-- Left-aligned under the name at one x, which reads down a season faster than
     labels ranged off a ragged right edge. A flex row rather than a run of text:
     whitespace between two blocks is the one thing a template cannot be held
     to. -->
{#snippet head()}
	<div class="min-w-0 flex-1">
		<p class="truncate text-[13px] font-medium" title={file.name}>{file.name}</p>
		<p class="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 text-[11.5px] text-faint">
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
			{#if file.modified}
				<span aria-hidden="true">·</span>
				<span class="text-ok">Modified {ago(file.modified.at)}</span>
				{#if shrank}
					<span class="font-mono">{shrank}</span>
				{/if}
			{/if}
		</p>
	</div>
{/snippet}
