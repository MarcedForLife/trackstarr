<script lang="ts">
	import { onDestroy } from 'svelte';
	import { resolve } from '$app/paths';
	import { fade } from 'svelte/transition';
	import Glyph from '$lib/components/Glyph.svelte';
	import RunButtons from '$lib/components/RunButtons.svelte';
	import RunCard from '$lib/components/RunCard.svelte';
	import { refusalText } from '$lib/api';
	import { button, danger, glyph, primary } from '$lib/controls';
	import { ago, headline, type Event } from '$lib/events';
	import { size, soon, stamp, titled } from '$lib/format';
	import { lift, type Hold } from '$lib/holds';
	import type { RunMode } from '$lib/library';
	import { keyboard } from '$lib/modal';
	import { poll } from '$lib/poll';
	import {
		doing,
		duration,
		getActivity,
		pause,
		resume,
		skipFile,
		startSweep,
		stopEverything,
		stopRun,
		tally,
		type Activity,
		type Run
	} from '$lib/runs';
	import { told } from '$lib/stream';

	// What the service is doing, as a transport bar: the state in a word, the
	// controls, the runs underneath, and what it last did along the bottom. Owns
	// the live half of the overview: the snapshot, its poll, the buttons and the
	// rows of runs that just ended. The page holds the history and hands in the
	// last sweep summary and an ended run's lines.
	let {
		seed,
		admin = false,
		recent,
		swept,
		onmoved,
		onpressed
	}: {
		/** The snapshot the loader read; the poll carries on from it. */
		seed: Activity;
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

	// Seeded from the loader, written over by the poll.
	let activity = $derived(seed);
	// Kept apart: a refusal is a sentence to read, a lost connection is a
	// condition the next poll clears. In one string the poll wiped the refusal.
	let refusal = $state('');
	let offline = $state('');
	let busy = $state('');
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
	// log under it; each row has a dismiss.
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

	// What Stop all would cost right now, for the confirm.
	const stopWould = $derived(
		rewriting
			? `Every run stops after the current file, and ${
					activity.rewrites === 1 ? 'the rewrite' : `all ${activity.rewrites} rewrites`
				} under way ${activity.rewrites === 1 ? 'is' : 'are'} killed mid-encode. The library files are untouched.`
			: 'Every run stops after the current file. Nothing is being rewritten, so nothing is lost.'
	);

	// A walk holding the sweep cache; the service refuses a second. A re-check
	// counts.
	const sweeping = $derived(
		activity.runs.some((run) => run.kind === 'sweep' || run.kind === 'recheck')
	);

	// Stop all is offered once there are two runs; one run has its own Stop.
	const stoppable = $derived(activity.runs.length > 1);

	// Whether Pause says its name: a wordless circle alone on an idle phone row,
	// a labelled button beside anything else labelled.
	const named = $derived(sweeping || stoppable);

	// The line under the state: who paused and when, or the next sweep.
	const sub = $derived.by(() => {
		if (holding) {
			const who = activity.paused_by ? ` by ${activity.paused_by}` : '';
			const since = activity.paused_at ? ` ${ago(activity.paused_at)}` : '';
			const lead = who || since ? `${who}${since}`.trim() : '';
			const opener = lead ? lead[0].toUpperCase() + lead.slice(1) + '. ' : '';
			return `${opener}Nothing new starts until resumed, even after a restart.`;
		}
		return activity.next_sweep ? `Next sweep ${soon(activity.next_sweep)}.` : '';
	});

	// The fallback poll rate, and the pace the stream's messages are held to. A
	// run is a readout being watched; an idle service is a page left on a desk.
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
		return told(activity.runs.length || Date.now() < expecting ? BUSY_MS : IDLE_MS);
	}

	function dismiss(run: string) {
		finished = finished.filter((entry) => entry.run.id !== run);
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
	async function ask() {
		// By id, not count: one run ending as another starts leaves the count
		// unchanged.
		const before = activity.runs;
		// Whether the last look failed, so a missing run may have been cut off
		// rather than finished.
		const blind = !!offline;
		try {
			const fresh = await getActivity();
			// A new stamp is a restart, which no run survives. Both polls can
			// succeed either side of one, which is the half `blind` cannot see.
			const restarted = !!activity.up_since && fresh.up_since !== activity.up_since;
			activity = fresh;
			offline = '';
			const alive = new Set(activity.runs.map((entry) => entry.id));
			const gone = before.filter((entry) => !alive.has(entry.id));
			if (gone.length) {
				// A run that left on its own finished, and its row is a receipt. One
				// that vanished across an outage or restart did not.
				if (!blind && !restarted) {
					// Newest first.
					finished = [
						...gone.map((run) => ({ run, at: Date.now(), cut: run.stopping })),
						...finished
					];
				}
				// The run has just written its summary.
				onmoved(true);
			} else {
				// A delivery can start and finish between two polls without ever
				// showing here.
				onmoved(false);
			}
		} catch {
			offline = 'Could not reach the service.';
		}
	}

	// A run beginning or ending is worth asking on at once; `progress` arrives as
	// fast as a sweep books files, and the gap holds it to a readable rate.
	const runs = poll({ ask, pace, gap: BUSY_MS, kinds: ['runs', 'progress'] });

	onDestroy(runs.stop);

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
			activity = 'runs' in told ? (answer as Activity) : await getActivity();
		} catch (error) {
			refusal = refusalText(error);
		} finally {
			busy = '';
			// The panel is now this moment's, and a just-started sweep polls fast
			// from here.
			runs.mark();
			// Pausing and resuming are history events.
			onpressed();
		}
	}

	function stop(entry: Run) {
		act(`stop-${entry.id}`, () => stopRun(entry.id));
	}

	// One file off one run. No confirm: nothing is lost but the encode, and the
	// next sweep still reaches the file.
	function skip(entry: Run, path: string) {
		act(`skip-${path}`, () => skipFile(entry.id, path));
	}

	// What is being left alone for now, and how long is left of each.
	const held = $derived(activity.holds ?? []);

	function until(hold: Hold): string {
		return hold.seconds ? `${duration(hold.seconds)} left` : 'until lifted';
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

	// A verdict tally in the library's dots and words, with nothing to press.
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
			<span aria-hidden="true" class={`h-1.5 w-1.5 flex-none rounded-full ${verdict.dot}`}></span>
			<span class={verdict.trouble ? 'font-medium text-danger' : 'text-dim'}>{verdict.label}</span>
			<span class="text-faint tabular-nums">{verdict.count.toLocaleString()}</span>
		</li>
	{/each}
{/snippet}

<section
	aria-labelledby="now"
	class={`mt-6 rounded-xl border bg-raised ${holding ? 'border-danger/40' : 'border-line'}`}
>
	<div class="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3.5">
		<div class="min-w-0 flex-1">
			<h2 id="now" class="flex items-center gap-2 text-[15px] font-semibold">
				{#if holding}
					<span aria-hidden="true" class="h-2 w-2 flex-none rounded-full bg-danger"></span>
				{/if}
				{doing(activity)}
			</h2>
			{#if sub}
				<p class="mt-0.5 text-[12.5px] text-dim">{sub}</p>
			{:else if !activity.runs.length}
				<p class="mt-0.5 text-[12.5px] text-dim">
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

		{#if admin}
			<!-- Under the state on a phone, beside it from sm up. Pause and Resume
			     swap in one place. The sweep controls go while a sweep is walking,
			     since the service would refuse a second. On a phone every worded
			     control grows to a share of the row, and the row wraps; Plan and
			     Process wrap together as one control. -->
			<div class="flex w-full flex-wrap items-center gap-3 sm:w-auto sm:flex-nowrap">
				{#if holding}
					<button
						onclick={() => act('resume', resume)}
						disabled={!!busy}
						class={`flex-1 sm:flex-none ${primary}`}
					>
						<Glyph name="play" />
						{busy === 'resume' ? 'Resuming…' : 'Resume'}
					</button>
				{:else}
					<button
						onclick={() => act('pause', pause)}
						disabled={!!busy}
						aria-label="Pause"
						title="Pause. Files already started finish; nothing new starts."
						class={named ? `flex-1 sm:flex-none ${button}` : `flex-none ${glyph}`}
					>
						<Glyph name="pause" />
						<!-- One condition for the word, the corner and the growing. -->
						<span class={named ? '' : 'hidden sm:inline'}>
							{busy === 'pause' ? 'Pausing…' : 'Pause'}
						</span>
					</button>
					{#if stoppable}
						<button
							onclick={() => (raisedHere(), (armed = arming === 'abort' ? '' : 'abort'))}
							disabled={!!busy}
							aria-expanded={arming === 'abort'}
							class={`flex-1 sm:flex-none ${arming === 'abort' ? danger : button}`}
						>
							<Glyph name="stop" size={9} />
							{busy === 'abort' ? 'Stopping…' : 'Stop all'}
						</button>
					{/if}
					{#if !sweeping}
						<!-- Dead while the confirm is up, so there is one way to say yes. -->
						<RunButtons
							fill
							mayRewrite={activity.may_rewrite}
							disabled={!!busy || arming === 'rewrite'}
							busy={starting}
							onrun={pressStart}
						/>
					{/if}
				{/if}
			</div>
		{/if}
	</div>

	{#if arming === 'rewrite'}
		{@render confirm(
			'Sweeps the library and applies the rules. Files are rewritten on disk with no undo.',
			'Process',
			sweepNow
		)}
	{:else if arming === 'abort'}
		{@render confirm(stopWould, 'Stop all', abortNow)}
	{/if}

	<!-- The service's own words, kept until the next button is pressed. -->
	{#if refusal}
		<p role="alert" class="border-t border-line px-4 py-2.5 text-[12.5px] text-danger">
			{refusal}
		</p>
	{:else if offline}
		<p role="status" class="border-t border-line px-4 py-2.5 text-[12.5px] text-danger">
			{offline}
		</p>
	{/if}

	{#if showing.length}
		<ol class="divide-y divide-line border-t border-line">
			{#each showing as entry (entry.run.id)}
				<!-- Ended rows fade rather than blink out. -->
				<li out:fade={{ duration: 200 }} class="px-4 py-3.5">
					<RunCard
						run={entry.run}
						paused={activity.paused}
						stoppable={admin && !entry.ended && !entry.run.stopping}
						busy={!!busy}
						ended={entry.ended}
						cut={entry.cut}
						onstop={stop}
						onskip={admin ? skip : undefined}
						ondismiss={entry.ended ? () => dismiss(entry.run.id) : undefined}
					/>
				</li>
			{/each}
		</ol>
	{/if}

	<!-- The last sweep's tally, unless one is running now. Held titles and parked
	     files sit here too: neither waiting nor working, and only news when not
	     zero. -->
	{#if (swept && !sweeping) || activity.parked || held.length}
		<div class="flex flex-col gap-1.5 border-t border-line px-4 py-3 text-[12.5px]">
			{#if swept && !sweeping}
				<ul class={tallyList}>
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
			{#if held.length}
				<!-- Named, not counted: the point of a hold is knowing which title it
				     is on, and it is the only thing here with a control. -->
				<ul class="flex flex-col gap-1">
					{#each held as hold (hold.path)}
						<li class="flex items-baseline gap-2">
							<span class="min-w-0 flex-1 truncate text-dim" title={hold.reason}>
								Holding {hold.name || titled(hold.path)}
							</span>
							<span class="flex-none text-faint">{until(hold)}</span>
							{#if admin}
								<!-- The ::after is the tap target around a small pill. -->
								<button
									onclick={() => release(hold)}
									disabled={!!busy}
									class="relative flex-none rounded border border-line-strong px-1.5 py-0.5 text-[10.5px] leading-none font-medium text-faint transition-colors after:absolute after:-inset-3 after:content-[''] hover:text-fg disabled:opacity-50"
								>
									{busy === `lift-${hold.path}` ? 'Lifting…' : 'Lift'}
								</button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if activity.parked}
				<p class="text-faint">
					{activity.parked.toLocaleString()}
					{activity.parked === 1 ? 'file' : 'files'} parked, waiting on a download client that still has
					them hard-linked.
				</p>
			{/if}
		</div>
	{/if}
</section>
