<script lang="ts">
	import { badges, describe } from '$lib/format';
	import { listing, verdictLabel, verdictText } from '$lib/library';
	import type { LibraryFile, Row } from '$lib/library';
	import { again, planOf } from '$lib/plans.svelte';

	let { path }: { path: string } = $props();
	// Read while the row was shut, in all but the moments after a page loads.
	const plan = $derived(planOf(path));
	const data = $derived(plan.detail);

	// What the rewrite does to a row, in the chips' own colours: green for what
	// it gains, red for what it takes away.
	const KEYS: Record<Row['state'], { label: string; tone: string }> = {
		added: { label: 'Add', tone: 'text-ok' },
		kept: { label: 'Keep', tone: 'text-faint' },
		dropped: { label: 'Remove', tone: 'text-danger' }
	};
</script>

<!-- Last in the panels that draw it, so a first load's answer lands at the foot
     and the reveal uncovers it. Spacing is the caller's. -->
<div class="text-[12px] leading-relaxed text-dim" aria-busy={plan.loading}>
	{#if plan.loading}
		<p>Reading saved plan…</p>
	{:else if plan.error}
		<p role="alert">
			{plan.error} <button class="min-h-8 underline" onclick={() => again(path)}>Retry</button>
		</p>
	{:else if data?.file}
		{@const file = data.file}
		<!-- Every row that opens this panel is queued, paused or being worked on,
		     so Pending is a given and only another verdict is worth a line. -->
		{#if file.status !== 'pending'}
			<p class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
				<span class="font-medium text-fg">Last checked verdict</span>
				<span class={`font-semibold ${verdictText[file.status]}`}>{verdictLabel(file.status)}</span>
			</p>
		{/if}
		{#if !data.current}<p class="mb-2">
				Rules have changed since this check. The plan will be re-evaluated before processing.
			</p>{/if}
		{#if file.why.failed}<p class="mb-2 text-danger">{file.why.failed}</p>{/if}
		{#if file.why.skip}<p class="mb-2">{file.why.skip}</p>{/if}
		{#if file.planned.length}
			{@const tracks = listing(file)}
			<!-- The table is the plan, so the reasons in prose only earn their place
			     where no row moves: a reorder, a retag or a remux. -->
			{@const quiet = tracks.rows.every((row) => row.state === 'kept')}
			{#if quiet}{@render prose(file)}{/if}
			<p class={`font-medium text-fg ${quiet ? 'mt-3' : ''}`}>{tracks.label}</p>
			<ul class="mt-1 divide-y divide-line">
				{#each tracks.rows as row, index (index)}
					{@const key = KEYS[row.state]}
					{@const flags = badges(row.track)}
					<li class="flex items-baseline justify-between gap-x-2 py-1">
						<span class="min-w-0 flex-1">
							<span class="block wrap-anywhere">{row.track.kind} · {describe(row.track)}</span>
							<!-- What one row has that another of the same codec and language
							     does not: two subtitles differ by SDH, two audio by commentary. -->
							{#if row.track.title || flags.length}
								<span class="flex flex-wrap items-baseline gap-1 text-[11px] text-faint">
									{#if row.track.title}<span class="min-w-0 wrap-anywhere">{row.track.title}</span
										>{/if}
									{#each flags as flag (flag)}
										<span class="flex-none rounded border border-line px-1 text-[10px]">{flag}</span
										>
									{/each}
								</span>
							{/if}
						</span>
						<span class={`flex-none text-[11px] ${key.tone}`}>{key.label}</span>
					</li>
				{/each}
			</ul>
		{:else}
			{@render prose(file)}
		{/if}
	{:else}
		<p>No saved verdict or plan yet. This file will be checked before processing.</p>
	{/if}
</div>

{#snippet prose(file: LibraryFile)}
	{#if file.why.reasons?.length || file.why.incidental?.length}
		<p class="font-medium text-fg">Planned changes</p>
		<ul class="mt-1 list-disc space-y-1 pl-4">
			{#each file.why.reasons ?? [] as reason, index (index)}<li class="wrap-anywhere">
					{reason}
				</li>{/each}
			{#each file.why.incidental ?? [] as reason, index (index)}<li
					class="wrap-anywhere text-faint"
				>
					{reason} <span>(with rewrite)</span>
				</li>{/each}
		</ul>
	{:else}
		<p>
			{file.status === 'unchecked'
				? 'No plan yet. This file will be checked before processing.'
				: 'No planned changes recorded.'}
		</p>
	{/if}
{/snippet}
