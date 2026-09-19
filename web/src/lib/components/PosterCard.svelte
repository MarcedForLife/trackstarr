<script lang="ts">
	import { pressing } from '$lib/field';
	import {
		changed,
		coverUrl,
		dot,
		initials,
		VERDICTS,
		verdictLabel,
		type Card
	} from '$lib/library';
	import PosterArt from '$lib/components/PosterArt.svelte';
	import Tick from '$lib/components/Tick.svelte';
	import { pressGesture } from '$lib/press';

	let {
		card,
		onopen,
		// Which way the browser may pan a finger on this card: the page only, or
		// also a row that scrolls inside itself.
		pan = 'y',
		// The artwork, for a card that stands for a title rather than being one.
		src,
		// Off for an illustration of a card; the tilt still answers a finger.
		tabbable = true,
		// The verdict and change badges across the bottom. Off at sizes where
		// "Unsupported" is "Uns…".
		verdict = true,
		// Whether this card is selected, and by being defined at all, whether a
		// tap selects. The tap still arrives through `onopen`; the grid decides.
		selected,
		// A long press released in place, as phone galleries select. Absent means
		// no long press.
		onhold,
		// Whether holding this card raises it. Off for the strip, which has no long
		// press for the raise to promise.
		lift = true
	}: {
		card: Card;
		onopen: (card: Card) => void;
		onhold?: (card: Card) => void;
		pan?: 'y' | 'both';
		src?: string;
		tabbable?: boolean;
		verdict?: boolean;
		selected?: boolean;
		lift?: boolean;
	} = $props();

	const picking = $derived(selected !== undefined);

	// Whether lifting now would pick the card: the one piece of a press drawn.
	// The artwork's own state is PosterArt's.
	let armed = $state(false);

	// The turned element, for the gesture's hit test.
	let frame: HTMLElement | null = $state(null);

	// Whether a point is on this card, measured on the turned card.
	function within(x: number, y: number) {
		const box = frame?.getBoundingClientRect();
		if (!box) return false;
		return x >= box.left && x <= box.right && y >= box.top && y <= box.bottom;
	}

	// The gesture is $lib/press's; what a card does with it is here. Raising is
	// a press for both pointers, not a hover, or a mouse crossing a row raised
	// every card. Which card is raised is the field's to say, so a dragged press
	// carries the raise along; the press goes to `pressing`. The long press is
	// offered only outside a selection, where a press already selects.
	const gesture = $derived({
		ontap: () => onopen(card),
		onhold: () => onhold?.(card),
		canHold: () => !!onhold && !picking,
		within,
		onpress: lift ? pressing : undefined,
		carries: true,
		onarm: (on: boolean) => (armed = on)
	});

	const mark = $derived(initials(card.name));

	// The artwork: a title's own poster, or the one an illustration was handed.
	const art = $derived(src ?? coverUrl(card.id));

	const written = $derived(changed(card));
	const changesLabel = $derived(
		[
			...written.map((change) => change.label),
			...(card.drops ? [`Drops ${card.drops} track${card.drops === 1 ? '' : 's'}`] : [])
		].join(', ')
	);

	// How much of the title we rewrote. Not drawn on the card, which said it with
	// our own star and read as a maker's mark; the word and the Modified filter
	// carry it, and `spread` puts this in the label.
	const rewritten = $derived.by(() => {
		const made = card.modified ?? 0;
		const files = card.files ?? 0;
		if (!made) return '';
		if (made < files) return `${made} of ${files} rewritten`;
		return files > 1 ? `all ${files} rewritten` : 'rewritten';
	});

	// A dot per state the title holds, worst first, so the leading dot is the
	// word beside it. Files that agree leave the one dot a card always had; a
	// mixed one gets up to four, since anything outstanding takes the headline
	// instead.
	const dots = $derived.by(() => {
		const held = card.counts;
		if (!held) return [card.state];
		const states = VERDICTS.filter((state) => (held[state] ?? 0) > 0);
		return states.length ? states : [card.state];
	});

	// The dots say which states in colour alone and the mark says only that we
	// rewrote something, so the label says both in words.
	const spread = $derived.by(() => {
		const parts =
			dots.length < 2
				? []
				: dots.map((state) => `${card.counts?.[state]} ${verdictLabel(state).toLowerCase()}`);
		if (rewritten) parts.push(rewritten);
		const word = verdictLabel(card.state);
		return parts.length ? `${word}: ${parts.join(', ')}` : word;
	});

	// The line under the name. Joined, since a series has no year.
	const sub = $derived(
		[
			card.year ?? (card.kind === 'series' ? 'Series' : ''),
			card.source,
			card.files ? `${card.files} file${card.files === 1 ? '' : 's'}` : '',
			card.variants ? `${card.variants} variants` : ''
		]
			.filter(Boolean)
			.join(' · ')
	);
