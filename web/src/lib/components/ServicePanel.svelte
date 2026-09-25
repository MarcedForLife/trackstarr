<script lang="ts">
	import FileTitle from './FileTitle.svelte';
	import type { Runner } from './TitleSheet.svelte';
	let titleDetails: FileTitle;
	import { onDestroy } from 'svelte';
	import { flip } from 'svelte/animate';
	import { fade } from 'svelte/transition';
	import { resolve } from '$app/paths';
	import { since, ticking } from '$lib/clock.svelte';
	import Count from '$lib/components/Count.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import PausedFile from '$lib/components/PausedFile.svelte';
	import QueueDialog from '$lib/components/QueueDialog.svelte';
	import { queueAction } from '$lib/queue';
	import Reveal from '$lib/components/Reveal.svelte';
	import RunFile from '$lib/components/RunFile.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import Spinner from '$lib/components/Spinner.svelte';
	import {
		keepRowSlots,
		rowArrive,
		rowFade,
		rowLeave,
		rowSlide,
		swapLeave
	} from '$lib/motion.svelte';
	import SweepButtons from '$lib/components/SweepButtons.svelte';
	import type { Landed, Snapshot } from '$lib/activity.svelte';
	import { refusalText } from '$lib/api';
	import { button, danger, frosted, mark, markAccent, markDanger, markQuiet } from '$lib/controls';
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
		type FileRow,
		type Run
	} from '$lib/runs';
	import { told } from '$lib/stream';

	// Current work and controls. Completed work belongs in Events.
	let {
		snapshot,
		admin = false,
		runner,
		onmoved,
		onpressed
	}: {
		/** What the service is doing, read for the page rather than here. */
		snapshot: Snapshot;
		admin?: boolean;
		/** Running a title the rows open, from the page that owns the runs. */
		runner?: Runner;
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

	// Stop all confirms first, in a panel hung off the button's right edge.
	const confirm = popover();
	let stopButton = $state<HTMLButtonElement>();
	let stopPanel = $state<HTMLDivElement>();

	// What caps the rewrites, hung off the readout that names it. Right-hung,
	// since the readout ends its row.
	const capacity = popover({ edge: 'right' });
	let slotsButton = $state<HTMLButtonElement>();
	let slotsPanel = $state<HTMLDivElement>();

	// The schedule, hung left off the idle state that starts its row.
	const nextCheck = popover({ edge: 'left' });
	let stateButton = $state<HTMLButtonElement>();
	let schedulePanel = $state<HTMLDivElement>();

	const holding = $derived(activity.paused);
	// Encoding this second, which is the only way to tell a rewrite from a probe.
	const rewriting = $derived(activity.rewrites > 0);
	// Every run winding down, unless a rewrite is still going.
	const stoppingAll = $derived(
		busy === 'abort' || (activity.runs.every((run) => run.stopping) && !rewriting)
	);
	// Zero on a build that did not send it, which draws no readout.
	const slots = $derived(activity.slots ?? 0);

	// One file under a run, with where the queue has it. `at` is zero for a row a
	// worker already holds, which has outgrown its place.
	type Row = { run: Run; row: FileRow; key: string; at: number };

	const files = $derived<Row[]>(
		activity.runs.flatMap((run) =>
			fileRows(run).map((row) => ({ run, row, key: `${run.id}:${row.path}`, at: 0 }))
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
	// The queue's own numbering where the service gives it. An older build's rows
	// are the head of the queue, so their place in the list is their place.
	const queuedFiles = $derived<Row[]>(
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
									key: `${item.run}:${item.path}`,
									at: item.position
								}
							]
						: [];
				})
			: legacyQueue.map((file, index) => ({ ...file, at: index + 1 }))
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
		return [mixed ? source(run) : '', run.dry_run && run.type !== 'import' ? 'Plan only' : '']
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
	// A run with no file count yet, the only thing that moves the total. Not
	// `walking`: a sweep lists the library before probing any of it, so its total
	// is exact and only the rewrites it will find are a floor.
	const listing = $derived(activity.runs.some((run) => !run.total));
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
	const hasWork = $derived(!!activity.runs.length || !!activeFiles.length || !!waiting);
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
	const tone = $derived(
		snapshot.offline ? 'text-danger' : holding || activity.runs.length ? 'text-accent' : 'text-dim'
	);
	let queueOpen = $state(false);
	let pausedOpen = $state(false);
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
		activity.runs.some((run) => run.type === 'sweep' || run.type === 'recheck')
	);

	// Paused workers finishing, or the wait for a lost connection.
	const sub = $derived.by(() => {
		if (snapshot.offline)
			return `Showing the last update from ${stamp(lastUpdated)}. Reconnecting…`;
		if (holding)
			return activeFiles.length
				? 'Files already started will finish. Nothing new starts until you resume.'
				: 'Nothing new starts until you resume.';
		return '';
	});

	// The next sweep, told by the idle state since a line under it crowded the
	// marks on a phone.
	const schedule = $derived(
		processingState !== 'Idle'
			? ''
			: activity.next_sweep
				? `Next scheduled check ${soon(activity.next_sweep)}.`
				: 'No sweep is scheduled.'
	);

	// The fallback poll rate this panel asks the snapshot for. A run is a readout
	// being watched; an idle service is a page left on a desk.
	const BUSY_MS = 2000;
	const IDLE_MS = 15000;

	// Fast polling after a start, which answers before the sweep lists the
	// *arrs.
	const SETTLE_MS = 30000;
	let expecting = 0;

	// A sweep started here but not yet in a snapshot. Dropped after WARMING_MS,
	// since a short sweep may never show.
	const WARMING_MS = 5000;
	let launched = $state<'' | RunMode>('');
	let launchedAt = 0;

	// Read as the chain re-arms, not watched: a reactive read would rebuild the
	// timer on every answer.
	function pace(): number {
		return told(snapshot.current.runs.length || Date.now() < expecting ? BUSY_MS : IDLE_MS);
	}

	// Each landed snapshot.
	function saw({ now: fresh, before }: Landed) {
		lastUpdated = new Date().toISOString();
		const shown = fresh.runs.some((run) => run.type === 'sweep');
		if (launched && (shown || Date.now() - launchedAt > WARMING_MS)) launched = '';
		const alive = new Set(fresh.runs.map((run) => run.id));
		// Refresh Events when a run ends, including one replaced between
		// snapshots.
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

	function top(run: Run, path: string) {
		act('queue-top', () => queueAction('top', [{ run: run.id, path }]));
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

	let panelRoot = $state<HTMLElement>();
	keepRowSlots(
		() => panelRoot,
		() => [activeFiles, shownQueue, pausedItems]
	);

	function release(pause: Pause) {
		act(`resume-${pause.path}`, () => resumeItem({ paths: [pause.path] }));
	}

	// Both are confirmed in the pair's popover first.
	const run = (mode: RunMode) =>
		act(`start-${mode}`, async () => {
			const answer = await startSweep(mode);
			launched = mode;
			launchedAt = Date.now();
			return answer;
		});
	const abortNow = () => act('abort', stopEverything);

	const starting = $derived<'' | RunMode>(
		busy === 'start-report' ? 'report' : busy === 'start-apply' ? 'apply' : launched
	);

	// The confirmation closes when the last run ends, which takes its button.
	$effect(() => {
		if (confirm.open && !activity.runs.length) confirm.lower();
	});

	// The schedule closes once the service leaves idle.
	$effect(() => {
		if (nextCheck.open && !schedule) nextCheck.lower();
	});
</script>

<svelte:window
	onpointerdown={(event) => {
		for (const held of [confirm, capacity, nextCheck])
			if (held.open && held.outside(event.target as Node)) held.lower();
	}}
	onresize={() => {
		for (const held of [confirm, capacity, nextCheck]) if (held.open) held.lower();
	}}
/>

{#snippet fileList(items: Row[])}
	<ul data-rows class="relative flex flex-col gap-2">
		{#each items as file (file.key)}
			<li animate:flip={rowSlide()} in:fade={rowArrive()} out:rowLeave>
				<!-- A run winding up leaves the file it is on encoding, so a held file
				     keeps its skip. Only the ones it will never reach lose theirs. -->
				<RunFile
					run={file.run.id}
					row={file.row}
					cover={activity.covers?.[file.row.path]}
					plan={activity.plans?.[file.row.path]}
					current={activity.plans_current ?? true}
					onopen={() => titleDetails.show(file.row.path, activity.covers?.[file.row.path])}
					origin={originOf(file.run)}
					place={file.at}
					age={snapshot.offline ? 0 : since(file.run.seen)}
					id={`file-${file.run.id}-${file.row.path}`}
					open={!!opened[file.key]}
					skippable={admin &&
						(!!file.row.live || (!!file.row.waiting && !file.run.stopping)) &&
						!file.row.skipped}
					busy={!!busy}
					ontoggle={() => (opened = { ...opened, [file.key]: !opened[file.key] })}
					ontop={file.row.waiting && file.at !== 1 ? () => top(file.run, file.row.path) : undefined}
					onskip={() => skip(file.run, file.row.path)}
					onpause={(seconds) => pauseFile(file.run, file.row.path, seconds)}
				/>
			</li>
		{/each}
	</ul>
{/snippet}

<section bind:this={panelRoot} aria-labelledby="now" class="mt-4">
	<div class="px-3 pb-3 sm:px-4">
		<h2 id="now" class="text-[17px] leading-tight font-semibold tracking-tight">Activity</h2>
	</div>
	<div class="rounded-2xl border border-line bg-sunken">
		<div class="flex min-h-11 items-center justify-between gap-3 py-2 pr-2 pl-3 sm:pr-3 sm:pl-4">
			<p
				class={`flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1 text-[17px] leading-tight font-semibold tracking-tight transition-colors duration-500 ${tone}`}
			>
				<!-- Keyed, so a state change reads as one word replacing another
				     rather than a redraw. -->
				{#key processingState}
					{#if schedule}
						<!-- A press shows the schedule where there is no hover. -->
						<button
							bind:this={stateButton}
							type="button"
							title={schedule}
							aria-expanded={nextCheck.open}
							aria-controls="next-check"
							onclick={() =>
								stateButton && schedulePanel && nextCheck.toggle(stateButton, schedulePanel)}
							class="-my-2 rounded-lg py-2 text-left"
							in:fade={rowFade()}>{processingState}</button
						>
					{:else}
						<span in:fade={rowFade()}>{processingState}</span>
					{/if}
				{/key}
				{#if failed}<a
						href={resolve('/events?filter=issues')}
						title="View issues in events"
						class="inline-flex min-h-11 items-center rounded-lg px-2 text-[12px] font-medium tracking-normal text-danger underline underline-offset-2 hover:bg-danger/10"
						>{failed} failed</a
					>{/if}
			</p>
			{#if admin}
				<div class="relative flex flex-none items-center gap-2" aria-label="Processing controls">
					{#if holding}
						<button
							onclick={() => act('resume', resume)}
							disabled={!!busy}
							aria-busy={busy === 'resume'}
							aria-label="Resume"
							title="Resume. Queued files start again."
							class={`${mark} ${markAccent}`}
							out:swapLeave
							in:fade={rowFade()}
						>
							<Spinner glyph="play" size={14} busy={busy === 'resume'} />
							<span class="hidden sm:inline">Resume</span>
						</button>
					{:else}
						<button
							onclick={() => act('pause', pause)}
							disabled={!!busy}
							aria-busy={busy === 'pause'}
							aria-label={activity.runs.length ? 'Pause' : 'Suspend'}
							title={activity.runs.length
								? 'Pause. Files already started finish and nothing new starts.'
								: 'Suspend. Nothing new starts until you resume.'}
							class={`${mark} ${markQuiet}`}
							out:swapLeave
							in:fade={rowFade()}
						>
							<Spinner glyph="pause" size={12} busy={busy === 'pause'} />
							<span class="hidden sm:inline">{activity.runs.length ? 'Pause' : 'Suspend'}</span>
						</button>
					{/if}
					{#if activity.runs.length}
						<button
							bind:this={stopButton}
							onclick={() => stopButton && stopPanel && confirm.toggle(stopButton, stopPanel)}
							disabled={!!busy || stoppingAll}
							aria-label="Stop all"
							title="Stop all processing"
							aria-expanded={confirm.open}
							aria-controls="stop-confirmation"
							aria-busy={stoppingAll}
							class={`${mark} ${markDanger}`}
							transition:fade={rowFade()}
						>
							<Spinner glyph="stop" size={10} busy={stoppingAll} />
							<span class="hidden sm:inline">Stop</span>
						</button>
					{/if}
					{#if !sweeping && !holding}
						<div class="flex-none" transition:fade={rowFade()}>
							<SweepButtons
								mayRewrite={activity.may_rewrite}
								disabled={!!busy || !!launched}
								busy={starting}
								onrun={run}
							/>
						</div>
					{/if}
				</div>
			{/if}
		</div>
		<Reveal when={!!sub}>
			<!-- Keyed inside the live region, which must exist before its words change
			     for them to be announced. -->
			<p role="status" class="px-3 pb-3 text-[12.5px] text-dim sm:px-4">
				{#key sub}<span in:fade={rowFade()}>{sub}</span>{/key}
			</p>
		</Reveal>
		<!-- The service's own words, kept until the next button is pressed. -->
		{#if refusal}
			<p role="alert" class="px-3 pb-3 text-[12.5px] text-danger sm:px-4">{refusal}</p>
		{:else if snapshot.offline}
			<p role="status" class="px-3 pb-3 text-[12.5px] text-danger sm:px-4">{snapshot.offline}</p>
		{/if}
		<Reveal when={hasWork}>
			<div class="px-3 pt-1.5 pb-3 sm:px-4 sm:pb-4">
				<Reveal when={!!activity.runs.length}>
					<div aria-label="Overall processing progress">
						{#if totalCount}
							<div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
								<span class="text-[28px] leading-none font-semibold tracking-tight"
									><Count value={completedCount} /></span
								>
								<span class="text-[12px] text-dim"
									>of <Count value={totalCount} />
									{totalCount === 1 ? 'file' : 'files'} processed</span
								>
								<span
									class="ml-auto inline-flex items-baseline gap-2 text-[12px] whitespace-nowrap tabular-nums"
								>
									{#if estimates.length === 1}<span
											aria-label="Estimated time remaining"
											class="text-dim">{estimates[0]}</span
										>{/if}
									{#if !listing}
										{#if estimates.length === 1}<span aria-hidden="true" class="text-faint">·</span
											>{/if}
										<span class="font-medium text-accent"
											><Count value={Math.floor(completion * 100)} />%</span
										>
									{/if}
								</span>
							</div>
							{#if !listing}<Bar
									class="mt-3 !h-1.5"
									track="bg-raised"
									fill={completion}
									glide={rewriting}
									now={completedCount}
									max={totalCount}
									text={`${completedCount} of ${totalCount} files processed across active runs`}
								/>{/if}
						{/if}
						{#if listing}<p class="mt-2 text-[12px] text-dim">
								Discovering files{totalCount ? ' · totals may grow' : ''}…
								{#if !totalCount && estimates.length === 1}<span
										aria-label="Estimated time remaining"
										class="ml-2 whitespace-nowrap tabular-nums">{estimates[0]}</span
									>{/if}
							</p>{/if}
					</div>
				</Reveal>
				<Reveal when={!!activeFiles.length || !!waiting} class={activity.runs.length ? 'pt-5' : ''}>
					<div class={waiting && activeFiles.length ? 'grid gap-6 lg:grid-cols-2 lg:gap-8' : ''}>
						{#if activeFiles.length}
							<!-- A grid cell keeps its content's width by default, and row names do
							     not wrap. -->
							<div class="min-w-0">
								<div class="flex flex-wrap items-baseline justify-between gap-2">
									<h3 class="text-[13px] font-semibold">
										Processing <span class="ml-1 text-faint tabular-nums">{activeFiles.length}</span
										>
									</h3>
									{#if slots}
										<button
											bind:this={slotsButton}
											onclick={() =>
												slotsButton && slotsPanel && capacity.toggle(slotsButton, slotsPanel)}
											aria-expanded={capacity.open}
											aria-controls="rewrite-threads"
											class="-my-1.5 -mr-1.5 rounded px-1.5 py-1.5 text-[11.5px] text-faint tabular-nums hover:text-fg"
										>
											{slots} rewrite {slots === 1 ? 'thread' : 'threads'}
										</button>
										<div
											bind:this={slotsPanel}
											id="rewrite-threads"
											popover="manual"
											class={`fixed m-0 w-72 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-3.5 text-fg shadow-lg`}
										>
											<h4 class="text-[13px] font-semibold">Rewrite threads</h4>
											<p class="mt-1.5 text-[12.5px] leading-relaxed text-dim">
												How many files can be rewritten in parallel. Strains the CPU and disk.
											</p>
											{#if admin}
												<a
													href={resolve('/settings')}
													class="mt-2.5 inline-flex min-h-8 items-center text-[12px] font-medium text-accent underline underline-offset-2 hover:text-fg"
												>
													Change it in settings
												</a>
											{/if}
										</div>
									{/if}
								</div>
								<div class="mt-3">
									{@render fileList(activeFiles)}
								</div>
							</div>
						{/if}
						{#if waiting}
							<div class="min-w-0">
								<div class="flex flex-wrap items-baseline justify-between gap-2">
									<h3 class="text-[13px] font-semibold">
										Waiting <span class="ml-1 text-faint tabular-nums"
											>{waiting.toLocaleString()}</span
										>
									</h3>
									<!-- The target comes from `after`, or its height would set the row's. -->
									<button
										class="relative -my-1.5 -mr-1.5 rounded px-1.5 py-1.5 text-[12px] font-medium text-accent after:absolute after:-inset-2 after:content-[''] hover:underline"
										onclick={() => (queueOpen = true)}>View queue</button
									>
								</div>
								<div class="mt-3">{@render fileList(shownQueue)}</div>
							</div>
						{/if}
					</div>
				</Reveal>
			</div>
		</Reveal>
	</div>

	<!-- Always mounted, like the confirmation below. -->
	<div
		bind:this={schedulePanel}
		id="next-check"
		popover="manual"
		class={`fixed m-0 w-max max-w-[min(22rem,calc(100vw-1.5rem))] rounded-xl border border-line-strong ${frosted} p-3.5 text-fg shadow-lg`}
	>
		<p class="text-[12.5px] leading-relaxed text-dim">{schedule}</p>
		{#if admin}
			<a
				href={resolve('/settings/sweep')}
				class="mt-2.5 inline-flex min-h-8 items-center text-[12px] font-medium text-accent underline underline-offset-2 hover:text-fg"
			>
				{activity.next_sweep ? 'Change the schedule' : 'Schedule one'}
			</a>
		{/if}
	</div>

	<!-- Always mounted. A popover mounted on the press has nothing to anchor to. -->
	<div
		bind:this={stopPanel}
		id="stop-confirmation"
		popover="manual"
		role="alertdialog"
		aria-labelledby="stop-title"
		aria-describedby="stop-description"
		class={`fixed m-0 w-72 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-3.5 text-fg shadow-lg`}
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
	<!-- Explicit pauses apply to titles and future work. A disclosure rather than
	     a `<details>`, which has no way to open on the blind the rows use. -->
	<Reveal when={!!pausedItems.length} class="pt-2">
		<div class="rounded-2xl border border-line bg-sunken px-3 text-[12.5px] sm:px-4">
			<Disclosure
				id="paused-files"
				open={pausedOpen}
				ontoggle={() => (pausedOpen = !pausedOpen)}
				mark={10}
				class="group flex min-h-12 w-full items-center gap-2 text-left text-dim hover:text-fg"
				panelClass=""
			>
				{#snippet summary(chevron)}
					{@render chevron()}
					<span>Paused <span class="text-faint tabular-nums">{pausedItems.length}</span></span>
				{/snippet}
				{#snippet panel()}
					<ul data-rows class="relative flex flex-col gap-2 pb-3">
						{#each pausedItems as pause (pause.path)}
							<li animate:flip={rowSlide()} in:fade={rowArrive()} out:rowLeave>
								<PausedFile
									{pause}
									cover={activity.covers?.[pause.path]}
									plan={activity.plans?.[pause.path]}
									current={activity.plans_current ?? true}
									onopen={() => titleDetails.show(pause.path, activity.covers?.[pause.path])}
									{admin}
									disabled={!!busy}
									resuming={busy === `resume-${pause.path}`}
									onresume={() => release(pause)}
								/>
							</li>
						{/each}
					</ul>
				{/snippet}
			</Disclosure>
		</div>
	</Reveal>
	<FileTitle bind:this={titleDetails} {runner} />
</section>

{#if queueOpen}<QueueDialog
		{admin}
		{runner}
		onclose={() => (queueOpen = false)}
		onchanged={() => {
			void snapshot.look();
			onpressed();
		}}
	/>{/if}
