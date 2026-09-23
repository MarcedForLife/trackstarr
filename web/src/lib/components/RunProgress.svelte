<script lang="ts">
	import { since, ticking } from '$lib/clock.svelte';
	import Bar from '$lib/components/Bar.svelte';
	import Count from '$lib/components/Count.svelte';
	import FileProgress from '$lib/components/FileProgress.svelte';
	import Spinner from '$lib/components/Spinner.svelte';
	import { button } from '$lib/controls';
	import { fraction, measured, percent, progressLabel, type Run } from '$lib/runs';

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

	const far = $derived(percent(run));
	const progress = $derived.by(() => {
		if (run.stopping) return 'Stopping after the current file…';
		const label = progressLabel(run);
		return far === undefined ? label : `${far}% · ${label}`;
	});
	// The line with numbers in it, which are set as their own elements so a
	// change rolls. The string above still tells the bar's screen reader.
	const counted = $derived(!run.stopping && measured(run));

	// The longest-held file, which is the whole readout for a run of one.
	const file = $derived(run.active[0]);
</script>

<div class="flex items-center gap-2.5">
	<p class="min-w-0 flex-1 truncate text-[13px] font-medium">
		{run.dry_run ? 'Planning' : 'Processing'}
		{run.label || noun}
	</p>
	{#if onstop}
		<button
			onclick={onstop}
			disabled={stopping || run.stopping}
			aria-busy={stopping || run.stopping}
			class={`${button} flex-none self-stretch text-danger`}
		>
			<Spinner glyph="stop" size={9} busy={stopping || run.stopping} />
			{stopping || run.stopping ? 'Stopping…' : 'Stop'}
		</button>
	{/if}
</div>

<!-- A one-file run counts nothing worth a line; the file below carries it. -->
{#if progress}
	<p class="mt-1 text-[12.5px] text-dim">
		{#if counted}
			{#if far !== undefined}<Count value={far} />% ·{/if}
			<Count value={run.done} /> of <Count value={run.total} /> files
		{:else}{progress}{/if}
	</p>
{/if}

<!-- No bar until there is a total worth measuring against. -->
{#if measured(run)}
	<Bar class="mt-2.5" fill={fraction(run)} now={run.done} max={run.total} text={progress} />
{/if}

<!-- The file, only while there is something to say. Keyed on the path, so a
     new file gets a fresh bar rather than a glide from the old fraction. -->
{#if file && !run.stopping}
	{#key file.path}
		<FileProgress {file} {age} class="mt-2.5" />
	{/key}
{/if}
