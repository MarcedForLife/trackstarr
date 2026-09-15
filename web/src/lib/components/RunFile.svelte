<script lang="ts">
	import FilePlan from './FilePlan.svelte';
	import FilePoster from './FilePoster.svelte';
	import Glyph from './Glyph.svelte';
	import PlanLine from './PlanLine.svelte';
	import RunLog from './RunLog.svelte';
	import type { FileChanges, FileCover } from '$lib/queue';
	import Bar from '$lib/components/Bar.svelte';
	import FileActions from '$lib/components/FileActions.svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import { fileRow, rowMenu } from '$lib/controls';
	import { verdictHint, verdictLabel } from '$lib/library';
	import { reduced } from '$lib/motion.svelte';
	import { warm } from '$lib/plans.svelte';
	import {
		duration,
		fileBar,
		fileFraction,
		fileReadout,
		fileStatus,
		fileWorking,
		getRunLog,
		named,
		queuePlace,
		titled,
		type FileRow
	} from '$lib/runs';

	// One file under a run: what it is, how long, and what it came to, opening
	// to the full path and its worker's log. The same row live or released.
	let {
		run,
		row,
		cover,
		plan,
		current = true,
		onopen,
		origin = '',
		place = 0,
		age = 0,
		ended = false,
		open = false,
		id,
		skippable = false,
		busy = false,
		ontoggle,
		onskip,
		onpause,
		ontop
	}: {
		// The run id, which is half of what names a file's log.
		run: string;
		row: FileRow;
		cover?: FileCover;
		// What a rewrite would do, drawn from the queue until the file is released.
		plan?: FileChanges;
		current?: boolean;
		onopen?: () => void;
		origin?: string;
		// Where a queued row sits in reach order. Zero for a list not in that order.
		place?: number;
		// Seconds since the snapshot arrived, to carry a live row's readouts
		// forward. A finished row ignores it.
		age?: number;
		ended?: boolean;
		open?: boolean;
		// Ties the button to the panel it opens; unique within the page.
		id: string;
		// An admin's, on a file this run has still to finish with.
		skippable?: boolean;
		busy?: boolean;
		ontoggle: () => void;
		onskip?: () => void;
		onpause?: (seconds: number) => Promise<void>;
		ontop?: () => void | Promise<void>;
	} = $props();

	// So the panel opens at its full height rather than growing into one. On the
	// path rather than the row, which is a new object every snapshot.
	const warming = $derived(row.path);
	$effect(() => warm(warming));

	const live = $derived(!ended && !!row.live);
	const waiting = $derived(!ended && !!row.waiting);
	const active = $derived(live && row.live?.stage !== 'waiting');
	const shown = $derived(named(row.path));
	const status = $derived(live && row.live ? fileStatus(row.live, age) : '');
	const bar = $derived(live && !!row.live && fileBar(row.live));
	const readout = $derived(bar && row.live ? fileReadout(row.live, age) : null);
	// The bar's place, saying the work is under way. Not on a skipped row, where
	// movement would read as progress.
	const crossing = $derived(
		live && !row.skipped && !!row.live && fileWorking(row.live) && !reduced()
	);

	// A live row's clock runs; a finished one's is how long the worker had it. A
	// waiting row has none, and shows what its rewrite is expected to take.
	const held = $derived(live ? row.seconds + age : row.seconds);
	const expected = $derived(row.waiting?.expected ?? 0);

	// The word on the right, in the library's words where there is a verdict.
	// Nothing where the list's heading and the bar already say it.
	const standing = $derived.by(() => {
		if (row.skipped) return live ? 'Stopping…' : 'Skipped';
		if (row.verdict) return verdictLabel(row.verdict);
		if (ended) return row.waiting ? 'Not processed' : 'Ended';
		if (waiting) return queuePlace(place);
		if (!live) return '';
		if (row.live?.stage === 'waiting') return 'Waiting for slot';
		return bar ? '' : 'Working';
	});
	// A queue number is set as the queue screen sets it, so the same file reads
	// the same in both places.
	const standingClass = $derived(
		row.verdict === 'failed'
			? 'font-medium text-danger'
			: waiting
				? 'font-mono text-[11px] text-faint tabular-nums'
				: ''
	);
	// What a queued rewrite is expected to take, or how long the file has been
	// held. Nothing under a bar, which says where the encode is in better numbers.
	const clock = $derived.by(() => {
		if (waiting) return expected ? `~${duration(expected)}` : '';
		if (ended && row.waiting) return '';
		return bar ? '' : duration(held);
	});

	// The line under the name: how far through, or the detail.
	const said = $derived(live ? status : ended && row.live ? '' : row.detail);

	let lines = $state<string[] | null>(null);
	let failure = $state('');

	// The worker's own output, which is for working out why rather than what, so
	// it is asked for rather than shown. It opens in a sheet, where there is room
	// to read it.
	let logging = $state(false);

	// A row closing under an open log takes the sheet with it.
	$effect(() => {
		if (!open) logging = false;
	});

	// Plain, not $state: the effect below reads and writes them, and a reactive
	// read would re-run it per fetch.
	let read = false;
	let readWhileWorking = false;

	// Runs on every poll while the log is up. A live file is re-read each time; a
	// released one once more, for the last lines.
	$effect(() => {
		if (!open || !logging) {
			read = readWhileWorking = false;
			return;
		}
		const working = live;
		if (!working && read && !readWhileWorking) return;
		read = true;
		readWhileWorking = working;
		let stopped = false;
		getRunLog(run, row.path)
			.then((found) => {
				if (stopped) return;
				lines = found;
				failure = '';
			})
			.catch(() => {
				if (!stopped) failure = 'Could not read this file’s log.';
			});
		return () => {
			stopped = true;
		};
	});
