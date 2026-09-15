<script lang="ts">
	import { changed, CHIP, PLAIN_CHIP } from '$lib/library';
	import type { FileChanges } from '$lib/queue';

	// What a rewrite would do to a file, counted on its row with layout names
	// available on hover. Draws nothing for a judged file with no work to do.
	let {
		plan,
		current = true,
		chipsOnly = false
	}: {
		// Absent until a sweep has judged the file.
		plan?: FileChanges;
		// False where the rules have moved on since that check, which is the whole
		// page's answer and not this row's.
		current?: boolean;
		// For a row a worker already has. Both notes below are about queueing, which
		// that file is past.
		chipsOnly?: boolean;
	} = $props();

	const chips = $derived(plan ? changed(plan) : []);
	// A file no sweep has reached says so, or an empty line reads as no work.
	const judged = $derived(!!plan && plan.status !== 'unchecked');
	// Why there are no chips, where that is worth a line. A replan may not ask for
	// the layouts the chips name.
	const note = $derived(
		chipsOnly ? '' : !judged ? 'Not checked yet' : !current ? 'Re-check pending' : ''
	);
	const planned = $derived(!!note || chips.length > 0 || !!plan?.drops || !!plan?.changes);

	// The chips carry a sign and a colour, so the same thing in words.
	const spread = $derived.by(() => {
		const parts = [];
		if (plan?.adds.length) parts.push(`adds ${plan.adds.join(', ')}`);
		if (plan?.rebuilds.length) parts.push(`rebuilds ${plan.rebuilds.join(', ')}`);
		if (plan?.drops) parts.push(`drops ${plan.drops} track${plan.drops === 1 ? '' : 's'}`);
		return parts.join(', ');
	});
</script>

{#if planned}
	<span class="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
		{#if note}
			<span class="text-[11px] text-faint">{note}</span>
		{:else if chips.length || plan?.drops}
			<span class="flex flex-wrap gap-1" title={spread} aria-label={spread}>
				{#each chips as change (change.chip)}
					<span class={`${CHIP} ${change.tone}`}>{change.chip}</span>
				{/each}
				{#if plan?.drops}<span class={PLAIN_CHIP}>&minus;{plan.drops}</span>{/if}
			</span>
		{:else if plan?.changes}
			<span class="text-[11px] text-faint"
				>{plan.changes} change{plan.changes === 1 ? '' : 's'}</span
			>
		{/if}
	</span>
{/if}
