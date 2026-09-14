<script lang="ts">
	import FileTitle from './FileTitle.svelte';
	let titleDetails: FileTitle;
	import { onDestroy } from 'svelte';
	import { resolve } from '$app/paths';
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import PausedFile from '$lib/components/PausedFile.svelte';
	import QueueDialog from '$lib/components/QueueDialog.svelte';
	import { queueAction } from '$lib/queue';
	import RunFile from '$lib/components/RunFile.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import SweepButtons from '$lib/components/SweepButtons.svelte';
	import type { Landed, Snapshot } from '$lib/activity.svelte';
	import { refusalText } from '$lib/api';
	import { button, danger, primary, rowButton } from '$lib/controls';
	import { soon, stamp } from '$lib/format';
	import { resume as resumeItem, place, type Pause } from '$lib/pauses';
	import type { RunMode } from '$lib/library';
	import { popover } from '$lib/popover.svelte';
	import {
		fileRows,
		source,
		progressed,
		remaining,
		pause,
		resume,
		skipFile,
		startSweep,
		stopEverything,
		type Activity,
		type Run
	} from '$lib/runs';
	import { told } from '$lib/stream';

	// Current work and controls. Completed work belongs in Activity.
	let {
		snapshot,
		admin = false,
		onmoved,
		onpressed
	}: {
		/** What the service is doing, read for the page rather than here. */
		snapshot: Snapshot;
		admin?: boolean;
		/** A run moved on or ended. `ended` means the history is a line behind and
		 * worth a look now. */
		onmoved: (ended: boolean) => void;
		/** A button here was pressed, which the history records. */
		onpressed: () => void;
	} = $props();

	const activity = $derived(snapshot.current);
	// Kept apart from the snapshot's own `offline`: a refusal is a sentence to
	// read, a lost connection is a condition the next look clears. In one string
	// the poll wiped the refusal.
	let refusal = $state('');
	let busy = $state('');

	// Stop all asks before it goes, in a panel hung off the button as the sweep's
	// pair does. Left-hung, since the button sits at the left of its row.
	const confirm = popover({ edge: 'left' });
	let stopButton = $state<HTMLButtonElement>();
	let stopPanel = $state<HTMLDivElement>();

	const holding = $derived(activity.paused);
	// Encoding this second, which is the only way to tell a rewrite from a probe.
	const rewriting = $derived(activity.rewrites > 0);

	const files = $derived(
		activity.runs.flatMap((run) =>
			fileRows(run).map((row) => ({ run, row, key: `${run.id}:${row.path}` }))
		)
	);
	const activeFiles = $derived(files.filter((file) => file.row.live));
	const legacyQueue = $derived(
		files
			.filter(
				(file) => (file.row.waiting && !file.row.skipped) || file.row.live?.stage === 'waiting'
			)
			.sort((left, right) => Number(!!right.row.live) - Number(!!left.row.live))
	);
	const queuedFiles = $derived<typeof files>(
		activity.queue_preview
			? activity.queue_preview.flatMap((item) => {
					const run = activity.runs.find((run) => run.id === item.run);
					return run
						? [
								{
									run,
									row: {
										path: item.path,
										seconds: 0,
										waiting: item,
										live: null,
										verdict: '',
										detail: '',
										skipped: false
									},
									key: `${item.run}:${item.path}`
								}
							]
						: [];
				})
			: legacyQueue
	);

	const shownQueue = $derived(queuedFiles.slice(0, 3));
	// Over the rows on screen, not the runs going. A sweep still walking has
	// nothing drawn to tell apart from the delivery beside it.
	const mixed = $derived(
		new Set([...activeFiles, ...shownQueue].map((file) => file.run.id)).size > 1
	);

	// The run a row belongs to, said only where the rows differ on it. A dry run
	// says so either way, since nothing it plans will be written.
	function originOf(run: Run): string {
		return [mixed ? source(run) : '', run.dry_run && run.kind !== 'import' ? 'Plan only' : '']
			.filter(Boolean)
			.join(' · ');
	}

	const failed = $derived(
		activity.runs.reduce((count, run) => count + (run.counts.failed ?? 0), 0)
	);
	let lastUpdated = $state(new Date().toISOString());

	// Progress and failures cover current work.
	const completedCount = $derived(activity.runs.reduce((count, run) => count + run.done, 0));
	const totalCount = $derived(activity.runs.reduce((count, run) => count + run.total, 0));
	const discovering = $derived(activity.runs.some((run) => run.walking || !run.total));
	// Counts the rewrites under way, so the bar glides with the files below it
	// rather than stepping once each lands.
	const processed = $derived(
		activity.runs.reduce(
			(count, run) => count + progressed(run, snapshot.offline ? 0 : since(run.seen)),
			0
		)
	);
	const completion = $derived(totalCount ? Math.min(1, processed / totalCount) : 0);

	const waiting = $derived(Math.max(activity.queue, queuedFiles.length));
	const processingState = $derived(
		snapshot.offline
			? 'Connection lost'
			: holding
				? activity.runs.length
					? 'Paused'
					: 'Suspended'
				: !activity.runs.length
					? 'Idle'
					: activity.runs.every((run) => run.stopping)
						? 'Stopping'
						: 'Working'
	);
	let queueOpen = $state(false);
	let queueNotice = $state('');
	let queueUndo = $state<string | null>(null);
	let opened = $state<Record<string, boolean>>({});
	$effect(() => (activity.runs.length && !snapshot.offline ? ticking() : undefined));

	// Show a single useful estimate without combining unrelated run estimates.
	const estimates = $derived(
		snapshot.offline
			? []
			: activity.runs.map((run) => remaining(run, holding, since(run.seen), true)).filter(Boolean)
	);

	// A walk holding the sweep cache; the service refuses a second. A re-check
	// counts.
	const sweeping = $derived(
		activity.runs.some((run) => run.kind === 'sweep' || run.kind === 'recheck')
	);

	// Paused workers finishing, or the next scheduled check while idle.
	const sub = $derived.by(() => {
		if (snapshot.offline)
			return `Showing the last update from ${stamp(lastUpdated)}. Reconnecting…`;
		if (holding)
			return activeFiles.length
				? 'Files already started will finish. Nothing new starts until you resume.'
				: 'Nothing new starts until you resume.';
		return !activity.runs.length && activity.next_sweep
			? `Next scheduled check ${soon(activity.next_sweep)}.`
			: '';
	});

	// The fallback poll rate this panel asks the snapshot for. A run is a readout
	// being watched; an idle service is a page left on a desk.
	const BUSY_MS = 2000;
	const IDLE_MS = 15000;

	// How long a run this page started counts as running before it appears in a
	// snapshot: a start answers before the sweep has listed the *arrs.
	// Self-clearing, so a failed start fast-polls for one window.
	const SETTLE_MS = 30000;
	let expecting = 0;

	// Read as the chain re-arms, not watched: a reactive read would rebuild the
	// timer on every answer.
	function pace(): number {
		return told(snapshot.current.runs.length || Date.now() < expecting ? BUSY_MS : IDLE_MS);
	}

	// News about a run, not a tick of one: the rows draw clocks and bars from the
	// last snapshot and its age.
	function saw({ now: fresh, before }: Landed) {
		lastUpdated = new Date().toISOString();
		const alive = new Set(fresh.runs.map((run) => run.id));
		// Refresh Activity promptly when a run ends, including one replaced by
		// another between snapshots. Short deliveries are caught by normal polls.
		onmoved(before.runs.some((run) => !alive.has(run.id)));
	}

	// Read once: the page hands in the same snapshot for the life of the panel.
	// svelte-ignore state_referenced_locally
	onDestroy(snapshot.watch({ pace, saw }));

	// Every button goes through here: names what is under way, keeps a refusal's
	// words, and refreshes rather than guessing.
	async function act(name: string, action: () => Promise<unknown>) {
		busy = name;
		refusal = '';
		confirm.lower();
		try {
			const answer = await action();
			const told = answer && typeof answer === 'object' ? (answer as Record<string, unknown>) : {};
			// An answer with a run id has started one before any snapshot shows it.
			if ('run' in told) expecting = Date.now() + SETTLE_MS;
			// Pause and resume answer with the snapshot; the rest need a fresh one.
			if ('runs' in told) snapshot.take(answer as Activity);
			else await snapshot.look();
		} catch (error) {
			refusal = refusalText(error);
		} finally {
			busy = '';
			// The snapshot is now this moment's, and a just-started sweep polls fast
			// from here.
			snapshot.mark();
			// Pausing and resuming are history events.
			onpressed();
		}
	}

	async function top(run: Run, path: string) {
		await act('queue-top', async () => {
			const answer = await queueAction('top', [{ run: run.id, path }]);
			queueNotice = answer.moved ? 'File moved to top.' : 'That file is no longer waiting.';
			queueUndo = answer.undo ?? null;
		});
	}
	async function undoTop() {
		await act('queue-undo', async () => {
			await queueAction('undo', [], { token: queueUndo! });
			queueNotice = 'Queue order restored for files still waiting.';
			queueUndo = null;
		});
	}

	// One file off one run. No confirm: nothing is lost but the encode, and the
	// next sweep still reaches the file.
	function skip(entry: Run, path: string) {
		act(`skip-${path}`, () => skipFile(entry.id, path));
	}

	async function pauseFile(entry: Run, path: string, seconds: number) {
		busy = `pause-${path}`;
		try {
			await place({ paths: [path] }, seconds);
			await skipFile(entry.id, path);
		} finally {
			busy = '';
			await snapshot.look();
			snapshot.mark();
			onpressed();
		}
	}

	// What is being left alone for now, and how long is left of each.
	const pausedItems = $derived(activity.pauses ?? []);

	function release(pause: Pause) {
		act(`resume-${pause.path}`, () => resumeItem({ paths: [pause.path] }));
	}

	// Both are asked for in the pair's own popover before they reach here.
	const run = (mode: RunMode) => act(`start-${mode}`, () => startSweep(mode));
	const abortNow = () => act('abort', stopEverything);

	const starting = $derived<'' | RunMode>(
		busy === 'start-report' ? 'report' : busy === 'start-apply' ? 'apply' : ''
	);

	// The question leaves with its reason, since the last run ending takes the
	// button it hangs from.
	$effect(() => {
		if (confirm.open && !activity.runs.length) confirm.lower();
	});
