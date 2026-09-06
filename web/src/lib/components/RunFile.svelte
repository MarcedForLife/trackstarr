<script lang="ts">
	import Bar from '$lib/components/Bar.svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import { pip, verdictHint, verdictLabel } from '$lib/library';
	import {
		duration,
		fileBar,
		fileFraction,
		fileStatus,
		getRunLog,
		named,
		titled,
		type FileRow
	} from '$lib/runs';

	// One file under a run: what it is, how long, and what it came to, opening
	// to the full path and its worker's log. The same row live or released.
	let {
		run,
		row,
		age = 0,
		open = false,
		id,
		ontoggle
	}: {
		// The run id, which is half of what names a file's log.
		run: string;
		row: FileRow;
		// Seconds since the snapshot arrived, to carry a live row's readouts
		// forward. A finished row ignores it.
		age?: number;
		open?: boolean;
		// Ties the button to the panel it opens; unique within the page.
		id: string;
		ontoggle: () => void;
	} = $props();

	const live = $derived(!!row.live);
	const shown = $derived(named(row.path));
	const status = $derived(row.live ? fileStatus(row.live, age) : '');
	const bar = $derived(!!row.live && fileBar(row.live));

	// A live row's clock runs; a finished one's is how long the worker had it.
	const held = $derived(row.live ? row.seconds + age : row.seconds);

	// The verdict in the library's words, or nothing while a thread has the file.
	const verdict = $derived(row.verdict ? verdictLabel(row.verdict) : '');
	const dot = $derived(live ? 'bg-accent' : (pip[row.verdict] ?? 'bg-line-strong'));

	// The line under the name: how far through, or the detail.
	const said = $derived(live ? status : row.detail);

	let lines = $state<string[] | null>(null);
	let failure = $state('');

	// Plain, not $state: the effect below reads and writes them, and a reactive
	// read would re-run it per fetch.
	let read = false;
	let readWhileWorking = false;

	// Runs on every poll while open. A live file is re-read each time; a released
	// one once more, for the last lines.
	$effect(() => {
		if (!open) {
			read = readWhileWorking = false;
			return;
		}
		const working = !!row.live;
		if (!working && read && !readWhileWorking) return;
		read = true;
		readWhileWorking = working;
		let stopped = false;
		getRunLog(run, row.path)
			.then((found) => {
				if (stopped) return;
				lines = found;
				failure = '';
			})
			.catch(() => {
				if (!stopped) failure = 'Could not read this file’s log.';
			});
		return () => {
			stopped = true;
		};
	});

	// Newest at the bottom, as a log reads, so an open row scrolls to its end.
	let tail: HTMLDivElement | undefined = $state();

	$effect(() => {
		void lines;
		if (tail) tail.scrollTop = tail.scrollHeight;
	});
</script>

<li class="text-[12px]">
	<Disclosure {id} {open} {ontoggle} mark={10} panelClass="mt-2 ml-3.5">
		{#snippet summary(chevron)}
			<span class="flex items-baseline gap-2">
				<span
					aria-hidden="true"
					title={row.verdict ? verdictHint(row.verdict) : ''}
					class={`h-1.5 w-1.5 flex-none translate-y-[-1px] rounded-full ${dot}`}
				></span>
				<!-- Named as the history names it, with the episode outside the
				     truncation: rows of one series differ only in it. -->
				<span class="flex min-w-0 flex-1 items-baseline gap-1.5 text-dim">
					<span class="min-w-0 truncate">{shown.name}</span>
					{#if shown.episode}
						<span class="flex-none text-faint tabular-nums">{shown.episode}</span>
					{/if}
				</span>
				{#if verdict}
					<span
						class={`flex-none font-medium ${row.verdict === 'failed' ? 'text-danger' : 'text-faint'}`}
					>
						{verdict}
					</span>
				{/if}
				<!-- How long the thread had the file, slot wait included. -->
				<span class="flex-none font-mono text-[11px] text-faint">{duration(held)}</span>
				{@render chevron()}
			</span>
		{/snippet}

		<!-- Only for a rewrite; a probe is over inside a second. -->
		{#snippet aside()}
			{#if bar || said}
				<div class="mt-1.5 ml-3.5 flex items-center gap-2.5">
					{#if bar && row.live}
						<Bar
							file
							fill={fileFraction(row.live, age)}
							now={Math.floor(fileFraction(row.live, age) * 100)}
							max={100}
							text={`${titled(row.path)}: ${status}`}
						/>
					{/if}
					<span
						class={`text-[11px] text-faint ${bar ? 'flex-none tabular-nums' : 'min-w-0 flex-1 truncate'}`}
						title={bar ? '' : said}
					>
						{said}
					</span>
				</div>
			{/if}
		{/snippet}

		{#snippet panel()}
			<div class="rounded-lg border border-line bg-sunken px-2.5 py-2">
				<p class="font-mono text-[11px] wrap-anywhere text-faint">{row.path}</p>
				{#if failure}
					<p role="alert" class="mt-1.5 text-[11.5px] text-danger">{failure}</p>
				{:else if lines === null}
					<p class="mt-1.5 text-[11.5px] text-faint">Reading the log…</p>
				{:else if !lines.length}
					<!-- A file far enough back has had its lines dropped. -->
					<p class="mt-1.5 text-[11.5px] text-faint">No log kept for this file.</p>
				{:else}
					<div bind:this={tail} class="mt-1.5 max-h-56 overflow-y-auto border-t border-line pt-1.5">
						{#each lines as line, at (at)}
							<p class="font-mono text-[11px] whitespace-pre-wrap text-dim">{line}</p>
						{/each}
					</div>
				{/if}
			</div>
		{/snippet}
	</Disclosure>
</li>
