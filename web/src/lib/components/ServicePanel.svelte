<script lang="ts">
	import { onDestroy } from 'svelte';
	import { resolve } from '$app/paths';
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import RunFile from '$lib/components/RunFile.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import type { Landed, Snapshot } from '$lib/activity.svelte';
	import { refusalText } from '$lib/api';
	import { button, danger, primary } from '$lib/controls';
	import { ago, headline, type Event } from '$lib/events';
	import { size, soon, stamp, titled } from '$lib/format';
	import { lift, type Hold } from '$lib/holds';
	import type { RunMode } from '$lib/library';
	import { keyboard } from '$lib/modal';
	import {
		fileRows,
		source,
		duration,
		pause,
		resume,
		skipFile,
		startSweep,
		stopEverything,
		tally,
		type Activity,
		type Run
	} from '$lib/runs';
	import { told } from '$lib/stream';

	// Owns processing controls and recent runs; files are presented by state.
	let {
		snapshot,
		admin = false,
		recent,
		swept,
		onmoved,
		onpressed
	}: {
		/** What the service is doing, read for the page rather than here. */
		snapshot: Snapshot;
		admin?: boolean;
		/** The last few history lines, for the numbers an ended run left. */
		recent: Event[];
		/** The last sweep to finish. */
		swept?: Event;
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
	let detailsOpen = $state(false);
	// Which irreversible press is waiting for its second one.
	let armed = $state<'' | 'rewrite' | 'abort'>('');

	// Whether that question is still worth answering, derived so it leaves in the
	// same repaint as its reason.
	const arming = $derived(
		(armed === 'rewrite' && !activity.may_rewrite) || (armed === 'abort' && !activity.runs.length)
			? ''
			: armed
	);

	// A run that left the snapshot, held long enough to read. `cut` is a run
	// somebody stopped, whose counts are part of the job.
	type Ended = { run: Run; at: number; cut: boolean };
	let finished = $state<Ended[]>([]);

	// How long an ended run stays up. Ten seconds was too short to open a file's
	// log under it; keep recent outcomes available for five minutes.
	const LINGER_MS = 5 * 60 * 1000;

	const holding = $derived(activity.paused);
	// Encoding this second, which is the only way to tell a rewrite from a probe.
	const rewriting = $derived(activity.rewrites > 0);

	/**
	 * The real numbers onto an ended run's row. The last snapshot is up to a poll
	 * short, so the summary the run wrote on the way out is the count. Only
	 * sweeps and re-checks write one. Read off the history each draw, since the
	 * summary may arrive after the row.
	 */
	function settled(entry: Ended): Ended {
		const summary = recent.find(
			(line) => line.run === entry.run.id && (line.event === 'sweep' || line.event === 'recheck')
		);
		if (!summary) return entry;
		const done = summary.files ?? entry.run.done;
		return {
			...entry,
			// The summary knows better than a snapshot that may not have caught the
			// stop.
			cut: !!summary.stopped,
			run: {
				...entry.run,
				done,
				total: done + (summary.stopped ?? 0) || entry.run.total,
				counts: summary.counts ?? entry.run.counts,
				seconds: summary.seconds ?? entry.run.seconds,
				active: []
			}
		};
	}

	// Live runs first, then ended ones.
	const showing = $derived([
		...activity.runs.map((run) => ({ run, at: 0, cut: false, ended: false })),
		...finished.map((entry) => ({ ...settled(entry), ended: true }))
	]);

	const files = $derived(
		showing.flatMap((entry) =>
			fileRows(entry.run).map((row) => ({ ...entry, row, key: `${entry.run.id}:${row.path}` }))
		)
	);
	const activeFiles = $derived(
		files.filter((file) => !file.ended && file.row.live && file.row.live.stage !== 'waiting')
	);
	const queuedFiles = $derived(
		files
			.filter(
				(file) =>
					!file.ended &&
					((file.row.waiting && !file.row.skipped) || file.row.live?.stage === 'waiting')
			)
			.sort((left, right) => Number(!!right.row.live) - Number(!!left.row.live))
	);
	const recentFiles = $derived(
		files.filter(
			(file) => file.ended || (!file.row.live && (!file.row.waiting || file.row.skipped))
		)
	);
	const failed = $derived(
		showing.reduce((count, entry) => count + (entry.run.counts.failed ?? 0), 0)
	);
	const combinedResults = $derived.by(() => {
		const counts: Record<string, number> = {};
		for (const entry of showing) {
			for (const [verdict, count] of Object.entries(entry.run.counts))
				counts[verdict] = (counts[verdict] ?? 0) + count;
		}
		return counts;
	});

	const resultRows = $derived(tally(combinedResults));

	// Progress covers current work; result counts also retain recent outcomes.
	const completedCount = $derived(activity.runs.reduce((count, run) => count + run.done, 0));
	const totalCount = $derived(activity.runs.reduce((count, run) => count + run.total, 0));
	const discovering = $derived(activity.runs.some((run) => run.walking || !run.total));
	const completion = $derived(totalCount ? Math.min(1, completedCount / totalCount) : 0);

	const waiting = $derived(Math.max(activity.queue, queuedFiles.length));
	const processingState = $derived(
		holding
			? 'Paused'
			: !activity.runs.length
				? 'Idle'
				: activity.runs.every((run) => run.stopping)
					? 'Stopping'
					: 'Working'
	);
	let queueExpanded = $state(false);
	let opened = $state<Record<string, boolean>>({});
	$effect(() => (activity.runs.length ? ticking() : undefined));

	// A walk holding the sweep cache; the service refuses a second. A re-check
	// counts.
	const sweeping = $derived(
		activity.runs.some((run) => run.kind === 'sweep' || run.kind === 'recheck')
	);

	// Paused workers finishing, or the next scheduled check while idle.
	const sub = $derived.by(() => {
		if (holding) return activeFiles.length ? 'Finishing in-progress files' : '';
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

	// One timer armed to the oldest deadline, re-armed when the list changes.
	$effect(() => {
		if (!finished.length) return;
		const soonest = Math.min(...finished.map((entry) => entry.at)) + LINGER_MS;
		const timer = setTimeout(
			() => {
				const now = Date.now();
				finished = finished.filter((entry) => now - entry.at < LINGER_MS);
			},
			Math.max(0, soonest - Date.now())
		);
		return () => clearTimeout(timer);
	});

	// News about a run, not a tick of one: the rows draw clocks and bars from the
	// last snapshot and its age.
	function saw({ now: fresh, before, missed }: Landed) {
		// A new stamp is a restart, which no run survives. Both looks can succeed
		// either side of one, which is the half `missed` cannot see.
		const restarted = !!before.up_since && fresh.up_since !== before.up_since;
		const alive = new Set(fresh.runs.map((entry) => entry.id));
		// By id, not count: one run ending as another starts leaves the count
		// unchanged.
		const gone = before.runs.filter((entry) => !alive.has(entry.id));
		if (gone.length) {
			// A run that left on its own finished, and its row is a receipt. One that
			// vanished across an outage or restart did not.
			if (!missed && !restarted) {
				// Newest first.
				finished = [
					...gone.map((run) => ({ run, at: Date.now(), cut: run.stopping })),
					...finished
				];
			}
			// The run has just written its summary.
			onmoved(true);
		} else {
			// A delivery can start and finish between two looks without ever showing
			// here.
			onmoved(false);
		}
	}

	// Read once: the page hands in the same snapshot for the life of the panel.
	// svelte-ignore state_referenced_locally
	onDestroy(snapshot.watch({ pace, saw }));

	// Every button goes through here: names what is under way, keeps a refusal's
	// words, and refreshes rather than guessing.
	async function act(name: string, action: () => Promise<unknown>) {
		busy = name;
		refusal = '';
		armed = '';
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

	// One file off one run. No confirm: nothing is lost but the encode, and the
	// next sweep still reaches the file.
	function skip(entry: Run, path: string) {
		act(`skip-${path}`, () => skipFile(entry.id, path));
	}

	// What is being left alone for now, and how long is left of each.
	const held = $derived(activity.holds ?? []);

	function until(hold: Hold): string {
		return hold.seconds ? `${duration(hold.seconds)} left` : 'On hold';
	}

	function release(hold: Hold) {
		act(`lift-${hold.path}`, () => lift({ paths: [hold.path] }));
	}

	// A plan goes on one press. Processing asks first: nothing puts the old file
	// back.
	const run = (mode: RunMode) => act(`start-${mode}`, () => startSweep(mode));
	const sweepNow = () => run('apply');
	const abortNow = () => act('abort', stopEverything);

	const pressStart = (mode: RunMode) =>
		mode === 'apply' ? (raisedHere(), (armed = 'rewrite')) : run(mode);

	const starting = $derived<'' | RunMode>(
		busy === 'start-report' ? 'report' : busy === 'start-apply' ? 'apply' : ''
	);

	// The control the confirm was raised from, taken at the press: raising it
	// disables that control, and the browser blurs a disabled button.
	let raisedFrom: HTMLButtonElement | null = null;

	function raisedHere() {
		raisedFrom =
			document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
	}

	// The confirm takes the keyboard and hands it back. See $lib/modal.
	const claim = (node: HTMLElement) => ({ destroy: keyboard(node, raisedFrom) });

	const focusStop = (node: HTMLElement) => ({
		destroy: keyboard(() => node.querySelector<HTMLButtonElement>('button'), raisedFrom)
	});

	// The last sweep uses the same verdict words as file outcomes.
	const tallyList = 'flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-[12px]';
</script>

<!-- The second press on something irreversible: the consequence in full, then
     Cancel and the verb itself, never "OK". -->
{#snippet confirm(question: string, verb: string, go: () => void)}
	<div
		use:claim
		tabindex="-1"
		role="alertdialog"
		aria-label={verb}
		aria-describedby="confirm-question"
		class="border-t border-danger/40 bg-danger/10 px-4 py-3"
	>
		<p id="confirm-question" class="text-[12.5px]">{question}</p>
		<div class="mt-2.5 flex items-center gap-2">
			<button onclick={() => (armed = '')} disabled={!!busy} class={button}>Cancel</button>
			<button onclick={go} disabled={!!busy} class={danger}>{verb}</button>
		</div>
	</div>
{/snippet}

{#snippet verdicts(counts: Record<string, number>)}
	{#each tally(counts) as verdict (verdict.state)}
		<li class="flex items-center gap-1.5" title={verdict.hint}>
			<span class={verdict.trouble ? 'font-medium text-danger' : 'text-dim'}>{verdict.label}</span>
			<span class="text-faint tabular-nums">{verdict.count.toLocaleString()}</span>
		</li>
	{/each}
{/snippet}

{#snippet fileList(items: typeof files)}
	<ul class="divide-y divide-line [overflow-anchor:none]">
		{#each items as file (file.key)}
			<RunFile
				run={file.run.id}
				row={file.row}
				origin={`${source(file.run)}${file.run.dry_run && file.run.kind !== 'import' ? ' · plan only' : ''}`}
				age={file.ended ? 0 : since(file.run.seen)}
				ended={file.ended}
				id={`file-${file.run.id}-${file.row.path}`}
				open={!!opened[file.key]}
				skippable={admin &&
					!file.ended &&
					!file.run.stopping &&
					(!!file.row.live || !!file.row.waiting) &&
					!file.row.skipped}
				busy={!!busy}
				ontoggle={() => (opened = { ...opened, [file.key]: !opened[file.key] })}
				onskip={() => skip(file.run, file.row.path)}
			/>
		{/each}
	</ul>
{/snippet}

<section aria-labelledby="now" class="mt-6 rounded-xl border border-line bg-raised">
	<div class="flex items-start gap-3 px-4 py-5 sm:px-5">
		<div class="min-w-0 flex-1">
			<h2
				id="now"
				class="flex items-center gap-2.5 text-[23px] leading-tight font-semibold tracking-tight"
			>
				<span
					aria-hidden="true"
					class={`h-2 w-2 flex-none rounded-full ${holding || activity.runs.length ? 'bg-accent-fill' : 'bg-line-strong'}`}
				></span>
				{processingState}
				{#if failed}<span class="ml-auto text-[12px] font-medium tracking-normal text-danger"
						>{failed} failed</span
					>{/if}
			</h2>
			{#if sub}
				<p class="mt-2 text-[12.5px] text-dim">{sub}</p>
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
					{#if !discovering}<span class="ml-auto text-[12px] font-medium text-accent tabular-nums"
							>{Math.floor(completion * 100)}%</span
						>{/if}
				</div>
				{#if !discovering}<Bar
						class="mt-3 !h-1.5"
						fill={completion}
						now={completedCount}
						max={totalCount}
						text={`${completedCount} of ${totalCount} files processed across active runs`}
					/>{/if}
			{/if}
			{#if discovering}<p class="mt-2 text-[12px] text-dim">
					Discovering files{totalCount ? ' · totals may grow' : ''}…
				</p>{/if}
		</div>
	{/if}

	<div
		class={`items-center px-4 pb-3 sm:px-5 ${admin && ((!activity.runs.length && !holding) || (holding && activity.runs.length)) ? 'grid grid-cols-3 gap-3 sm:flex' : 'flex flex-wrap gap-1'}`}
		aria-label="Processing controls"
	>
		{#if admin}
			{#if holding}
				<button
					onclick={() => act('resume', resume)}
					disabled={!!busy}
					class={`min-w-0 flex-1 !px-2 !text-[12px] sm:flex-none sm:!px-3.5 ${primary}`}
				>
					{#if busy !== 'resume'}<Glyph name="play" />{/if}
					{busy === 'resume' ? 'Resuming…' : 'Resume'}
				</button>
			{:else}
				<button
					onclick={() => act('pause', pause)}
					disabled={!!busy}
					aria-label="Pause"
					title="Pause. Files already started finish and nothing new starts."
					class="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-2 text-[12px] font-medium text-dim hover:bg-sunken disabled:opacity-(--disabled) sm:px-3"
				>
					<Glyph name="pause" />
					<span>
						{busy === 'pause' ? 'Pausing…' : 'Pause'}
					</span>
				</button>
				{#if !sweeping}
					<!-- Dead while the confirm is up, so there is one way to say yes. -->
					<RunButtons
						fill
						mobileColumns={!activity.runs.length}
						mayRewrite={activity.may_rewrite}
						disabled={!!busy || arming === 'rewrite'}
						busy={starting}
						onrun={pressStart}
					/>
				{/if}
			{/if}
			{#if activity.runs.length}
				<button
					onclick={() => (raisedHere(), (armed = arming === 'abort' ? '' : 'abort'))}
					disabled={!!busy || (activity.runs.every((run) => run.stopping) && !rewriting)}
					aria-expanded={arming === 'abort'}
					aria-controls="stop-confirmation"
					class="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-[12px] font-medium text-danger hover:bg-danger/10 disabled:opacity-(--disabled)"
				>
					<Glyph name="stop" size={9} />
					{busy === 'abort' || (activity.runs.every((run) => run.stopping) && !rewriting)
						? 'Stopping…'
						: 'Stop all'}
				</button>
			{/if}
		{/if}
		{#if activity.runs.length}
			<button
				onclick={() => (detailsOpen = !detailsOpen)}
				aria-expanded={detailsOpen}
				aria-controls="processing-details"
				aria-label="Processing details"
				class={`${holding && admin ? 'w-full justify-center px-2 sm:ml-auto sm:w-auto sm:px-3' : 'ml-auto px-3'} inline-flex min-h-11 items-center gap-2 rounded-lg text-[12px] font-medium text-dim hover:bg-sunken`}
			>
				<Glyph name="sliders" /> <span class="sm:hidden">Details</span><span
					class="hidden sm:inline">Processing details</span
				>
			</button>
		{/if}
	</div>
	{#if arming === 'abort'}
		<div class="px-4 pb-4 sm:px-5">
			<div
				use:focusStop
				id="stop-confirmation"
				role="alertdialog"
				aria-labelledby="stop-title"
				aria-describedby="stop-description"
				tabindex="-1"
				onkeydown={(event) => {
					if (event.key === 'Escape') {
						event.preventDefault();
						event.stopPropagation();
						armed = '';
					}
				}}
				class="rounded-lg border border-line-strong bg-sunken/60 p-4"
			>
				<h3 id="stop-title" class="text-[15px] font-semibold">Stop all processing?</h3>
				<p id="stop-description" class="mt-2 text-[12.5px] leading-relaxed text-dim">
					Queued files won’t start. {rewriting
						? 'Current rewrites will be interrupted; their progress will be lost.'
						: 'Files already being checked will finish.'}
				</p>
				<dl class="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-[12px]">
					<div class="flex items-baseline gap-2">
						<dt class="text-dim">Queued</dt>
						<dd class="font-semibold tabular-nums">{waiting.toLocaleString()}</dd>
					</div>
					{#if rewriting}<div class="flex items-baseline gap-2">
							<dt class="text-dim">Rewriting</dt>
							<dd class="font-semibold tabular-nums">{activity.rewrites.toLocaleString()}</dd>
						</div>{/if}
				</dl>
				<div
					class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3"
				>
					<p class="text-[12px] text-dim">Original files stay untouched.</p>
					<div class="flex w-full gap-2 sm:w-auto">
						<button
							onclick={() => (armed = '')}
							disabled={!!busy}
							class="min-h-11 flex-1 rounded-lg px-4 text-[12px] font-medium text-dim hover:bg-raised sm:flex-none"
							>Cancel</button
						>
						<button
							onclick={abortNow}
							disabled={!!busy}
							class={`min-h-11 flex-1 sm:flex-none ${danger}`}
							><Glyph name="stop" size={9} /> Stop all</button
						>
					</div>
				</div>
			</div>
		</div>
	{/if}

	{#if arming === 'rewrite'}
		{@render confirm(
			'Sweeps the library and applies the rules. Files are rewritten on disk with no undo.',
			'Process',
			sweepNow
		)}
	{/if}

	{#if detailsOpen && activity.runs.length}
		<div
			id="processing-details"
			role="region"
			aria-label="Processing details"
			class="border-t border-line bg-sunken/30 px-4 py-4 sm:px-5"
		>
			<h3 class="text-[13px] font-semibold">Processing details</h3>
			<p class="mt-1 text-[12px] text-faint">Results from current and recently completed work.</p>
			{#if resultRows.length}
				<table class="mt-3 w-full text-[12px]">
					<caption class="sr-only">Combined processing outcomes</caption>
					<thead
						><tr class="border-b border-line text-faint"
							><th scope="col" class="pb-2 text-left font-medium">Outcome</th><th
								scope="col"
								class="pb-2 text-right font-medium">Files</th
							></tr
						></thead
					>
					<tbody class="divide-y divide-line">
						{#each resultRows as result (result.state)}
							<tr title={result.hint} class={result.trouble ? 'text-danger' : ''}>
								<th scope="row" class="py-2.5 text-left font-medium">{result.label}</th>
								<td class="py-2.5 text-right font-semibold tabular-nums"
									>{result.count.toLocaleString()}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="mt-3 text-[12px] text-dim">No results yet.</p>
			{/if}
		</div>
	{/if}

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
		<div
			class={`border-t border-line px-4 py-4 sm:px-5 ${waiting && activeFiles.length ? 'grid gap-4 lg:grid-cols-2' : ''}`}
		>
			{#if activeFiles.length}
				<div>
					<div class="flex flex-wrap items-baseline justify-between gap-2">
						<h3 class="text-[13px] font-semibold">
							Processing <span class="ml-1 text-faint tabular-nums">{activeFiles.length}</span>
						</h3>
					</div>
					<div class="mt-3 rounded-lg bg-accent/5 px-3">{@render fileList(activeFiles)}</div>
				</div>
			{/if}
			{#if waiting}
				<div class="rounded-lg bg-sunken/60 px-3 pt-3 pb-1">
					<h3 class="text-[12px] font-semibold">
						Up next <span class="ml-1 font-normal text-faint tabular-nums"
							>{waiting.toLocaleString()}</span
						>
					</h3>
					{@render fileList(queueExpanded ? queuedFiles : queuedFiles.slice(0, 3))}
					{#if queuedFiles.length > 3}
						<button
							class="min-h-11 text-[12px] text-accent hover:text-fg"
							aria-expanded={queueExpanded}
							onclick={() => (queueExpanded = !queueExpanded)}
							>{queueExpanded ? 'Show fewer' : `Show ${queuedFiles.length - 3} more`}</button
						>
					{/if}
					{#if waiting > queuedFiles.length}<p class="py-2 text-[11.5px] text-faint">
							{queuedFiles.length
								? 'Showing the available queue preview.'
								: 'Waiting files will appear as they are discovered.'}
						</p>{/if}
				</div>
			{/if}
		</div>
	{/if}

	<!-- Explicit holds apply to titles and future work. -->
	{#if held.length}
		<details class="group/service border-t border-line px-4 text-[12.5px] sm:px-5">
			<summary
				class="flex min-h-12 cursor-pointer list-none items-center gap-2 text-dim hover:text-fg [&::-webkit-details-marker]:hidden"
			>
				<span class="transition-transform group-open/service:rotate-90"
					><Glyph name="chevron" size={10} /></span
				>
				Held <span class="text-faint tabular-nums">{held.length}</span>
			</summary>
			<div class="flex flex-col gap-3 pb-4">
				{#if held.length}
					<!-- Named, not counted: the point of a hold is knowing which title it
				     is on, and it is the only thing here with a control. -->
					<ul class="flex flex-col gap-3">
						{#each held as hold (hold.path)}
							<li class="flex items-start gap-3 rounded-lg bg-sunken/60 p-3">
								<div class="min-w-0 flex-1">
									<p class="font-medium wrap-anywhere">{hold.name || titled(hold.path)}</p>
									<p class="mt-1 text-[11.5px] text-faint">{until(hold)}</p>
									{#if hold.reason}<p class="mt-2 wrap-anywhere text-dim">{hold.reason}</p>{/if}
								</div>
								{#if admin}
									<button
										onclick={() => release(hold)}
										disabled={!!busy}
										aria-label={`Lift hold on ${hold.name || titled(hold.path)}`}
										class="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg px-3 text-[12px] font-medium text-accent hover:bg-accent/10 disabled:opacity-(--disabled)"
									>
										{busy === `lift-${hold.path}` ? 'Lifting…' : 'Lift hold'}
									</button>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</details>
	{/if}
	{#if recentFiles.length || (swept && !sweeping)}
		<details class="group/outcomes border-t border-line px-4 sm:px-5">
			<summary
				class="flex min-h-12 cursor-pointer list-none items-center gap-2 text-[12.5px] text-dim hover:text-fg [&::-webkit-details-marker]:hidden"
			>
				<span class="transition-transform group-open/outcomes:rotate-90"
					><Glyph name="chevron" size={10} /></span
				>
				Recent outcomes <span class="text-faint tabular-nums">{recentFiles.length || ''}</span>
			</summary>
			<div class="mb-4 rounded-lg bg-sunken/60 px-3">
				{#if swept && !sweeping}
					<ul class={`${tallyList} py-3`}>
						<li class="flex items-baseline gap-1.5 text-[12.5px]">
							<span class="text-dim">{headline(swept)}</span>
							<time datetime={swept.ts} title={stamp(swept.ts)} class="text-faint">
								{ago(swept.ts)}
							</time>
						</li>
						{@render verdicts(swept.counts ?? {})}
						{#if swept.library_bytes}
							<li class="text-faint">{size(swept.library_bytes)}</li>
						{/if}
					</ul>
				{/if}
				{@render fileList(recentFiles)}
			</div>
		</details>
	{/if}
</section>