</script>

<svelte:window
	onpointerdown={(event) =>
		confirm.open && confirm.outside(event.target as Node) && confirm.lower()}
	onresize={() => confirm.open && confirm.lower()}
/>

{#snippet fileList(items: typeof files, ordered: boolean)}
	<ul class="space-y-2">
		{#each items as file, index (file.key)}
			<RunFile
				run={file.run.id}
				row={file.row}
				cover={activity.covers?.[file.row.path]}
				plan={activity.plans?.[file.row.path]}
				current={activity.plans_current ?? true}
				onopen={() => titleDetails.show(file.row.path, activity.covers?.[file.row.path])}
				origin={originOf(file.run)}
				place={ordered ? index + 1 : 0}
				age={snapshot.offline ? 0 : since(file.run.seen)}
				id={`file-${file.run.id}-${file.row.path}`}
				open={!!opened[file.key]}
				skippable={admin &&
					!file.run.stopping &&
					(!!file.row.live || !!file.row.waiting) &&
					!file.row.skipped}
				busy={!!busy}
				ontoggle={() => (opened = { ...opened, [file.key]: !opened[file.key] })}
				ontop={file.row.waiting ? () => top(file.run, file.row.path) : undefined}
				onskip={() => skip(file.run, file.row.path)}
				onpause={(seconds) => pauseFile(file.run, file.row.path, seconds)}
			/>
		{/each}
	</ul>
{/snippet}

<section aria-labelledby="now" class="mt-6 rounded-xl border border-line bg-raised">
	<div class="px-4 py-5 sm:px-5">
		<div class="flex items-center justify-between gap-3">
			<h2
				id="now"
				class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1 text-[23px] leading-tight font-semibold tracking-tight"
			>
				<span
					aria-hidden="true"
					class={`h-2 w-2 flex-none rounded-full ${snapshot.offline ? 'bg-danger' : holding || activity.runs.length ? 'bg-accent-fill' : 'bg-line-strong'}`}
				></span>
				{processingState}
				{#if failed}<a
						href={resolve('/events?filter=issues')}
						title="View issues in activity"
						class="inline-flex min-h-11 items-center rounded-lg px-2 text-[12px] font-medium tracking-normal text-danger underline underline-offset-2 hover:bg-danger/10"
						>{failed} failed</a
					>{/if}
			</h2>
			{#if admin && !sweeping && !holding}
				<SweepButtons
					mayRewrite={activity.may_rewrite}
					disabled={!!busy}
					busy={starting}
					onrun={run}
				/>
			{/if}
		</div>
		{#if sub}
			<p role="status" class="mt-2 text-[12.5px] text-dim">{sub}</p>
		{:else if !activity.runs.length && !holding}
			<p class="mt-2 text-[12.5px] text-dim">
				No sweep is scheduled.
				{#if admin}
					<a
						href={resolve('/settings/sweep')}
						class="text-accent underline underline-offset-2 hover:text-fg"
					>
						Schedule one
					</a>
				{/if}
			</p>
		{/if}
	</div>

	{#if activity.runs.length}
		<div class="px-4 pb-4 sm:px-5" aria-label="Overall processing progress">
			{#if totalCount}
				<div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
					<span class="text-[28px] leading-none font-semibold tracking-tight tabular-nums"
						>{completedCount.toLocaleString()}</span
					>
					<span class="text-[12px] text-dim"
						>of {totalCount.toLocaleString()} {totalCount === 1 ? 'file' : 'files'} processed</span
					>
					<span
						class="ml-auto inline-flex items-baseline gap-2 text-[12px] whitespace-nowrap tabular-nums"
					>
						{#if estimates.length === 1}<span aria-label="Estimated time remaining" class="text-dim"
								>{estimates[0]}</span
							>{/if}
						{#if !discovering}
							{#if estimates.length === 1}<span aria-hidden="true" class="text-faint">·</span>{/if}
							<span class="font-medium text-accent">{Math.floor(completion * 100)}%</span>
						{/if}
					</span>
				</div>
				{#if !discovering}<Bar
						class="mt-3 !h-1.5"
						fill={completion}
						glide={rewriting}
						now={completedCount}
						max={totalCount}
						text={`${completedCount} of ${totalCount} files processed across active runs`}
					/>{/if}
			{/if}
			{#if discovering}<p class="mt-2 text-[12px] text-dim">
					Discovering files{totalCount ? ' · totals may grow' : ''}…
					{#if !totalCount && estimates.length === 1}<span
							aria-label="Estimated time remaining"
							class="ml-2 whitespace-nowrap tabular-nums">{estimates[0]}</span
						>{/if}
				</p>{/if}
		</div>
	{/if}

	<div
		class="flex flex-wrap items-center gap-2 px-4 pb-1.5 sm:px-5 sm:pb-3"
		aria-label="Processing controls"
	>
		{#if admin}
			{#if holding}
				<button
					onclick={() => act('resume', resume)}
					disabled={!!busy}
					class={`min-w-0 !text-[12px] ${primary}`}
				>
					{#if busy !== 'resume'}<Glyph name="play" />{/if}
					{busy === 'resume' ? 'Resuming…' : 'Resume'}
				</button>
			{:else}
				<button
					onclick={() => act('pause', pause)}
					disabled={!!busy}
					aria-label={activity.runs.length ? 'Pause' : 'Suspend'}
					title={activity.runs.length
						? 'Pause. Files already started finish and nothing new starts.'
						: 'Prevent new processing from starting until you resume.'}
					class={`${rowButton} gap-2 px-2 text-dim hover:bg-sunken sm:px-3`}
				>
					<Glyph name="pause" />
					<span>
						{#if activity.runs.length}
							{busy === 'pause' ? 'Pausing…' : 'Pause'}
						{:else if busy === 'pause'}
							Suspending…
						{:else}
							Suspend
						{/if}
					</span>
				</button>
			{/if}
			{#if activity.runs.length}
				<button
					bind:this={stopButton}
					onclick={() => stopButton && stopPanel && confirm.toggle(stopButton, stopPanel)}
					disabled={!!busy || (activity.runs.every((run) => run.stopping) && !rewriting)}
					aria-expanded={confirm.open}
					aria-controls="stop-confirmation"
					class={`${rowButton} gap-2 px-3 text-danger hover:bg-danger/10`}
				>
					<Glyph name="stop" size={9} />
					{busy === 'abort' || (activity.runs.every((run) => run.stopping) && !rewriting)
						? 'Stopping…'
						: 'Stop all'}
				</button>
			{/if}
		{/if}
	</div>
	<!-- Always drawn, since a popover is hidden until shown and one that mounts on
	     the press has nothing to hang from. -->
	<div
		bind:this={stopPanel}
		id="stop-confirmation"
		popover="manual"
		role="alertdialog"
		aria-labelledby="stop-title"
		aria-describedby="stop-description"
		style:left={`${confirm.left}px`}
		style:top={`${confirm.top}px`}
		class="fixed m-0 w-72 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong bg-raised p-3.5 text-fg shadow-lg"
	>
		<h3 id="stop-title" class="text-[13px] font-semibold">Stop all processing?</h3>
		<p id="stop-description" class="mt-1.5 text-[12.5px] leading-relaxed text-dim">
			Queued files won’t start. {rewriting
				? 'Current rewrites will be interrupted and their progress will be lost.'
				: 'Files already being checked will finish.'}
		</p>
		<dl class="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-[12px]">
			<div class="flex items-baseline gap-2">
				<dt class="text-dim">Queued</dt>
				<dd class="font-semibold tabular-nums">{waiting.toLocaleString()}</dd>
			</div>
			{#if rewriting}<div class="flex items-baseline gap-2">
					<dt class="text-dim">Rewriting</dt>
					<dd class="font-semibold tabular-nums">{activity.rewrites.toLocaleString()}</dd>
				</div>{/if}
		</dl>
		<p class="mt-2.5 text-[12px] text-faint">Original files stay untouched.</p>
		<div class="mt-3 flex gap-2">
			<button onclick={() => confirm.lower()} disabled={!!busy} class={`flex-1 ${button}`}
				>Cancel</button
			>
			<button onclick={abortNow} disabled={!!busy} class={`flex-1 ${danger}`}
				><Glyph name="stop" size={9} /> Stop all</button
			>
		</div>
	</div>

	<!-- The service's own words, kept until the next button is pressed. -->
	{#if refusal}
		<p role="alert" class="border-t border-line px-4 py-2.5 text-[12.5px] text-danger">
			{refusal}
		</p>
	{:else if snapshot.offline}
		<p role="status" class="border-t border-line px-4 py-2.5 text-[12.5px] text-danger">
			{snapshot.offline}
		</p>
	{/if}

	{#if activeFiles.length || waiting}
		<!-- The rows sit in a well inset from the panel, so the raised ground shows
		     round it: the sunken step alone was too small a difference to read. The
		     frame is 6px on a phone, where the width it costs the rows is worth more
		     than the separation it buys. -->
		<div class="px-1.5 pb-1.5 sm:px-5 sm:pb-4">
			<div
				class={`rounded-xl border border-line bg-sunken p-3 sm:p-4 ${waiting && activeFiles.length ? 'grid gap-6 lg:grid-cols-2 lg:gap-8' : ''}`}
			>
				{#if activeFiles.length}
					<!-- A grid cell holds its content's width by default, and a row's
					     name no longer wraps. -->
					<div class="min-w-0">
						<div class="flex flex-wrap items-baseline justify-between gap-2">
							<h3 class="text-[13px] font-semibold">
								Processing <span class="ml-1 text-faint tabular-nums">{activeFiles.length}</span>
							</h3>
						</div>
						<div class="mt-3">
							{@render fileList(activeFiles, false)}
						</div>
					</div>
				{/if}
				{#if waiting}
					<div class="min-w-0">
						<h3 class="text-[13px] font-semibold">
							Waiting <span class="ml-1 text-faint tabular-nums">{waiting.toLocaleString()}</span>
						</h3>
						<div class="mt-3">
							{@render fileList(shownQueue, true)}
							<button
								class="mt-1 min-h-11 text-[12px] font-medium text-accent hover:underline"
								onclick={() => (queueOpen = true)}>View queue ({waiting})</button
							>
							{#if queueNotice}<p role="status" class="pb-2 text-[12px] text-dim">
									{queueNotice}
									{#if queueUndo}<button
											class="min-h-8 text-accent underline"
											onclick={undoTop}
											disabled={!!busy}>Undo</button
										>{/if}
								</p>{/if}
						</div>
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Explicit pauses apply to titles and future work. -->
	{#if pausedItems.length}
		<details
			class="group/service mx-1.5 mb-1.5 rounded-xl border border-line bg-sunken px-3 text-[12.5px] sm:mx-5 sm:mb-4 sm:px-4"
		>
			<summary
				class="flex min-h-12 cursor-pointer list-none items-center gap-2 text-dim hover:text-fg [&::-webkit-details-marker]:hidden"
			>
				<span class="transition-transform group-open/service:rotate-90"
					><Glyph name="chevron" size={10} /></span
				>
				Paused <span class="text-faint tabular-nums">{pausedItems.length}</span>
			</summary>
			<ul class="space-y-2 pb-3">
				{#each pausedItems as pause (pause.path)}
					<PausedFile
						{pause}
						cover={activity.covers?.[pause.path]}
						onopen={() => titleDetails.show(pause.path, activity.covers?.[pause.path])}
						{admin}
						disabled={!!busy}
						resuming={busy === `resume-${pause.path}`}
						onresume={() => release(pause)}
					/>
				{/each}
			</ul>
		</details>
	{/if}
	<FileTitle bind:this={titleDetails} />
</section>

{#if queueOpen}<QueueDialog
		{admin}
		onclose={() => (queueOpen = false)}
		onchanged={() => {
			void snapshot.look();
			onpressed();
		}}
	/>{/if}
