<script module lang="ts">
	import type { LibraryFile, Listed } from '$lib/library';
	import type { Outcome, Summary } from '$lib/retag';

	/** Inline tag editing as the sheet runs it. Which row is open stays up there:
	 * Escape closes the editor ahead of the sheet, and focus goes back to the row
	 * that opened it. Null where tags cannot be edited at all. */
	export type Editing = {
		at: { path: string; stream: number } | null;
		told: ({ path: string; stream: number } & Summary) | null;
		languages: Record<string, string> | null;
		open: (file: LibraryFile, row: Listed, pressed: HTMLElement) => void;
		done: (file: LibraryFile, row: Listed, outcomes: Outcome[]) => void;
		cancel: () => void;
	};
</script>

<script lang="ts">
	import FileWork from './FileWork.svelte';
	import Glyph from './Glyph.svelte';
	import TrackEditor from './TrackEditor.svelte';
	import { ago } from '$lib/events';
	import { badges, bytesFor, carriesLanguage, describe, rate } from '$lib/format';
	import { listing, size, verdictLabel, verdictText, type Track } from '$lib/library';
	import type { TitleWork } from '$lib/queue';
	import { editable, matching } from '$lib/retag';

	// One file of a title: what it is, what a rewrite would leave, and why.

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

	const shown = $derived(listing(file));

	// The same kinds the row's own line says a language on.
	function tagged(row: Listed): boolean {
		return carriesLanguage(row.track.kind);
	}

	// A track the file holds now, of a kind the rules read tags on, in a container
	// the service edits in place.
	function edits(row: Listed): boolean {
		return !!editing && row.stream !== null && tagged(row) && editable(file);
	}

	// The one reason a row can carry, said once under the heading for a phone and
	// on each row for a pointer.
	const MKV_ONLY = 'Tags are edited in place on .mkv files only. The remux rule converts this one.';

	const locked = $derived(!!editing && !editable(file) && shown.rows.some(tagged));

	function openedFor(row: Listed): boolean {
		const at = editing?.at;
		return !!at && at.path === file.path && at.stream === row.stream;
	}

	// What the last edit came to, under the row it was made from.
	function toldFor(row: Listed) {
		const told = editing?.told;
		return told && told.path === file.path && told.stream === row.stream ? told : null;
	}

	// The track as the file has it, rather than as a plan would leave it: a plan
	// clears titles and converts subtitles, and neither is on disk yet.
	function onDisk(row: Listed): Track {
		return file.tracks.find((track) => track.index === row.stream) ?? row.track;
	}

	const KIND_LETTER: Record<string, string> = {
		video: 'V',
		audio: 'A',
		subtitle: 'S',
		attachment: 'F'
	};

	// What a track takes of the file: its rate over the running time. Video and
	// audio only, since a subtitle is tens of kilobytes whatever the film.
	function weighs(track: Track): number {
		if (track.kind !== 'video' && track.kind !== 'audio') return 0;
		return bytesFor(track.bitrate, file.seconds);
	}

	// The number the row leads with, padded to the width of the longest a file is
	// likely to reach so the kind letters line up beneath each other. Blank for a
	// track the rewrite drops, which is what having no place looks like.
	function place(row: Listed): string {
		return (row.position === null ? '' : `[${row.position}]`).padStart(4);
	}

	// The verb a change opens with and the mark that stands for it. The planner
	// writes these strings to a form, so the first word is the verb; see _record
	// in planner.py. A clear takes a title away, which is a drop of the only thing
	// it had. The plus and minus are the pair `shrank` already uses.
	const CHANGE_MARKS: [string, string][] = [
		['add', '+'],
		['drop', '−'],
		['clear', '−'],
		['regenerate', '~'],
		['replace', '~']
	];

	// A remux or a reorder is none of the three, and keeps the plain mark.
	function changeMark(text: string): string {
		const verb = text.slice(0, text.indexOf(' '));
		return CHANGE_MARKS.find(([opener]) => opener === verb)?.[1] ?? '›';
	}

	// One change per line; run together they were a paragraph nobody finished.
	// `mark` says what the change does to the file and `rides` whether the rules
	// chose it, so neither has to carry the other's meaning.
	const changes = $derived([
		...(file.why.reasons ?? []).map((text) => ({ text, mark: changeMark(text), rides: false })),
		// Marked apart by the colour: these never cause a rewrite on their own.
		...(file.why.incidental ?? []).map((text) => ({ text, mark: changeMark(text), rides: true }))
	]);

	// What the rewrite did to the file's size, as the history's chips spell it.
	const shrank = $derived.by(() => {
		const { bytes_before: before, bytes_after: after } = file.modified ?? {};
		if (before === undefined || after === undefined) return '';
		const delta = after - before;
		return `${delta < 0 ? '−' : '+'}${size(delta)}`;
	});
