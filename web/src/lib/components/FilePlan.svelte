<script lang="ts">
	import { describe } from '$lib/format';
	import { listing, verdictLabel, verdictText } from '$lib/library';
	import { again, planOf } from '$lib/plans.svelte';

	let { path }: { path: string } = $props();
	// Read while the row was shut, in all but the moments after a page loads.
	const plan = $derived(planOf(path));
	const data = $derived(plan.detail);
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
		{:else if !file.planned.length}
			<p>
				{file.status === 'unchecked'
					? 'No plan yet. This file will be checked before processing.'
					: 'No planned changes recorded.'}
			</p>
		{/if}
		{#if file.planned.length}
			{@const tracks = listing(file)}
			<p class="mt-3 font-medium text-fg">{tracks.label}</p>
			<ul class="mt-1 divide-y divide-line">
				{#each tracks.rows as row, index (index)}
					<li class="flex flex-wrap items-baseline justify-between gap-x-2 py-1">
						<span class="min-w-0 wrap-anywhere">{row.track.kind} · {describe(row.track)}</span>
						<span class="text-[11px] text-faint"
							>{row.state === 'added' ? 'Add' : row.state === 'dropped' ? 'Remove' : 'Keep'}</span
						>
					</li>
				{/each}
			</ul>
		{/if}
	{:else}
		<p>No saved verdict or plan yet. This file will be checked before processing.</p>
	{/if}
</div>
