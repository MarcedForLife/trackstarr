<script lang="ts">
	import { resolve } from '$app/paths';
	import Spinner from '$lib/components/Spinner.svelte';
	import { button, markAccent, markQuiet, markWorded, primary } from '$lib/controls';
	import { REPORT_ONLY_NOTE, type RunMode } from '$lib/library';
	import { overlay } from '$lib/overlay';

	// Plan and Process for a selection or one title. No confirm step, unlike the
	// overview's glyph-only pair, since these show their words.
	let {
		mayRewrite,
		// Why neither can be pressed now, in the caller's words.
		refuses = '',
		disabled = false,
		// Which of the two is under way.
		busy = '',
		// Whether the pair fills its width on a phone, and wraps as one.
		fill = false,
		// Draw the pair as marks spanning two columns of the caller's grid.
		marks = false,
		mobileColumns = false,
		onrun
	}: {
		mayRewrite: boolean;
		refuses?: string;
		disabled?: boolean;
		busy?: '' | RunMode;
		fill?: boolean;
		marks?: boolean;
		/** Match caller grid columns on mobile, then use natural button widths. */
		mobileColumns?: boolean;
		onrun: (mode: RunMode) => void;
	} = $props();

	// A share of the line below sm, natural width from sm up.
	const spread = $derived(
		mobileColumns ? 'w-full !px-2 sm:w-auto sm:!px-3.5' : fill ? 'flex-1 sm:flex-none' : 'flex-none'
	);
	const planShape = $derived(marks ? `${markWorded} ${markAccent} w-full` : `${spread} ${primary}`);
	const processShape = $derived(
		marks ? `${markWorded} ${markQuiet} w-full` : `${spread} ${button}`
	);
	// Marks match the overview's glyph size.
	const glyphSize = $derived(marks ? 14 : 12);

	const off = $derived(disabled || !!refuses || !!busy);

	// Why Process is refused, shown on press since touch has no hover.
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

<!-- Accent on Plan, the safe one. -->
<div
	class={`relative items-center ${
		mobileColumns
			? 'col-span-2 grid min-w-0 grid-cols-2 gap-3 sm:flex'
			: marks
				? 'col-span-2 grid grid-cols-2 gap-1.5'
				: fill
					? // The caller's gap, so wrapped pairs line up.
						'flex min-w-fit flex-1 gap-3 sm:min-w-0 sm:flex-initial'
					: 'flex min-w-0 gap-2 sm:gap-3'
	}`}
	bind:this={box}
>
	<button
		type="button"
		onclick={() => press('report')}
		disabled={off}
		aria-busy={busy === 'report'}
		title="Reads every file and records what needs doing. Changes nothing."
		class={planShape}
	>
		<Spinner glyph="doc" size={glyphSize} busy={busy === 'report'} />
		{busy === 'report' ? 'Planning…' : 'Plan'}
	</button>
	<button
		type="button"
		onclick={() => press('apply')}
		disabled={mayRewrite && off}
		aria-disabled={!mayRewrite || undefined}
		aria-busy={busy === 'apply'}
		aria-expanded={mayRewrite ? undefined : why}
		title={mayRewrite
			? 'Reads every file and applies the rules. Changes files on disk.'
			: REPORT_ONLY_NOTE}
		class={`${processShape} aria-disabled:opacity-(--disabled)`}
	>
		<Spinner glyph="bolt" size={glyphSize} busy={busy === 'apply'} />
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
