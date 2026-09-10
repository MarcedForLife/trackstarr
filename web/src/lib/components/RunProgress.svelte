<script lang="ts">
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import {
		fileBar,
		fileFraction,
		fileStatus,
		fraction,
		measured,
		percent,
		progressLabel,
		titled,
		type Run
	} from '$lib/runs';

	// A run in three lines: what it is doing, how far, and the way out. The
	// compact RunCard for the library's bar and a title's sheet, so the same run
	// reads one way.
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
</script>

<div class="flex items-baseline gap-2.5">
	<p class="min-w-0 flex-1 truncate text-[13px] font-medium">
		{run.dry_run ? 'Planning' : 'Processing'}
		{run.label || noun}
	</p>
	{#if onstop}
		<button
			onclick={onstop}
			disabled={stopping || run.stopping}
			class="relative -my-1 flex-none rounded-md border border-line-strong px-2 py-1 text-[11.5px] leading-none font-medium text-dim transition-colors after:absolute after:-inset-2.5 after:content-[''] hover:border-danger/45 hover:text-danger active:bg-danger/10 disabled:opacity-(--disabled)"
		>
			<span class="inline-flex items-center gap-1.5">
				<Glyph name="stop" size={9} />
				{run.stopping ? 'Stopping' : 'Stop'}
			</span>
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
{#if file && !run.stopping && (fileBar(file) || status)}
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
				{/if}
				<span class="flex-none text-[11px] text-faint tabular-nums">{status}</span>
			</div>
		</div>
	{/key}
{/if}