</script>

<li class="rounded-xl border border-line bg-sunken p-3">
	<div class="flex items-baseline gap-2">
		<p class="min-w-0 flex-1 truncate text-[13px] font-medium" title={file.name}>
			{file.name}
		</p>
		<span class={`flex-none text-[11px] font-semibold ${verdictText[file.status] ?? 'text-dim'}`}>
			{verdictLabel(file.status)}
		</span>
	</div>
	{#if manyFiles}
		<FileWork path={file.path} {work} {admin} {unavailable} bind:busy {onchanged} />
	{/if}
	<!-- The whole of what a rewrite of ours left behind: when, and what it cost.
	     What it changed is the list below, where any track moved, and the history
	     page where none did. A flex row rather than a run of text: whitespace
	     between two blocks is the one thing a template cannot be held to. -->
	<p class="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 text-[11.5px] text-faint">
		<span>{size(file.bytes)}</span>
		{#if file.modified}
			<span aria-hidden="true">·</span>
			<span class="text-ok">Modified {ago(file.modified.at)}</span>
			{#if shrank}
				<span class="font-mono">{shrank}</span>
			{/if}
		{/if}
	</p>

	{#if shown.rows.length}
		<!-- One list, the whole width, in the order the file ends up in. A rewrite
		     copies far more than it touches, so two columns were mostly the same
		     list twice. -->
		<div class="mt-3">
			<p class="pb-1 text-[10.5px] font-semibold tracking-wider text-faint uppercase">
				{shown.label}
			</p>
			{#if locked}
				<p class="mb-1.5 text-[11px] text-faint">{MKV_ONLY}</p>
			{/if}
			{@render list(shown.rows)}
		</div>
	{/if}

	{#if file.why.skip}
		<!-- The skip first, or the reasons read as a rewrite that never comes.
		     Labelled with the file's own verdict, since an unsupported container is
		     a skip in the plan. -->
		<p class="mt-3 text-[12px] text-dim">
			<span class="font-medium text-fg">{verdictLabel(file.status)}:</span>
			{file.why.skip}
		</p>
	{/if}
	{#if file.why.failed}
		<!-- Above the changes for the same reason as the skip. -->
		<p class="mt-3 text-[12px] text-dim">
			<span class="font-medium text-danger">Failed:</span>
			{file.why.failed}
		</p>
	{/if}
	{@render account()}
	{#if !changes.length && !file.why.skip && !file.why.failed}
		<p class="mt-3 text-[12px] text-faint">Nothing to change.</p>
	{/if}
</li>

<!-- The file's tracks, one row each. The number is the place the track takes in
     the file the rewrite leaves; a dropped row has none and is struck through, a
     generated one is accented. Title and flags go on a second line under the
     track they belong to, indented by the grid rather than by a guessed width.
     Where tags can be edited a row the file holds is a button, marked by the
     pencil at its end; the editor comes up inline under it. -->
{#snippet list(rows: Listed[])}
	<ul class="flex flex-col gap-1">
		{#each rows as row, at (at)}
			{@const gone = row.state === 'dropped'}
			{@const fresh = row.state === 'added'}
			{@const open = openedFor(row)}
			{@const told = toldFor(row)}
			<li
				class={`font-mono text-[11px] ${gone ? 'text-faint' : fresh ? 'text-accent' : 'text-dim'}`}
			>
				<!-- Padded to a tappable height either way, so the two variants line up:
				     the text alone is a 17px line. -->
				{#if edits(row)}
					<button
						type="button"
						onclick={(event) => editing?.open(file, row, event.currentTarget)}
						aria-expanded={open}
						class={`grid w-full grid-cols-[auto_1fr] gap-x-1.5 rounded px-1 py-1.5 text-left transition-colors hover:bg-raised ${
							open ? 'bg-raised' : ''
						}`}
					>
						{@render cells(row, gone, fresh, true)}
					</button>
				{:else}
					<div
						class="grid grid-cols-[auto_1fr] gap-x-1.5 px-1 py-1.5"
						title={editing && tagged(row) && !editable(file) ? MKV_ONLY : undefined}
					>
						{@render cells(row, gone, fresh, false)}
					</div>
				{/if}
				{#if open}
					{@const track = onDisk(row)}
					<div class="mt-1 font-sans">
						<TrackEditor
							{track}
							twins={matching(siblings, file, track)}
							languages={editing?.languages ?? null}
							many={series}
							ondone={(outcomes) => editing?.done(file, row, outcomes)}
							oncancel={() => editing?.cancel()}
						/>
					</div>
				{:else if told}
					<!-- What the edit came to, where the form was: the row above already
					     reads as the file now does, and this says how many others
					     followed. -->
					<div class="px-1 pb-1 font-sans text-[12px]">
						<p role="status" class={told.problems.length ? 'text-dim' : 'text-ok'}>
							{told.line}
						</p>
						{#each told.problems as problem, at (at)}
							<p class="text-danger">{problem}</p>
						{/each}
					</div>
				{/if}
			</li>
		{/each}
	</ul>
{/snippet}

<!-- One row's cells: its place and kind, what it is, and under that its title and
     flags. `pencil` marks a row that opens. -->
{#snippet cells(row: Listed, gone: boolean, fresh: boolean, pencil: boolean)}
	{@const track = row.track}
	{@const takes = weighs(track)}
	<span class="whitespace-pre text-faint">{place(row)} {KIND_LETTER[track.kind] ?? '·'}</span>
	<span class="flex min-w-0 items-baseline gap-1.5">
		<span class={`min-w-0 truncate ${gone ? 'line-through' : ''} ${fresh ? 'font-semibold' : ''}`}>
			{describe(track)}
		</span>
		{#if fresh}
			<span class="flex-none text-[10px] tracking-wide">NEW</span>
		{/if}
		<span class="ml-auto flex flex-none items-baseline gap-1.5 pl-2 text-faint">
			{#if track.bitrate}
				<span>{rate(track.bitrate)}</span>
			{/if}
			{#if takes}
				<span>{size(takes)}</span>
			{/if}
			{#if pencil}
				<Glyph name="pencil" size={11} />
			{/if}
		</span>
	</span>
	{#if track.title || badges(track).length}
		<span class="col-start-2 flex min-w-0 items-baseline gap-1.5 text-faint">
			<span class="min-w-0 truncate">{track.title}</span>
			{#each badges(track) as flag (flag)}
				<span class="flex-none rounded border border-line px-1 text-[10px]">{flag}</span>
			{/each}
		</span>
	{/if}
{/snippet}

<!-- What a rewrite of this file would do: the changes a line each, then the rules
     behind them. -->
{#snippet account()}
	{#if changes.length}
		<ul class="mt-3 flex flex-col gap-1">
			{#each changes as change, at (at)}
				<li class={`flex gap-1.5 text-[12px] ${change.rides ? 'text-faint' : 'text-dim'}`}>
					<!-- Marks our fonts carry; see Glyph.svelte. Fixed width so every line
					     starts on one column whichever mark it takes. -->
					<span class="w-2 flex-none text-center">{change.mark}</span>
					<span class="min-w-0">{change.text}</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if file.why.rules?.length || file.why.incidental_rules?.length}
		<div class="mt-1.5 flex flex-wrap gap-1.5">
			{#each file.why.rules ?? [] as rule (rule)}
				<span
					class="rounded border border-line bg-accent-soft px-1.5 py-0.5 font-mono text-[10.5px] text-accent"
				>
					{rule}
				</span>
			{/each}
			{#each file.why.incidental_rules ?? [] as rule (rule)}
				<span class="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-faint">
					{rule}
				</span>
			{/each}
		</div>
	{/if}
{/snippet}
