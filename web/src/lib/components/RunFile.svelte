<script lang="ts">
	import Bar from '$lib/components/Bar.svelte';
	import HoldMenu from '$lib/components/HoldMenu.svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import { verdictHint, verdictLabel } from '$lib/library';
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
		origin = '',
		age = 0,
		ended = false,
		open = false,
		id,
		skippable = false,
		busy = false,
		ontoggle,
		onskip,
		onhold
	}: {
		// The run id, which is half of what names a file's log.
		run: string;
		row: FileRow;
		origin?: string;
		// Seconds since the snapshot arrived, to carry a live row's readouts
		// forward. A finished row ignores it.
		age?: number;
		ended?: boolean;
		open?: boolean;
		// Ties the button to the panel it opens; unique within the page.
		id: string;
		// An admin's, on a file this run has still to finish with.
		skippable?: boolean;
		busy?: boolean;
		ontoggle: () => void;
		onskip?: () => void;
		onhold?: (seconds: number) => Promise<void>;
	} = $props();

	const live = $derived(!ended && !!row.live);
	const waiting = $derived(!ended && !!row.waiting);
	const active = $derived(live && row.live?.stage !== 'waiting');
	const action = $derived(active ? 'Cancel' : 'Skip');
	const shown = $derived(named(row.path));
	const status = $derived(live && row.live ? fileStatus(row.live, age) : '');
	const bar = $derived(live && !!row.live && fileBar(row.live));

	// A live row's clock runs; a finished one's is how long the worker had it. A
	// waiting row has none, and shows what its rewrite is expected to take.
	const held = $derived(live ? row.seconds + age : row.seconds);
	const expected = $derived(row.waiting?.expected ?? 0);

	// The word on the right: the verdict in the library's words, or where the
	// file stands with this run. A skipped file being encoded says so until the
	// thread lets go of it.
	const verdict = $derived(
		row.skipped
			? live
				? 'Stopping…'
				: 'Skipped'
			: row.verdict
				? verdictLabel(row.verdict)
				: ended
					? row.waiting
						? 'Not processed'
						: 'Ended'
					: waiting
						? 'Queued'
						: ''
	);
	// The line under the name: how far through, or the detail.
	const said = $derived(live ? status : ended && row.live ? '' : row.detail);

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
		const working = live;
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

<li class="py-2.5 text-[12px]">
	<Disclosure
		{id}
		{open}
		{ontoggle}
		mark={10}
		class="group block min-h-11 w-full text-left"
		panelClass="mt-2"
	>
		{#snippet summary(chevron)}
			<span class="flex items-start gap-2">
				<span class="min-w-0 flex-1">
					<span class="block text-[13px] leading-snug font-medium wrap-anywhere text-fg">
						{shown.name}
						{#if shown.episode}<span class="whitespace-nowrap text-dim">{shown.episode}</span>{/if}
					</span>
					<span class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-dim">
						<span
							title={row.verdict ? verdictHint(row.verdict) : undefined}
							class={row.verdict === 'failed' ? 'font-medium text-danger' : ''}
							>{verdict ||
								(bar
									? 'Rewriting'
									: live
										? row.live?.stage === 'waiting'
											? 'Waiting for slot'
											: 'Working'
										: '')}</span
						>
						{#if origin}<span class="text-faint">{origin}</span>{/if}
						<span class="text-faint tabular-nums"
							>{waiting
								? expected
									? `~${duration(expected)}`
									: ''
								: ended && row.waiting
									? ''
									: duration(held)}</span
						>
					</span>
				</span>
				{@render chevron()}
			</span>
		{/snippet}

		{#snippet after()}
			{#if skippable}
				<button
					onclick={onskip}
					disabled={busy}
					aria-label={`${action} ${titled(row.path)}`}
					title={active ? 'Cancel this file. The original stays untouched.' : undefined}
					class="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg px-3 text-[12px] font-medium text-dim hover:bg-sunken hover:text-danger disabled:opacity-(--disabled)"
					>{action}</button
				>
			{/if}
		{/snippet}

		<!-- The bar for a rewrite, or a probe being over inside a second. -->
		{#snippet aside()}
			{#if bar || said}
				<div class="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
					{#if bar && row.live}
						<Bar
							file
							fill={fileFraction(row.live, age)}
							now={Math.floor(fileFraction(row.live, age) * 100)}
							max={100}
							text={`${titled(row.path)}: ${status}`}
						/>
					{/if}
					{#if said}
						<span
							class={`text-[11px] text-faint ${bar ? 'tabular-nums' : 'line-clamp-2 min-w-0 flex-1 wrap-anywhere'}`}
							title={bar ? '' : said}
						>
							{said}
						</span>
					{/if}
				</div>
			{/if}
		{/snippet}

		{#snippet panel()}
			{#if skippable && onhold}
				<div class="mb-2">
					<HoldMenu
						label={titled(row.path)}
						disabled={busy}
						onchoose={onhold}
						hint={active
							? 'Stops this attempt. After the hold ends, a later sweep can restart the rewrite from the beginning.'
							: 'A later sweep can process this file after the hold ends.'}
						class="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg px-3 text-[12px] font-medium text-dim hover:bg-sunken disabled:opacity-(--disabled)"
					/>
				</div>
			{/if}

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
							<p class="font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-dim">{line}</p>
						{/each}
					</div>
				{/if}
			</div>
		{/snippet}
	</Disclosure>
</li>
