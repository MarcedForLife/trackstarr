<script lang="ts">
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import RunFile from '$lib/components/RunFile.svelte';
	import { stamp } from '$lib/format';
	import {
		duration,
		fileRows,
		fraction,
		measured,
		percent,
		progressLabel,
		remaining,
		source,
		verdicts,
		type Run
	} from '$lib/runs';

	// One run: what it is, how far it has got, and each worker's file. A row in
	// the overview's panel, so no border of its own.
	let {
		run,
		paused = false,
		stoppable = false,
		busy = false,
		ended = false,
		cut = false,
		onstop,
		ondismiss
	}: {
		run: Run;
		paused?: boolean;
		// An admin's, while the run is not already winding down.
		stoppable?: boolean;
		busy?: boolean;
		// Over and held on the page to be read: a receipt with nothing to press.
		ended?: boolean;
		// Ended because somebody stopped it, so its counts are part of the job.
		cut?: boolean;
		onstop?: (run: Run) => void;
		// Take an ended row off the page now.
		ondismiss?: () => void;
	} = $props();

	// What the run is, plus "plan only" in the switch's own word. An import
	// never claims it: the verdicts say what became of the file.
	const label = $derived(
		run.kind !== 'import' && run.dry_run ? `${source(run)} · plan only` : source(run)
	);

	// The clocks and bars are drawn from the last snapshot plus its age, so they
	// move between snapshots. A receipt has stopped.
	$effect(() => {
		if (ended) return;
		return ticking();
	});
	const age = $derived(ended ? 0 : since(run.seen));

	/** What the run is doing, or what it came to. */
	const progress = $derived.by(() => {
		// A stopped run counted part of the job and must say so; a finished one
		// has nothing to compare against.
		if (ended) {
			if (cut)
				return `Stopped after ${run.done.toLocaleString()} of ${run.total.toLocaleString()} ${
					run.total === 1 ? 'file' : 'files'
				}`;
			return `Finished · ${run.done.toLocaleString()} ${run.done === 1 ? 'file' : 'files'}`;
		}
		if (run.stopping) return 'Stopping after the current file…';
		const far = percent(run);
		return far ? `${far} · ${progressLabel(run)}` : progressLabel(run);
	});

	// A held run looks the same working or asleep, so it says which.
	const held = $derived(
		!paused || run.stopping
			? ''
			: run.active.length
				? 'Paused, finishing the current file'
				: 'Paused, nothing running'
	);

	// Why the run is not moving, or how long it has left. One slot.
	const aside = $derived(ended ? '' : held || remaining(run, paused, age));

	const started = $derived(`Started ${stamp(run.started)}`);

	// What it is working on, then what it has just finished with.
	const rows = $derived(fileRows(run));

	// How many rows show before asking for the rest: at minutes per file, six is
	// most of an hour.
	const SHOWN = 6;
	let all = $state(false);
	const showing = $derived(all ? rows : rows.slice(0, SHOWN));

	// Which rows are open, by path, since positions move with every snapshot.
	let opened = $state<Record<string, boolean>>({});
</script>

<div>
	<div class="flex items-baseline gap-2.5">
		<span class="min-w-0 flex-1 truncate text-[13.5px] font-medium">{label}</span>
		<time datetime={run.started} title={started} class="flex-none font-mono text-[11px] text-faint">
			{duration(run.seconds + age)}
		</time>
		{#if ended}
			<!-- Where Stop was: the button becomes the word for what happened. -->
			<span
				class={`flex-none rounded-md border px-2 py-1 text-[11.5px] leading-none font-medium ${
					cut ? 'border-line-strong text-dim' : 'border-ok/40 text-ok'
				}`}
			>
				{cut ? 'Stopped' : 'Finished'}
			</span>
			{#if ondismiss}
				<!-- For somebody done with the row sooner. The ::after is the tap
				     target. -->
				<button
					onclick={ondismiss}
					aria-label={`Dismiss ${label.toLowerCase()}`}
					title="Dismiss"
					class="relative -my-1 flex-none rounded-md px-1 py-1 text-[13px] leading-none text-faint transition-colors after:absolute after:-inset-3 after:content-[''] hover:text-fg"
				>
					×
				</button>
			{/if}
		{:else if stoppable}
			<!-- A bordered pill, so the one control here does not read as label. The
			     ::after is the 47px tap target around a 23px pill. -->
			<button
				onclick={() => onstop?.(run)}
				disabled={busy}
				aria-label={`Stop ${label.toLowerCase()}`}
				class="relative -my-1 flex-none rounded-md border border-line-strong px-2 py-1 text-[11.5px] leading-none font-medium text-dim transition-colors after:absolute after:-inset-3 after:content-[''] hover:border-danger/45 hover:text-danger active:bg-danger/10 disabled:opacity-50"
			>
				<span class="inline-flex items-center gap-1.5">
					<Glyph name="stop" size={9} />
					Stop
				</span>
			</button>
		{/if}
	</div>

	<!-- Nothing for a one-file run, which is its name, its time and its file. -->
	{#if progress || aside}
		<div class="mt-1 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[12.5px]">
			<span class="min-w-0 flex-1 text-dim">{progress}</span>
			<!-- Faint even for paused: a chosen pause is not trouble. Its own line
			     on a phone, or the count breaks mid-phrase. -->
			{#if aside}
				<span class="w-full flex-none text-faint sm:w-auto">{aside}</span>
			{/if}
		</div>
	{/if}

	<!-- No bar until there is a total worth measuring against: empty reads as
	     stalled. A finished run fills it; a stopped one keeps its fraction. -->
	{#if measured(run)}
		<Bar
			class="mt-2.5"
			fill={ended && !cut ? 1 : fraction(run)}
			now={run.done}
			max={run.total}
			text={progress}
			done={ended && !cut}
		/>
	{/if}

	{#if verdicts(run).length}
		<!-- A tally in the library's dots and words, with no pill: nothing to
		     press. -->
		<ul class="mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-[12px]">
			{#each verdicts(run) as verdict (verdict.state)}
				<li class="flex items-center gap-1.5" title={verdict.hint}>
					<span aria-hidden="true" class={`h-1.5 w-1.5 flex-none rounded-full ${verdict.dot}`}
					></span>
					<span class={verdict.trouble ? 'font-medium text-danger' : 'text-dim'}>
						{verdict.label}
					</span>
					<span class="text-faint tabular-nums">{verdict.count.toLocaleString()}</span>
				</li>
			{/each}
		</ul>
	{/if}

	<!-- Working and finished files in one list, so a verdict lands where the
	     name already was. -->
	{#if rows.length}
		<ul class="mt-3 flex flex-col gap-2 border-t border-line pt-2.5">
			{#each showing as row (row.path)}
				<RunFile
					run={run.id}
					{row}
					{age}
					id={`file-${run.id}-${row.path}`}
					open={!!opened[row.path]}
					ontoggle={() => (opened = { ...opened, [row.path]: !opened[row.path] })}
				/>
			{/each}
		</ul>
		{#if rows.length > SHOWN}
			<button
				onclick={() => (all = !all)}
				aria-expanded={all}
				class="mt-2 text-[12px] font-medium text-dim hover:text-fg"
			>
				{all ? 'Show fewer' : `Show ${rows.length - SHOWN} more`}
			</button>
		{/if}
	{/if}
</div>
