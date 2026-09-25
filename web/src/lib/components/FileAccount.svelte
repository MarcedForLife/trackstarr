<script module lang="ts">
	import type { LibraryFile, Listed } from '$lib/library';
	import type { Outcome, Summary } from '$lib/retag';

	/** Tag editing, run by the title sheet so it can hand focus back. Null
	 * elsewhere, which keeps the rows shut. */
	export type Editing = {
		at: { path: string; stream: number } | null;
		told: ({ path: string; stream: number } & Summary) | null;
		languages: Record<string, string> | null;
		open: (file: LibraryFile, row: Listed, pressed: HTMLElement) => void;
		done: (file: LibraryFile, row: Listed, outcomes: Outcome[]) => void;
		cancel: () => void;
		// The open editor's pencil.
		trigger: HTMLElement | null;
	};
</script>

<script lang="ts">
	import Glyph from './Glyph.svelte';
	import RuleChips from './RuleChips.svelte';
	import TrackEditor from './TrackEditor.svelte';
	import {
		badges,
		bytesFor,
		capitalized,
		carriesLanguage,
		describe,
		DV_REMOVED,
		rate,
		videoDetail
	} from '$lib/format';
	import { CHIP, PLAIN_TONE, listing, size, verdictLabel, type Track } from '$lib/library';
	import { editable, matching } from '$lib/retag';

	// What a rewrite would do to one file: its tracks as it leaves them, why it is
	// skipped or failed, and the rules behind it. The sheet, the queue, a run and
	// the paused list all open on this.

	let {
		file,
		editing = null,
		// The title's other files, for an edit offered across twins.
		siblings = [],
		series = false
	}: {
		file: LibraryFile;
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

	// The one reason a row can carry, said once above the list for a phone and on
	// each row for a pointer.
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

	// The whole plan, not what the chips draw: a rewrite that only drops tracks
	// still changes the file.
	const idle = $derived(!file.why.reasons?.length && !file.why.incidental?.length);
	// Cached plans from before rule pairing still carry their explanations.
	const unpaired = $derived(
		[
			...(file.why.reasons ?? []).map((text) => ({ text, rides: false })),
			...(file.why.incidental ?? []).map((text) => ({ text, rides: true }))
		].filter((line) => !file.why.changes?.some((change) => change.text === line.text))
	);
</script>

{#if shown.rows.length}
	<!-- One list in the order the file ends up in: a rewrite copies far more than
	     it touches, so two columns were the same list twice. Plan or record is
	     the caller's line above, "Pending" against "Modified", so the label is
	     left to a reader who cannot see it. -->
	<div class="mt-3">
		{#if locked}
			<p class="mb-1.5 text-[11px] text-faint">{MKV_ONLY}</p>
		{/if}
		{@render list(shown.rows)}
	</div>
{/if}

{#if file.modified?.dv_removed}
	<p class="mt-3"><span class={`${CHIP} ${PLAIN_TONE}`}>{DV_REMOVED}</span></p>
{/if}

{#each file.why.notes ?? [] as note (note)}
	<p class="mt-3 text-[12px] text-dim">{note}</p>
{/each}

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

<!-- A chip opens on the lines its own rule ordered. -->
{#if file.why.rules?.length || file.why.incidental_rules?.length}
	<RuleChips
		rules={file.why.rules}
		incidental={file.why.incidental_rules}
		changes={file.why.changes}
	/>
{/if}
{#if unpaired.length}
	<ul class="mt-3 list-disc space-y-1 pl-4 text-[12px] text-dim">
		{#each unpaired as line, at (at)}
			<li class={line.rides ? 'text-faint' : undefined}>
				{capitalized(line.text)}{line.rides ? ' (with rewrite)' : ''}
			</li>
		{/each}
	</ul>
{/if}
<!-- Any other file's status already says whether it changes. -->
{#if idle && file.status === 'unchecked' && !file.why.skip && !file.why.failed}
	<p class="mt-3 text-[12px] text-faint">
		No plan yet. This file will be checked before processing.
	</p>
{/if}

<!-- The number is the place the track takes in the file the rewrite leaves; a
     dropped row has none and is struck through, a generated one is accented.
     The row is not the tap target, only the pencil at its end: the row is where
     a tap dismissing something else lands. -->
{#snippet list(rows: Listed[])}
	<ul class="flex flex-col gap-1" aria-label={shown.label}>
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
				<div
					class={`grid grid-cols-[auto_1fr] gap-x-1.5 rounded px-1 py-1.5 ${open ? 'bg-raised' : ''}`}
					title={editing && tagged(row) && !editable(file) ? MKV_ONLY : undefined}
				>
					{@render cells(row, gone, fresh, edits(row))}
				</div>
				{#if open}
					{@const track = onDisk(row)}
					{@const done = editing?.done}
					<!-- Only while this row is open, since a press on another pencil opens
					     that row before this editor sees it as outside. -->
					<TrackEditor
						{track}
						trigger={editing?.trigger ?? null}
						twins={matching(siblings, file, track)}
						languages={editing?.languages ?? null}
						many={series}
						ondone={(outcomes) => done?.(file, row, outcomes)}
						oncancel={() => openedFor(row) && editing?.cancel()}
					/>
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

<!-- Title and flags go on a second line, indented by the grid rather than by a
     guessed width. `pencil` gives the row its editor button. -->
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
				<!-- Pulled into the row's padding, so a thumb-sized target does not
				     space the list out. -->
				<button
					type="button"
					onclick={(event) => editing?.open(file, row, event.currentTarget)}
					aria-expanded={openedFor(row)}
					aria-haspopup="dialog"
					aria-label={`Edit tags on ${describe(track)}`}
					class="-my-1.5 -mr-1 inline-flex h-8 w-8 items-center justify-center self-center rounded-full transition-colors hover:bg-raised"
				>
					<Glyph name="pencil" size={11} />
				</button>
			{/if}
		</span>
	</span>
	{#if videoDetail(track)}
		<span class="col-start-2 text-faint">{videoDetail(track)}</span>
	{/if}
	{#if track.title || badges(track).length}
		<span class="col-start-2 flex min-w-0 items-baseline gap-1.5 text-faint">
			<span class="min-w-0 truncate">{track.title}</span>
			{#each badges(track) as flag (flag)}
				<span class="flex-none rounded border border-line px-1 text-[10px]">{flag}</span>
			{/each}
		</span>
	{/if}
{/snippet}
