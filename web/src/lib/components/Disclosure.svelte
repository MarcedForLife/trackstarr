<script lang="ts">
	import type { Snippet } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { ripple } from '$lib/ripple';
	import { shift } from '$lib/shift';
	import { unclip } from '$lib/unclip';

	// A row that opens to show what it had no room for: a button with the aria, a
	// chevron that turns, and a panel unmounted while shut that opens on
	// `.reveal` in layout.css. What the row says arrives as snippets.
	let {
		// Ties the button to the panel it opens; unique within the page.
		id,
		open,
		ontoggle,
		// The button's classes; `group` lets the chevron answer a hover.
		class: shape = 'group block w-full text-left',
		// The chevron's size.
		mark = 11,
		// How far the chevron turns: a quarter from right to down, or a half from
		// down to up.
		turn = 'quarter',
		// Whether a press ripples, for a row too wide for a flash to say where.
		press = false,
		// The panel's spacing.
		panelClass = 'mt-2',
		// The row, given the chevron to place inside its own line.
		summary,
		// Anything under the summary but outside the panel, shown while shut.
		aside,
		// Something left of the summary; the panel still takes the full width.
		beside,
		panel
	}: {
		id: string;
		open: boolean;
		ontoggle: () => void;
		class?: string;
		mark?: number;
		turn?: 'quarter' | 'half';
		press?: boolean;
		panelClass?: string;
		summary: Snippet<[Snippet]>;
		aside?: Snippet;
		beside?: Snippet;
		panel?: Snippet;
	} = $props();

	// Opening slower than closing.
	const OPENING = 'duration-[260ms] ease-[cubic-bezier(0.22,1,0.36,1)]';
	const CLOSING = 'duration-[180ms] ease-[cubic-bezier(0.4,0,0.2,1)]';

	// Tailwind v4 puts rotate in its own property; `transition-transform` covers
	// it, a hand-written transition on `transform` would not.
	const half = $derived(turn === 'half');
	const angle = $derived(open ? (half ? '-rotate-90' : 'rotate-90') : half ? 'rotate-90' : '');
</script>

{#snippet chevron()}
	<span
		aria-hidden="true"
		class={`inline-flex flex-none self-center text-faint transition-transform group-hover:text-fg ${angle} ${
			open ? OPENING : CLOSING
		}`}
	>
		<Glyph name="chevron" size={mark} />
	</span>
{/snippet}

{#snippet row()}
	<!-- The whole summary is the target. -->
	<button
		onclick={ontoggle}
		aria-expanded={open}
		aria-controls={id}
		class={shape}
		use:ripple={press}
	>
		{@render summary(chevron)}
	</button>
	{@render aside?.()}
{/snippet}

<!-- A sibling of the summary, not inside it: a control inside a control is
     neither valid nor reachable by keyboard. -->
{#if beside}
	<div class="flex gap-2.5">
		{@render beside()}
		<div class="min-w-0 flex-1">{@render row()}</div>
	</div>
{:else}
	{@render row()}
{/if}

<!-- Unmounted while shut, so a long list carries no hidden panels. Opens on
     `.reveal` in layout.css; `shift` moves what follows and `unclip` lets the
     clip go afterwards. -->
{#if open && panel}
	<div {id} class={`reveal ${panelClass}`} use:shift use:unclip>
		<div>{@render panel()}</div>
	</div>
{/if}
