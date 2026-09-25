<script lang="ts">
	import type { Snippet } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { pressGesture } from '$lib/press';
	import { ripple } from '$lib/ripple';
	import { fold, shift, unfold } from '$lib/shift';

	// A row that opens to show what it had no room for: a button with the aria, a
	// chevron that turns, and a panel unmounted while shut that opens on
	// `$lib/shift`. What the row says arrives as snippets.
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
		// Where the chevron and `after` sit against a row of several lines.
		// Centred reads adrift once a row grows a third line.
		align = 'center',
		// Whether a press ripples, for a row too wide for a flash to say where.
		press = false,
		// A long press on the row, and whether lifting now would land it.
		hold,
		onarm,
		// A finger or button held on the row, for a caller that raises it.
		onpress,
		// Whether this row is picked, and by being set at all, whether a tap picks
		// rather than opens. The caller answers the press; this is what it says.
		picked,
		// The panel's spacing.
		panelClass = 'mt-2',
		// The row, given the chevron to place inside its own line.
		summary,
		// Anything under the summary but outside the panel, shown while shut.
		aside,
		// Something left of the summary; the panel still takes the full width.
		beside,
		// Controls right of the summary, on its line but outside the button: a
		// control within a control is neither valid nor reachable by keyboard.
		// Given the chevron, for a row that wants it pressable on its own.
		after,
		// A control right after the summary, which then takes only its own width.
		trail,
		panel
	}: {
		id: string;
		open: boolean;
		ontoggle: () => void;
		class?: string;
		mark?: number;
		turn?: 'quarter' | 'half';
		align?: 'center' | 'start';
		press?: boolean;
		hold?: () => void;
		onarm?: (on: boolean) => void;
		onpress?: (on: boolean) => void;
		picked?: boolean;
		panelClass?: string;
		summary: Snippet<[Snippet<[boolean?]>]>;
		aside?: Snippet;
		beside?: Snippet;
		after?: Snippet<[Snippet<[boolean?]>]>;
		trail?: Snippet;
		panel?: Snippet;
	} = $props();

	// The panel measures its opening; closing keeps the shared short duration.
	let openingDuration = $state<number>();
	const TURN = 'duration-(--reveal-span) ease-(--reveal-ease)';

	// A row that picks is a checkbox; nothing opens while it is one.
	const aria = $derived(
		picked === undefined
			? { 'aria-expanded': open, 'aria-controls': id }
			: { role: 'checkbox', 'aria-checked': picked }
	);

	// Tailwind v4 puts rotate in its own property; `transition-transform` covers
	// it, a hand-written transition on `transform` would not.
	const half = $derived(turn === 'half');
	const angle = $derived(open ? (half ? '-rotate-90' : 'rotate-90') : half ? 'rotate-90' : '');

	// A panel open at the first render was never opened by anyone, so it takes
	// its height at once: growing into it fights whatever is still arriving
	// around it, such as a sheet mid-slide.
	// svelte-ignore state_referenced_locally
	let alreadyOpen = open;
	function opening() {
		const first = alreadyOpen;
		alreadyOpen = false;
		return !first;
	}

	// Svelte hands the same node back when a row is opened again before its
	// close has finished, and only this puts it right.
	let box = $state<HTMLElement>();
	$effect(() => {
		if (open && box) unfold(box);
	});
</script>

{#snippet chevron(bare = false)}
	<!-- Bare where the caller has put it in a control of its own, which brings
	     its own place and colour. -->
	<span
		aria-hidden="true"
		style:transition-duration={open && openingDuration !== undefined
			? `${openingDuration}ms`
			: undefined}
		class={`inline-flex flex-none transition-transform ${angle} ${TURN} ${
			bare
				? ''
				: `text-faint group-hover:text-fg ${align === 'start' ? 'mt-1 self-start' : 'self-center'}`
		}`}
	>
		<Glyph name="chevron" size={mark} />
	</span>
{/snippet}

{#snippet toggle()}
	<!-- The whole summary is the target. Where a long press means something the
	     gesture answers the tap as well, since a click would double it. -->
	{#if hold}
		<button
			use:pressGesture={{ ontap: ontoggle, onhold: hold, onarm, onpress }}
			{...aria}
			class={`${shape} pressable`}
			use:ripple={press}
		>
			{@render summary(chevron)}
		</button>
	{:else}
		<button onclick={ontoggle} {...aria} class={shape} use:ripple={press}>
			{@render summary(chevron)}
		</button>
	{/if}
{/snippet}

{#snippet line()}
	{#if trail}
		<div class="flex min-w-0 items-center">{@render toggle()}{@render trail()}</div>
	{:else}
		{@render toggle()}
	{/if}
{/snippet}

{#snippet row()}
	{#if after}
		<div class={`flex gap-3 ${align === 'start' ? 'items-start' : 'items-center'}`}>
			<div class="min-w-0 flex-1">{@render line()}</div>
			{@render after(chevron)}
		</div>
	{:else}
		{@render line()}
	{/if}
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

<!-- Unmounted while shut, so a long list carries no hidden panels. `shift` animates its height
     and clip together; `fold` keeps it mounted through closing. -->
{#if open && panel}
	<!-- Positioned, so a row whose press covers the card does not cover this. -->
	<div
		bind:this={box}
		{id}
		class={`reveal relative ${panelClass}`}
		use:shift={{ measured: (duration) => (openingDuration = duration), animate: opening() }}
		out:fold
	>
		<div><div>{@render panel()}</div></div>
	</div>
{/if}

<style>
	/* A held row answers a finger as a poster does: sideways is the gesture's,
	   never a scroll, and no callout or grey flash. `pan-y` not `none`, since a
	   drag down the list is the scroller's until the press is made. */
	.pressable {
		touch-action: pan-y;
		user-select: none;
		-webkit-touch-callout: none;
		-webkit-tap-highlight-color: transparent;
	}
</style>
