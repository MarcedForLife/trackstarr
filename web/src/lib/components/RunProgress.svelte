<script lang="ts">
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { dangerInline } from '$lib/controls';
	import { reduced } from '$lib/motion.svelte';
	import {
		fileBar,
		fileFraction,
		fileStatus,
		fileWorking,
		fraction,
		measured,
		percent,
		progressLabel,
		titled,
		type Run
	} from '$lib/runs';

	// Compact progress and controls for the library bar and title sheet.
	let {
		run,
		stopping = false,
		noun = 'the selected titles',
		onstop
	}: {
		run: Run;
		// A stop already sent, which the next poll has yet to confirm.
		stopping?: boolean;
		// The name before the first snapshot lands.
		noun?: string;
		onstop?: () => void;
	} = $props();

	// The file's bar glides between snapshots from ffmpeg's last reading.
	$effect(ticking);
	const age = $derived(since(run.seen));

	const progress = $derived.by(() => {
		if (run.stopping) return 'Stopping after the current file…';
		const far = percent(run);
		return far ? `${far} · ${progressLabel(run)}` : progressLabel(run);
	});

	// The longest-held file, which is the whole readout for a run of one.
	const file = $derived(run.active[0]);
	const status = $derived(file ? fileStatus(file, age) : '');
	// Planning is too fast to measure, so the bar's place says it is happening.
	const crossing = $derived(!!file && fileWorking(file) && !reduced());
</script>

<div class="flex items-center gap-2.5">
	<p class="min-w-0 flex-1 truncate text-[13px] font-medium">
		{run.dry_run ? 'Planning' : 'Processing'}
		{run.label || noun}
	</p>
	{#if onstop}
		<!-- The pressed area runs past the box, which is short to sit level with
		     the line beside it. -->
		<button
			onclick={onstop}
			disabled={stopping || run.stopping}
			class={`${dangerInline} relative flex-none !px-2.5 after:absolute after:-inset-2 after:content-[''] disabled:opacity-(--disabled)`}
		>
			<Glyph name="stop" size={9} />
			{run.stopping ? 'Stopping' : 'Stop'}
		</button>
	{/if}
</div>

<!-- A one-file run counts nothing worth a line; the file below carries it. -->
{#if progress}
	<p class="mt-1 text-[12.5px] text-dim">{progress}</p>
{/if}

<!-- No bar until there is a total worth measuring against. -->
{#if measured(run)}
	<Bar class="mt-2.5" fill={fraction(run)} now={run.done} max={run.total} text={progress} />
{/if}

<!-- The file, only while there is something to say. Keyed on the path, so a
     new file gets a fresh bar rather than a glide from the old fraction. -->
{#if file && !run.stopping && (fileBar(file) || crossing || status)}
	{#key file.path}
		<div class="mt-2.5">
			<!-- Whole, unlike a file list's rows: one file with the width to say it. -->
			<p class="truncate text-[11.5px] text-faint" title={file.path}>{titled(file.path)}</p>
			<div class="mt-1.5 flex items-center gap-2.5">
				{#if fileBar(file)}
					<Bar
						file
						fill={fileFraction(file, age)}
						now={Math.floor(fileFraction(file, age) * 100)}
						max={100}
						text={`${titled(file.path)}: ${status}`}
					/>
				{:else if crossing}
					<Bar file indeterminate text={`${titled(file.path)}: working`} />
				{/if}
				<!-- Left out rather than left empty, which would still cost the gap
				     beside it. -->
				{#if status}<span class="flex-none text-[11px] text-faint tabular-nums">{status}</span>{/if}
			</div>
		</div>
	{/key}
{/if}
