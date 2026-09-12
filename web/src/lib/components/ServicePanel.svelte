<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import { resolve } from '$app/paths';
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import RunFile from '$lib/components/RunFile.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import type { Landed, Snapshot } from '$lib/activity.svelte';
	import { refusalText } from '$lib/api';
	import { button, danger, primary } from '$lib/controls';
	import type { Event } from '$lib/events';
	import { size, soon, stamp, titled } from '$lib/format';
	import { lift, place, type Hold } from '$lib/holds';
	import type { RunMode } from '$lib/library';
	import { keyboard } from '$lib/modal';
	import {
		fileRows,
		source,
		duration,
		remaining,
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
		onmoved,
		onpressed
	}: {
		/** What the service is doing, read for the page rather than here. */
		snapshot: Snapshot;
		admin?: boolean;
		/** The last few history lines, for the numbers an ended run left. */
		recent: Event[];
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
	let resultsOpen = $state(false);
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

	// Keep a bounded set of completed runs available until newer runs replace them.
	const RECENT_RUNS = 4;

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
			cut: entry.cut || !!summary.stopped,
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
	// Keep each run's scope and mode intact, including summaries loaded after a reload.
	const resultRuns = $derived.by(() => {
		const live = showing.map((entry) => {
			const summary = entry.ended
				? recent.find(
						(line) =>
							line.run === entry.run.id && (line.event === 'sweep' || line.event === 'recheck')
					)
				: undefined;
			return {
				id: entry.run.id,
				label: source(entry.run),
				ts: summary?.ts ?? entry.run.started,
				when: summary ? 'Finished' : 'Started',
				mode: entry.run.dry_run
					? 'Plan only'
					: entry.cut
						? 'Apply mode'
						: entry.ended
							? 'Applied'
							: 'Applying',
				state: entry.ended
					? entry.cut
						? 'Stopped'
						: 'Completed'
					: entry.run.stopping
						? 'Stopping'
						: 'In progress',
				done: entry.run.done,
				counts: entry.run.counts,
				seconds: entry.run.seconds + (entry.ended || snapshot.offline ? 0 : since(entry.run.seen)),
				bytes: summary?.library_bytes
			};
		});
		const ids = new Set(live.map((entry) => entry.id));
		const summaries = recent
			.filter(
				(entry) =>
					(entry.event === 'sweep' || entry.event === 'recheck') && !ids.has(entry.run ?? '')
			)
			.slice(0, Math.max(0, RECENT_RUNS - finished.length));
		return [
			...live,
			...summaries.map((entry) => ({
				id: entry.run ?? entry.ts,
				label: entry.event === 'sweep' ? 'Sweep' : 'Re-check',
				ts: entry.ts,
				when: 'Finished',
				mode: entry.dry_run ? 'Plan only' : entry.stopped ? 'Apply mode' : 'Applied',
				state: entry.stopped ? 'Stopped' : 'Completed',
				done: entry.files ?? 0,
				counts: entry.counts ?? {},
				seconds: entry.seconds ?? 0,
				bytes: entry.library_bytes
			}))
		];
	});
	const failed = $derived(
		resultRuns.reduce((count, entry) => count + (entry.counts.failed ?? 0), 0)
	);
	const failedFiles = $derived(files.filter((file) => file.row.verdict === 'failed'));
	const failureEvents = $derived(
		recent.filter(
			(entry) =>
				entry.event === 'failed' &&
				entry.path &&
				resultRuns.some((run) => run.id === entry.run) &&
				!failedFiles.some((file) => file.run.id === entry.run && file.row.path === entry.path)
		)
	);
	let failuresOpen = $state(false);
	let failurePanel = $state<HTMLDivElement>();
	async function revealFailures() {
		failuresOpen = !failuresOpen;
		if (!failuresOpen) return;
		opened = { ...opened, ...Object.fromEntries(failedFiles.map((file) => [file.key, true])) };
		await tick();
		failurePanel?.focus();
	}
	let lastUpdated = $state(new Date().toISOString());
	let expandedResults = $state(false);

	// Progress covers current work; result counts also retain recent outcomes.
	const completedCount = $derived(activity.runs.reduce((count, run) => count + run.done, 0));
	const totalCount = $derived(activity.runs.reduce((count, run) => count + run.total, 0));
	const discovering = $derived(activity.runs.some((run) => run.walking || !run.total));
	const completion = $derived(totalCount ? Math.min(1, completedCount / totalCount) : 0);

	const waiting = $derived(Math.max(activity.queue, queuedFiles.length));
	const processingState = $derived(
		snapshot.offline
			? 'Connection lost'
			: holding
				? 'Paused'
				: !activity.runs.length
					? 'Idle'
					: activity.runs.every((run) => run.stopping)
						? 'Stopping'
						: 'Working'
	);
	let queueExpanded = $state(false);
	let opened = $state<Record<string, boolean>>({});
	$effect(() => (activity.runs.length && !snapshot.offline ? ticking() : undefined));

	const encodingCount = $derived(
		activeFiles.filter((file) => file.row.live?.stage === 'encoding').length
	);
	const stageSummary = $derived(
		[
			encodingCount ? `Rewriting ${encodingCount} ${encodingCount === 1 ? 'file' : 'files'}` : '',
			activeFiles.length > encodingCount
				? `Checking ${activeFiles.length - encodingCount} ${activeFiles.length - encodingCount === 1 ? 'file' : 'files'}`
				: '',
			waiting ? `${waiting.toLocaleString()} waiting` : ''
		]
			.filter(Boolean)
			.join(' · ')
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
	function saw({ now: fresh, before, missed }: Landed) {
		lastUpdated = new Date().toISOString();
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
					...gone.map((run) => ({ run, at: Date.now(), cut: run.stopping || busy === 'abort' })),
					...finished
				].slice(0, RECENT_RUNS);
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

	async function holdFile(entry: Run, path: string, seconds: number) {
		busy = `hold-${path}`;
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

{#snippet fileList(items: typeof files, showOrigin = true, scope = 'file')}
	<ul class="divide-y divide-line [overflow-anchor:none]">
		{#each items as file (file.key)}
			<RunFile
				run={file.run.id}
				row={file.row}
				origin={[
					showOrigin ? source(file.run) : '',
					file.run.dry_run && file.run.kind !== 'import' ? 'Plan only' : ''
				]
					.filter(Boolean)
					.join(' · ')}
				age={file.ended || snapshot.offline ? 0 : since(file.run.seen)}
				ended={file.ended}
				id={`${scope}-${file.run.id}-${file.row.path}`}
				open={!!opened[file.key]}
				skippable={admin &&
					!file.ended &&
					!file.run.stopping &&
					(!!file.row.live || !!file.row.waiting) &&
					!file.row.skipped}
				busy={!!busy}
				ontoggle={() => (opened = { ...opened, [file.key]: !opened[file.key] })}
				onskip={() => skip(file.run, file.row.path)}
				onhold={(seconds) => holdFile(file.run, file.row.path, seconds)}
			/>
		{/each}
	</ul>
{/snippet}

<section aria-labelledby="now" class="mt-6 rounded-xl border border-line bg-raised">
	<div class="px-4 py-5 sm:px-5">
		<div class="flex items-start justify-between gap-3">
			<h2
				id="now"
				class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1 text-[23px] leading-tight font-semibold tracking-tight"
			>
				<span
					aria-hidden="true"
					class={`h-2 w-2 flex-none rounded-full ${snapshot.offline ? 'bg-danger' : holding || activity.runs.length ? 'bg-accent-fill' : 'bg-line-strong'}`}
				></span>
				{processingState}
				{#if failed}<button
						onclick={revealFailures}
						aria-expanded={failuresOpen}
						aria-controls="processing-failures"
						class="min-h-11 rounded-lg px-2 text-[12px] font-medium tracking-normal text-danger underline underline-offset-2 hover:bg-danger/10"
						>{failed} failed</button
					>{/if}
			</h2>
			<button
				onclick={() => (resultsOpen = !resultsOpen)}
				aria-expanded={resultsOpen}
				aria-controls="processing-results"
				class="-my-2 inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[12px] font-medium whitespace-nowrap text-dim hover:bg-sunken"
			>
				<Glyph name="chart" size={14} /> Results
			</button>
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
			{#if stageSummary}<p class="mb-3 text-[12.5px] text-dim">
					{snapshot.offline ? 'At last update: ' : ''}{stageSummary}
				</p>{/if}
			{#if !snapshot.offline}
				{#each activity.runs as run (run.id)}
					{@const estimate = remaining(run, holding, since(run.seen))}
					{#if estimate}<p class="mb-3 text-[12px] text-dim">
							{activity.runs.length > 1 ? `${source(run)}: ` : ''}{estimate}
						</p>{/if}
				{/each}
			{/if}
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
					aria-describedby="pause-explanation"
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
						? 'Current rewrites will be interrupted and their progress will be lost.'
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

	{#if admin && !holding}
		<p id="pause-explanation" class="px-4 pb-3 text-[12px] text-dim sm:px-5">
			Pause lets files already started finish, then waits until you resume.
		</p>
	{/if}

	{#if failuresOpen}
		<div
			id="processing-failures"
			bind:this={failurePanel}
			tabindex="-1"
			role="region"
			aria-label="Failed files"
			class="border-t border-line px-4 py-4 sm:px-5"
		>
			<h3 class="text-[13px] font-semibold">Failed files</h3>
			<p class="mt-1 text-[12px] text-dim">
				Failures from the runs listed in Results. Only recent file details are available here.
			</p>
			{@render fileList(failedFiles, true, 'failure')}
			<ul class="divide-y divide-line">
				{#each failureEvents as entry (entry)}
					<li class="py-3 text-[12px]">
						<p class="font-medium">{titled(entry.path)}</p>
						<p class="mt-1 wrap-anywhere text-danger">{entry.detail || 'Processing failed.'}</p>
						<p class="mt-1 font-mono text-[11px] wrap-anywhere text-faint">{entry.path}</p>
					</li>
				{/each}
			</ul>
			{#if !failedFiles.length && !failureEvents.length}<p class="mt-3 text-[12px] text-dim">
					File details are no longer in this preview. Open activity to find older failures.
				</p>{/if}
			<a
				href={resolve('/events?filter=issues')}
				class="inline-flex min-h-11 items-center text-[12px] text-accent hover:text-fg"
				>View issues in activity</a
			>
		</div>
	{/if}

	{#if resultsOpen}
		<div
			id="processing-results"
			role="region"
			aria-label="Results"
			class="border-t border-line bg-sunken/30 px-4 py-4 sm:px-5"
		>
			<h3 class="text-[13px] font-semibold">Results by run</h3>
			<p class="mt-1 text-[12px] text-dim">
				Active runs and recent completed runs. Each run counts its own files.
			</p>
			{#each resultRuns as result (result.id)}
				<article
					aria-label={`${result.label} · ${result.mode}`}
					class="mt-3 border-t border-line pt-3"
				>
					<div class="flex flex-wrap items-baseline justify-between gap-2 text-[13px]">
						<h4 class="font-semibold">{result.label}</h4>
						<span class="text-[12px] text-dim">{result.mode} · {result.state}</span>
					</div>
					<p class="mt-1 text-[11.5px] text-faint">
						{result.when} <time datetime={result.ts}>{stamp(result.ts)}</time>
					</p>
					<dl class="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-[12px]">
						<div>
							<dt class="text-faint">Files processed</dt>
							<dd class="mt-1 font-medium tabular-nums">{result.done.toLocaleString()}</dd>
						</div>
						<div>
							<dt class="text-faint">Elapsed</dt>
							<dd class="mt-1 font-medium tabular-nums">{duration(result.seconds)}</dd>
						</div>
						{#if result.bytes}<div>
								<dt class="text-faint">Library size</dt>
								<dd class="mt-1 font-medium">{size(result.bytes)}</dd>
							</div>{/if}
					</dl>
					{#if tally(result.counts).length}<ul
							aria-label="Outcome counts"
							class={`${tallyList} mt-3`}
						>
							{@render verdicts(result.counts)}
						</ul>{/if}
				</article>
			{:else}
				<p class="mt-3 text-[12px] text-dim">No recent run results available.</p>
			{/each}
			<a
				href={resolve('/events?filter=runs')}
				class="mt-2 inline-flex min-h-11 items-center text-[12px] text-accent hover:text-fg"
				>View run history</a
			>
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
				<div>
					<h3 class="text-[13px] font-semibold">
						Waiting <span class="ml-1 text-faint tabular-nums">{waiting.toLocaleString()}</span>
					</h3>
					<div class="mt-3 rounded-lg bg-sunken/60 px-3 pb-1">
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
	{#if recentFiles.length}
		<details class="group/recent border-t border-line px-4 sm:px-5">
			<summary
				class="flex min-h-12 cursor-pointer list-none items-center gap-2 text-[12.5px] text-dim hover:text-fg [&::-webkit-details-marker]:hidden"
			>
				<span class="transition-transform group-open/recent:rotate-90"
					><Glyph name="chevron" size={10} /></span
				>
				Recently processed <span class="text-faint tabular-nums">{recentFiles.length || ''}</span>
			</summary>
			<div class="mb-3 rounded-lg bg-sunken/60 px-3 py-2">
				{#if recentFiles.length}
					<p class="pt-1 text-[11px] text-faint">A preview of recent file results.</p>
					{@render fileList(expandedResults ? recentFiles : recentFiles.slice(0, 5), false)}
					{#if recentFiles.length > 5}
						<button
							onclick={() => (expandedResults = !expandedResults)}
							aria-expanded={expandedResults}
							class="min-h-11 text-[12px] text-accent hover:text-fg"
						>
							{expandedResults ? 'Show fewer' : `Show ${recentFiles.length - 5} more`}
						</button>
					{/if}
				{:else}
					<p class="py-1 text-[11.5px] text-faint">No recent file details available.</p>
				{/if}
				<a
					href={resolve('/events')}
					class="inline-flex min-h-11 items-center text-[12px] text-accent hover:text-fg"
					>View all activity</a
				>
			</div>
		</details>
	{/if}
</section>
