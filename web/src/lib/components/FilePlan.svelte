<script lang="ts">
	import FileAccount from './FileAccount.svelte';
	import { verdictLabel, verdictText } from '$lib/library';
	import { again, planOf } from '$lib/plans.svelte';

	let { path }: { path: string } = $props();
	// Read while the row was shut, in all but the moments after a page loads.
	const plan = $derived(planOf(path));
	const data = $derived(plan.detail);
</script>

<!-- Last in the panels that draw it, so a first load's answer lands at the foot
     and the reveal uncovers it. Spacing is the caller's. Only where the plan
     came from is this panel's own; the rest is the shared account. -->
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
			<p class="flex flex-wrap items-baseline justify-between gap-2">
				<span class="font-medium text-fg">Last checked verdict</span>
				<span class={`font-semibold ${verdictText[file.status]}`}>{verdictLabel(file.status)}</span>
			</p>
		{/if}
		{#if !data.current}<p class="mt-2">
				Settings or version changed. These details may be outdated. The plan will be re-evaluated
				before processing.
			</p>{/if}
		<FileAccount {file} />
	{:else}
		<p>No saved verdict or plan yet. This file will be checked before processing.</p>
	{/if}
</div>
