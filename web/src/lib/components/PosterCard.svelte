<script lang="ts">
	import { arrival, COVER_FADE, type Arrival } from '$lib/covers';
	import { display } from '$lib/display.svelte';
	import { pressing } from '$lib/field';
	import { coverUrl, dot, initials, VERDICTS, verdictLabel, type Card } from '$lib/library';
	import Mark from '$lib/components/Mark.svelte';
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

	// The only state Svelte owns. Everything the pointer drives is written onto
	// the element by the field: hundreds of cards cannot re-render on pointermove.
	let missing = $state(false);
	// Whether lifting now would pick the card: the one piece of a press drawn.
	let armed = $state(false);
	// How the cover got here, and whether the tile it fades off is still
	// mounted.
	let cover = $state<Arrival>('coming');
	let tiled = $state(true);

	let frame: HTMLElement | null = $state(null);

	// The effects slider at Off. Reduced motion is answered by the field and the
	// stylesheet.
	const flat = $derived(display.strength === 0);

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
		onarm: (on: boolean) => (armed = on)
	});

	const mark = $derived(initials(card.name));

	// The artwork: a title's own poster, or the one an illustration was handed.
	const art = $derived(src ?? coverUrl(card.id));

	// The tile a card wears until its cover lands, and keeps instead of one that
	// never arrives, so a poster that fails to load changes nothing but stays.
	const TILE =
		'flex h-full w-full items-center justify-center bg-raised text-lg font-semibold text-faint';

	// The layouts a rewrite would add and rebuild, told apart by fill: green for
	// a gain, accent for a rebuild. A rebuild is one change, not a gain plus a
	// drop.
	const written = $derived([
		...(card.adds ?? []).map((layout) => ({ chip: `+${layout}`, tone: 'bg-ok/90 text-on-ok' })),
		...(card.rebuilds ?? []).map((layout) => ({
			chip: layout,
			tone: 'bg-accent-fill/90 text-on-accent'
		}))
	]);

	// A title trackstarr has rewritten reads Passed like any other, so the mark
	// beside the word is the only thing saying its files are the ones we made.
	// How much of the title that is goes in the label: a number on the card was
	// noise on a film, always one, and no fraction on a series.
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
			card.files ? `${card.files} file${card.files === 1 ? '' : 's'}` : ''
		]
			.filter(Boolean)
			.join(' · ')
	);
</script>

<button
	type="button"
	tabindex={tabbable ? undefined : -1}
	use:pressGesture={gesture}
	class:is-flat={flat}
	class:pans-both={pan === 'both'}
	class="poster group block w-full text-left"
	aria-pressed={picking ? selected : undefined}
	aria-label={`${card.name}${card.year ? ` (${card.year})` : ''} — ${spread}`}
