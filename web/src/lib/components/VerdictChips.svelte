<script lang="ts">
	import { control } from '$lib/controls';
	import { FILTERS, pip, tint, verdictHint, verdictLabel, type Verdict } from '$lib/library';

	// The verdict filter row, over the library grid and on Appearance, where it
	// sets what the grid opens holding. One component, so the setting looks like
	// what it governs.
	let {
		chosen,
		onchange,
		counts,
		allHint,
		allCount,
		label,
		labelledBy,
		describedBy
	}: {
		// Which verdicts are held. Empty is All, which is not itself a value.
		chosen: Verdict[];
		onchange: (next: Verdict[]) => void;
		// How many titles each verdict holds. Absent on Appearance.
		counts?: Record<string, number>;
		allHint: string;
		allCount?: number;
		// The label for the row over the grid; on Appearance the SettingRow's
		// paragraph ids instead.
		label?: string;
		labelledBy?: string;
		describedBy?: string;
	} = $props();

	// Against a shelf, only the states something is in. Without one, all of them,
	// or a setting that holds Failed until something fails is unfindable.
	const states = $derived(counts ? FILTERS.filter((state) => counts[state]) : FILTERS);

	const on = (state: Verdict) => chosen.includes(state);

	// A chip toggles. All off is All.
	function toggle(state: Verdict) {
		onchange(on(state) ? chosen.filter((held) => held !== state) : [...chosen, state]);
	}

	// A group of toggles, not a radio.
	const chip = `flex ${control} items-center gap-2 rounded-full border px-3.5 text-[13px] transition-colors sm:px-3`;
</script>

<!-- A wrapping row, not tabs: eight states will not fit a phone. -->
<div
	role="group"
	aria-label={label}
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	class="flex flex-wrap gap-2"
>
	<button
		type="button"
		aria-pressed={!chosen.length}
		title={allHint}
		onclick={() => onchange([])}
		class={`${chip} ${
			chosen.length
				? 'border-line font-medium text-dim'
				: 'border-line-strong bg-raised font-semibold text-fg'
		}`}
	>
		<!-- All carries no dot, or it would read as one more verdict. -->
		All
		{#if allCount !== undefined}
			<span class={`tabular-nums ${chosen.length ? 'text-faint' : 'text-dim'}`}>
				{allCount.toLocaleString()}
			</span>
		{/if}
	</button>
	{#each states as state (state)}
		<button
			type="button"
			aria-pressed={on(state)}
			title={verdictHint(state)}
			onclick={() => toggle(state)}
			class={`${chip} ${
				on(state) ? `${tint[state]} font-semibold text-fg` : 'border-line font-medium text-dim'
			}`}
		>
			<!-- The posters' own dot. -->
			<span class={`h-1.5 w-1.5 flex-none rounded-full ${pip[state]}`}></span>
			{verdictLabel(state)}
			{#if counts}
				<span class={`tabular-nums ${on(state) ? 'text-dim' : 'text-faint'}`}>
					{counts[state].toLocaleString()}
				</span>
			{/if}
		</button>
	{/each}
</div>