</script>

<button
	type="button"
	tabindex={tabbable ? undefined : -1}
	use:pressGesture={gesture}
	class:pans-both={pan === 'both'}
	class="poster group block w-full text-left"
	aria-pressed={picking ? selected : undefined}
	aria-label={`${card.name}${card.year ? ` (${card.year})` : ''} — ${spread}${verdict && changesLabel ? `, ${changesLabel}` : ''}`}
	title={verdict && changesLabel ? changesLabel : undefined}
>
	<PosterArt
		src={art}
		{mark}
		border={selected || armed ? 'border-accent-fill' : 'border-line'}
		bind:frame
	>
		{#if verdict}
			<!-- The chips ride above the word rather than under it, so the word
		     lands on one line whatever a card has to say. -->
			<span class="pointer-events-none absolute inset-x-2 bottom-2 block">
				{#if written.length || card.drops}
					<span class="mb-1 flex flex-wrap gap-1">
						{#each written as change (change.chip)}
							<span class={`rounded px-1 py-px font-mono text-[10px] leading-tight ${change.tone}`}>
								{change.chip}
							</span>
						{/each}
						{#if card.drops}
							<span
								class="rounded bg-black/60 px-1 py-px font-mono text-[10px] leading-tight text-white/80"
							>
								−{card.drops}
							</span>
						{/if}
					</span>
				{/if}
				<span class="flex items-center gap-1.5" title={spread}>
					<!-- Half the gap between the dots that sits between them and the
				     word, so a run of them reads as one mark. -->
					<span class="flex flex-none items-center gap-0.5">
						{#each dots as state (state)}
							<span class={`h-1.5 w-1.5 rounded-full ${dot(state)}`}></span>
						{/each}
					</span>
					<span class="truncate text-[11px] font-medium text-white/70">
						{verdictLabel(card.state)}
					</span>
				</span>
			</span>
		{/if}

		<!-- The tick and the ring where it would land, only while selecting. Top
	     right, clear of the verdict and a poster's own title; a dark disc
	     so a white ring shows on a pale poster. Armed wears it filled too,
	     so the release reads as finishing the gesture. -->
		{#if picking || armed}
			<Tick
				on={selected || armed}
				off="border-white/75 bg-black/45"
				class="pointer-events-none absolute top-2 right-2"
			/>
		{/if}
	</PosterArt>

	<span class="mt-1.5 block truncate text-[12px] font-medium">{card.name}</span>
	<span class="block text-[11px] text-faint">{sub}</span>
</button>

<style>
	/* pan-y, not none: a vertical drag is the page scrolling. The rest stops a
	   phone selecting text and flashing a grey box on a held finger; the
	   long-press menu cannot be turned off from here on Chromium, so
	   $lib/press answers it. */
	.poster {
		touch-action: pan-y;
		user-select: none;
		-webkit-touch-callout: none;
		-webkit-tap-highlight-color: transparent;
	}

	/* In a self-scrolling row, both pans belong to the scrollers. */
	.poster.pans-both {
		touch-action: pan-x pan-y;
	}

	/* Flat and 2D: a keyboard points at nothing, so no angle and no layer. The
	   rest of the frame is in $lib/poster.css beside the field that drives it. */
	.poster:focus-visible :global([data-tilt]) {
		transform: scale(1.04);
	}
</style>
