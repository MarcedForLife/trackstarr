<script lang="ts">
	import { resolve } from '$app/paths';
	import Glyph from './Glyph.svelte';
	import { button, primary } from '$lib/controls';
	import { REPORT_ONLY_NOTE, type RunMode } from '$lib/library';
	import { overlay } from '$lib/overlay';

	// The two runs as two buttons, each naming its consequence. Three surfaces
	// start runs, so the pair lives here.
	let {
		mayRewrite,
		// Why neither can be pressed now, in the caller's words.
		refuses = '',
		disabled = false,
		// Which of the two is under way, so only that one says so.
		busy = '',
		// Whether the pair fills its width on a phone, and wraps as one.
		fill = false,
		// Whether the pair takes two columns of a grid the caller lays out, one
		// each, at every width. For a row where a third button must match them.
		columns = false,
		onrun
	}: {
		mayRewrite: boolean;
		refuses?: string;
		disabled?: boolean;
		busy?: '' | RunMode;
		fill?: boolean;
		columns?: boolean;
		onrun: (mode: RunMode) => void;
	} = $props();

	// A share of the line below sm, natural width from sm up.
	const spread = $derived(columns ? 'w-full' : fill ? 'flex-1 sm:flex-none' : 'flex-none');

	const off = $derived(disabled || !!refuses || !!busy);

	// Why Process is dead, opened by pressing it: a touchscreen has no hover for
	// a title.
	let why = $state(false);
	let box = $state<HTMLElement>();

	// Answers Escape before the sheet under it, but takes no history entry.
	const note = overlay({ close: () => (why = false) });

	function explain() {
		if (why) {
			note.lower();
			return;
		}
		why = true;
		note.raise();
	}

	// aria-disabled, so the button still takes the press that explains it.
	function press(mode: RunMode) {
		if (mode === 'apply' && !mayRewrite) explain();
		else onrun(mode);
	}
</script>

<svelte:window
	onpointerdown={(event) => why && !box?.contains(event.target as Node) && note.lower()}
/>

<!-- Accent on Plan, not Process: the loud one should be safe to press without
     thinking. -->
<div
	class={`relative items-center ${
		columns
			? // Two of the caller's columns, split by the caller's gap, so each button
				// comes out the width of a column beside them.
				'col-span-2 grid grid-cols-2 gap-3'
			: fill
				? // The caller's own gap, so a pair that has wrapped under another pair
					// lines up with it column for column instead of by a few pixels.
					'flex min-w-fit flex-1 gap-3 sm:min-w-0 sm:flex-initial'
				: 'flex min-w-0 gap-2 sm:gap-3'
	}`}
	bind:this={box}
>
	<button
		type="button"
		onclick={() => press('report')}
		disabled={off}
		title="Reads every file and records what needs doing. Changes nothing."
		class={`${spread} ${primary}`}
	>
		<Glyph name="doc" />
		{busy === 'report' ? 'Planning…' : 'Plan'}
	</button>
	<button
		type="button"
		onclick={() => press('apply')}
		disabled={mayRewrite && off}
		aria-disabled={!mayRewrite || undefined}
		aria-expanded={mayRewrite ? undefined : why}
		title={mayRewrite
			? 'Reads every file and applies the rules. Changes files on disk.'
			: REPORT_ONLY_NOTE}
		class={`${spread} ${button} aria-disabled:opacity-50`}
	>
		<Glyph name="play" />
		{busy === 'apply' ? 'Processing…' : 'Process'}
	</button>

	{#if why}
		<!-- Positioned, not in the flow, or it would push the row out of line. -->
		<p
			role="status"
			class="absolute top-full left-0 z-30 mt-2 w-64 rounded-xl border border-line-strong bg-raised p-3 text-[12.5px] text-dim shadow-lg"
		>
			{REPORT_ONLY_NOTE}
			<a
				href={resolve('/settings')}
				class="mt-1 block font-medium text-accent underline underline-offset-2 hover:text-fg"
			>
				Change it in Settings
			</a>
		</p>
	{/if}
</div>