>
	<!-- data-tilt is how the field finds the element it turns; `frame` is scoped.
	     --fx is the effects multiplier the lift, shadow and keystone read, set
	     here because the properties it feeds are `inherits: false`. -->
	<span
		bind:this={frame}
		data-tilt
		style:--fx={display.strength}
		class="frame relative block aspect-[2/3] w-full"
	>
		<!-- The cover is this element's bottom background layer and the sheen is
		     blended on top at paint time. A blended element over the artwork made
		     the card a render surface, which a per-frame transform stretches from
		     one texture: jagged corners and a hard shadow edge while turning. A
		     background blend is settled at raster, and costs fewer layers.
		     data-sheen is where the field writes the light; the properties are
		     `inherits: false`, so the write lands on one element. -->
		<span
			data-sheen
			style:--cover={missing || display.art === 'hide' ? undefined : `url("${art}")`}
			class={`art absolute inset-0 block overflow-hidden rounded-xl border bg-sunken ${
				flat || !display.lights ? '' : `is-${display.sheen}`
			} ${selected || armed ? 'border-accent-fill' : 'border-line'}`}
		>
			{#if missing || display.art === 'hide'}
				<span class={TILE}>{mark}</span>
			{:else}
				<!-- The same cover, never seen: a background image cannot say it
				     failed or landed, so this hidden element fetches it, reports
				     both, and carries the lazy load. data-cover lets $lib/covers
				     call it off on navigation. -->
				<img
					data-cover
					src={art}
					alt=""
					width="500"
					height="750"
					loading="lazy"
					decoding="async"
					draggable="false"
					onload={() => (cover = arrival(art))}
					onerror={() => (missing = true)}
					class="invisible h-full w-full object-cover"
				/>
				{#if tiled && cover !== 'seen'}
					<!-- A background layer cannot fade, so the tile over it fades off
					     instead. Under the scrim and the badges, which say what the
					     title is and how it fared while the artwork is still coming.
					     Dropped once faded, or every card in the grid would keep a
					     spare layer for a fade that is over. -->
					<span
						aria-hidden="true"
						ontransitionend={() => (tiled = false)}
						class={`${TILE} ${COVER_FADE} absolute inset-0 ${
							cover === 'fading' ? 'opacity-0' : ''
						}`}
					>
						{mark}
					</span>
				{/if}
			{/if}

			<span class="scrim pointer-events-none absolute inset-x-0 bottom-0 block h-1/2"></span>
			{#if verdict}
				<span class="pointer-events-none absolute inset-x-2 bottom-2 block">
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
						<!-- Our own star after the word: this title is as it should be,
						     and trackstarr made it so. In Passed's green, since accent on
						     a card means work outstanding. -->
						{#if rewritten}
							<Mark size={11} class="flex-none text-ok" />
						{/if}
					</span>
					{#if written.length || card.drops}
						<span class="mt-1 flex flex-wrap gap-1">
							{#each written as change (change.chip)}
								<span
									class={`rounded px-1 py-px font-mono text-[10px] leading-tight ${change.tone}`}
								>
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
				</span>
			{/if}

			<!-- The tick and the ring where it would land, only while selecting. Top
			     right, clear of the verdict and a poster's own title; a dark disc
			     so a white ring shows on a pale poster. Armed wears it filled too,
			     so the release reads as finishing the gesture. -->
			{#if picking || armed}
				<span
					aria-hidden="true"
					class={`pointer-events-none absolute top-2 right-2 flex h-5 w-5 items-center justify-center rounded-full border transition-colors ${
						selected || armed
							? 'border-accent-fill bg-accent-fill text-on-accent'
							: 'border-white/75 bg-black/45 text-transparent'
					}`}
				>
					<svg
						viewBox="0 0 12 12"
						width="9"
						height="9"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path d="M2.5 6.3 4.9 8.7 9.5 3.6" />
					</svg>
				</span>
			{/if}
		</span>
	</span>

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

	/* The dark ramp the verdict and badges print on: 85% black at the bottom
	   to nothing at the top. A 1x256 PNG rather than a CSS gradient, because
	   Chrome re-dithers a gradient each time a card gains or loses its
	   composited layer, which read as a twitch across the bottom of every card
	   the pointer had passed. A picture comes back identical on both sides. */
	.scrim {
		background-image: url('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAEACAYAAAByPhyYAAABf0lEQVR42j3E03IYABRAwaZ2aiO1baa2bdu2bdu2bdvG37T7cs/M2YQU/0twkBKpgtRIg7RBOqQPMiAjMgWZkSXIisQgG7IHOZATuYLcyBPkRT7kDwqgYFAIhYMiKIqkoBiKByVQMiiF0igTlEW5oDwqBBVRKaiMKqgaVEP1oAZqolZQG3WCuqgX1EeDoCEaoXGQjCZBUzRD86AFWgat0Dpog7ZoF7RHh6AjOgWd0QVdg27oHvRAz6AXegd90Bf90B8DMBCDMBhDMBTDMBwjMBKjMBpjMBbjMB4TMBGTMBlTMBXTMB0zMBOzMBtzMBfzMB8LsBCLsBhLsBTLsBwrsBKrsBprsBbrsB4bsBGbsBlbsBXbsB07sBO7sBt7sBf7sB8HcBCHcBhHcBTHcBwncBKncBpncBbncB4XcBGXcBlXcBXXcB03cBO3cBt3cBf3cB8P8BCP8BhP8BTP8Bwv8BKv8Bpv8Bbv8B4f8BGf8Blf8BXf8B0/8BO/8Bt/8Pcfnulj/I+bvvUAAAAASUVORK5CYII=');
		background-size: 100% 100%;
	}

	/* Flat and 2D: a keyboard points at nothing, so no angle and no layer. The
	   rest of .frame is in $lib/poster.css beside the field that drives it. */
	.poster:focus-visible .frame {
		transform: scale(1.04);
	}
</style>
