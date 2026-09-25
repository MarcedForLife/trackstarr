<script lang="ts">
	import { changed } from '$lib/library';
	import type { FileChanges } from '$lib/queue';

	// What a rewrite would do to a file, counted on its row with layout names
	// available on hover. Draws nothing for a judged file with no work to do.
	// One item of the row's dotted status line, the counts as coloured text since
	// chips read as buttons.
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

	// The chips carry a sign and a colour, so the same thing in words.
	const spread = $derived.by(() => {
		const parts = [];
		if (plan?.adds.length) parts.push(`adds ${plan.adds.join(', ')}`);
		if (plan?.rebuilds.length) parts.push(`rebuilds ${plan.rebuilds.join(', ')}`);
		if (plan?.drops) parts.push(`drops ${plan.drops} track${plan.drops === 1 ? '' : 's'}`);
		return parts.join(', ');
	});
</script>

{#if note}
	<span class="flex-none text-faint">{note}</span>
{:else if chips.length || plan?.drops}
	<!-- Mono on the counts only, so the dot before them keeps the line's face. -->
	<span class="flex-none [&>*+*]:ml-1.5" title={spread} aria-label={spread}>
		{#each chips as change (change.chip)}<span
				class={`font-mono text-[11.5px] tracking-tight ${change.ink}`}>{change.chip}</span
			>{/each}
		{#if plan?.drops}<span class="font-mono text-[11.5px] tracking-tight text-dim"
				>&minus;{plan.drops}</span
			>{/if}
	</span>
{:else if plan?.changes}
	<span class="flex-none text-faint">{plan.changes} change{plan.changes === 1 ? '' : 's'}</span>
{/if}