</script>

{#snippet poster()}<FilePoster {cover} name={shown.name} prominent={active} {onopen} />{/snippet}

<!-- The `<li>` is the caller's, which is where the list animates it from. -->
<div class={`${fileRow()} py-3 text-[12px]`}>
	<Disclosure
		{id}
		{open}
		{ontoggle}
		mark={12}
		turn="half"
		class="group block min-h-11 w-full text-left"
		panelClass="mt-2"
		align="start"
		beside={poster}
	>
		{#snippet summary(chevron)}
			<span class="flex items-start gap-3">
				<span class="min-w-0 flex-1">
					<!-- The title alone, on one line, so a list stands to one height.
					     Its year and release words go on the line under it. -->
					<span
						class={`flex items-baseline gap-1.5 leading-snug font-medium text-fg ${active ? 'text-[15px]' : 'text-[13px]'}`}
					>
						<span class="truncate" title={shown.name}>{shown.name}</span>
						{#if shown.episode}<span class="flex-none text-dim">{shown.episode}</span>{/if}
					</span>
					<!-- One line: the release words truncate rather than wrap. -->
					<span class="mt-1 flex items-center gap-x-2 overflow-hidden text-[11.5px] text-dim">
						<!-- Left out rather than left empty: an empty flex item still
						     indents the line by a gap. -->
						{#if standing}<span
								title={row.verdict ? verdictHint(row.verdict) : undefined}
								class={`flex-none ${standingClass}`}>{standing}</span
							>{/if}
						{#if origin}<span class="flex-none text-faint">{origin}</span>{/if}
						{#if clock}<span class="flex-none text-faint tabular-nums">{clock}</span>{/if}
						<!-- Last, so it is the part that gives way. -->
						{#if shown.detail}<span class="min-w-0 truncate text-faint">{shown.detail}</span>{/if}
					</span>
					<!-- The plan keeps its line once a worker picks the file up. The
					     cover runs the height of whatever that leaves. -->
					{#if waiting || live}<PlanLine {plan} {current} chipsOnly={live} />{/if}
				</span>
				{@render chevron()}
			</span>
		{/snippet}

		{#snippet after()}
			{#if skippable && onskip && onpause}
				<FileActions
					label={titled(row.path)}
					{active}
					disabled={busy}
					{onskip}
					{ontop}
					onchoose={onpause}
					hint={active
						? 'Stops this attempt. A later sweep can restart it after the pause ends.'
						: 'A later sweep can process this file after the pause ends.'}
					class={rowMenu}
				/>
			{/if}
		{/snippet}

		<!-- The bar for a rewrite, or the segment for work with nothing to measure. -->
		{#snippet aside()}
			{#if bar || crossing || said}
				<div
					class={`flex flex-wrap items-center gap-x-2.5 gap-y-1.5 ${active ? 'mt-3' : 'mt-1.5'}`}
				>
					{#if bar && row.live}
						<Bar
							file
							class={active ? '!h-1 basis-full' : ''}
							fill={fileFraction(row.live, age)}
							now={Math.floor(fileFraction(row.live, age) * 100)}
							max={100}
							text={`${titled(row.path)}: ${status}`}
						/>
					{:else if crossing}
						<Bar
							file
							indeterminate
							class={active ? '!h-1 basis-full' : ''}
							text={`${titled(row.path)}: working`}
						/>
					{/if}
					{#if readout}
						<!-- How fast under the bar's start, when it ends and how far under
						     its end, in the overall bar's order so the steady number holds
						     the same edge on both. -->
						{#if readout.speed}<span class="text-[11px] text-faint tabular-nums"
								>{readout.speed}</span
							>{/if}
						<span class="ml-auto text-[11px] text-faint tabular-nums"
							>{[readout.left, readout.far].filter(Boolean).join(' · ')}</span
						>
					{:else if said}
						<span
							class="line-clamp-2 min-w-0 flex-1 text-[11px] wrap-anywhere text-faint"
							title={said}
						>
							{said}
						</span>
					{/if}
				</div>
			{/if}
		{/snippet}

		{#snippet panel()}
			<div class="rounded-lg border border-line bg-sunken px-2.5 py-2">
				<div class="flex items-start gap-2">
					<p class="min-w-0 flex-1 font-mono text-[11px] wrap-anywhere text-faint">{row.path}</p>
					<!-- Nothing has touched a file still waiting, so it has no log to ask
					     for. -->
					{#if !waiting}
						<button
							type="button"
							onclick={() => (logging = true)}
							aria-haspopup="dialog"
							aria-label="Show worker log"
							class="relative -my-1.5 -mr-1 inline-flex h-7 flex-none items-center gap-1 rounded-md px-1.5 text-[11px] font-medium text-dim transition-colors after:absolute after:-inset-2 after:content-[''] hover:bg-accent-soft hover:text-fg"
						>
							<!-- The overhang is the target, so the path keeps its line height. -->
							<Glyph name="log" />
							Log
						</button>
					{/if}
				</div>
				<div class="mt-2 border-t border-line pt-2"><FilePlan path={row.path} /></div>
			</div>
		{/snippet}
	</Disclosure>
	{#if logging}
		<RunLog path={row.path} {lines} {failure} onclose={() => (logging = false)} />
	{/if}
</div>
