<script lang="ts">
	import Bar from '$lib/components/Bar.svelte';
	import { reduced } from '$lib/motion.svelte';
	import {
		fileBar,
		fileFraction,
		fileReadout,
		fileStatus,
		fileWorking,
		titled,
		type ActiveFile
	} from '$lib/runs';

	// One held file's bar and readout. The caller ages the reading, since it owns
	// the snapshot it came from.
	let {
		file,
		age = 0,
		// The line above the bar, or empty where the page has already named it.
		caption,
		class: extra = ''
	}: { file: ActiveFile; age?: number; caption?: string; class?: string } = $props();

	const name = $derived(titled(file.path));
	const line = $derived(caption ?? name);
	const status = $derived(fileStatus(file, age));
	const bar = $derived(fileBar(file));
	const far = $derived(bar ? fileFraction(file, age) : 0);
	const readout = $derived(bar ? fileReadout(file, age) : null);
	// Planning is too fast to measure, so the bar's place says it is happening.
	const crossing = $derived(fileWorking(file) && !reduced());
</script>

{#if bar || crossing || status}
	<div class={extra}>
		<!-- Whole, unlike a file list's rows: one file with the width to say it. -->
		{#if line}
			<p class="truncate text-[11.5px] text-faint" title={file.path}>{line}</p>
		{/if}
		<div class={`flex flex-wrap items-center gap-x-2.5 gap-y-1.5 ${line ? 'mt-1.5' : ''}`}>
			<!-- One bar for both, so the segment gives way to the fill inside it. An
			     encode's takes the row, with its numbers under it as a run's file row
			     lays them out. -->
			{#if bar || crossing}
				<Bar
					file
					indeterminate={!bar}
					class={bar ? 'basis-full' : ''}
					fill={far}
					now={Math.floor(far * 100)}
					max={100}
					text={`${name}: ${bar ? status : 'working'}`}
				/>
			{/if}
			{#if readout}
				<!-- How fast under the bar's start, when it ends and how far under its
				     end, so the steady number holds the same edge as the run's. -->
				{#if readout.speed}<span class="text-[11px] text-faint tabular-nums">{readout.speed}</span
					>{/if}
				<span class="ml-auto text-[11px] text-faint tabular-nums"
					>{[readout.left, readout.far].filter(Boolean).join(' · ')}</span
				>
			{:else if status}
				<!-- Left out rather than left empty, which would still cost the gap
				     beside it. -->
				<span class="flex-none text-[11px] text-faint tabular-nums">{status}</span>
			{/if}
		</div>
	</div>
{/if}
