<script lang="ts">
	import { frosted } from '$lib/controls';
	import { popover } from '$lib/popover.svelte';
	import type { Change } from '$lib/library';

	// The rules that judged one file, each opening on the changes it ordered.

	let {
		rules = [],
		incidental = [],
		changes = []
	}: {
		rules?: string[];
		incidental?: string[];
		changes?: Change[];
	} = $props();

	// The verb a change opens with and the mark that stands for it. The planner
	// writes these strings to a form, so the first word is the verb; see _record
	// in planner.py. A clear takes a title away, which is a drop of the only thing
	// it had. The plus and minus are the pair `shrank` already uses.
	const CHANGE_MARKS: [string, string][] = [
		['add', '+'],
		['drop', '−'],
		['clear', '−'],
		['regenerate', '~'],
		['replace', '~']
	];

	// A remux or a reorder is none of the three, and keeps the plain mark.
	function changeMark(text: string): string {
		const verb = text.slice(0, text.indexOf(' '));
		return CHANGE_MARKS.find(([opener]) => opener === verb)?.[1] ?? '›';
	}

	// `rides` is the fainter tone: these never cause a rewrite on their own. A
	// file judged before the planner paired its lines has no chip to open.
	const chips = $derived(
		[
			...rules.map((rule) => ({ rule, rides: false })),
			...incidental.map((rule) => ({ rule, rides: true }))
		].map((chip) => ({
			...chip,
			lines: changes.filter((change) => change.rule === chip.rule).map((change) => change.text)
		}))
	);

	const id = $props.id();
	let triggers = $state<HTMLButtonElement[]>([]);
	let panels = $state<HTMLDivElement[]>([]);
	const chooser = popover({ edge: 'left' });

	// Sized for the longest line one carries, a cleared release title, without
	// running to the window edge on a phone.
	const PANEL = `fixed m-0 w-72 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-2.5 text-fg shadow-lg`;
	const CHIP = 'rounded border border-line px-1.5 py-1 font-mono text-[10.5px]';
	const ACTING = `${CHIP} bg-accent-soft text-accent`;
	const RIDES = `${CHIP} text-faint`;
</script>

<svelte:window
	onpointerdown={(event) =>
		chooser.open && chooser.outside(event.target as Node) && chooser.lower()}
	onresize={() => chooser.open && chooser.lower()}
/>

<div class="mt-3 flex flex-wrap gap-1.5">
	{#each chips as chip, at (chip.rule)}
		{#if chip.lines.length}
			<button
				bind:this={triggers[at]}
				type="button"
				onclick={() => chooser.toggle(triggers[at], panels[at])}
				aria-expanded={chooser.holds(panels[at])}
				aria-controls={`${id}-${at}`}
				class={`${chip.rides ? RIDES : ACTING} min-h-7 hover:bg-raised`}
			>
				{chip.rule}
			</button>
			<div bind:this={panels[at]} id={`${id}-${at}`} popover="manual" class={PANEL}>
				<p class="pb-1.5 font-mono text-[10.5px] text-faint">{chip.rule}</p>
				<ul class="flex flex-col gap-1">
					{#each chip.lines as line, row (row)}
						<li class="flex gap-1.5 text-[12px] text-dim">
							<!-- Marks our fonts carry; see Glyph.svelte. Fixed width so every
							     line starts on one column whichever mark it takes. -->
							<span class="w-2 flex-none text-center">{changeMark(line)}</span>
							<span class="min-w-0">{line}</span>
						</li>
					{/each}
				</ul>
			</div>
		{:else}
			<span class={`${chip.rides ? RIDES : ACTING} inline-flex min-h-7 items-center`}>
				{chip.rule}
			</span>
		{/if}
	{/each}
</div>
