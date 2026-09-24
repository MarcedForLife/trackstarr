<script lang="ts">
	import type { Snippet } from 'svelte';
	import { arrival, COVER_FADE, type Arrival } from '$lib/covers';
	import { display } from '$lib/display.svelte';
	import { coverPalette, NEUTRAL_PALETTE } from '$lib/poster-palette';

	// The artwork the field turns and lights, for a grid card and the sheet's
	// header. What a card says and what a press does is PosterCard's.
	let {
		// The cover, or nothing for a title with none: the initials stand in.
		src,
		mark,
		// The frame's box: a poster's 2:3 in the grid, the header's own in the sheet.
		box = 'aspect-[2/3] w-full',
		rounded = 'rounded-xl',
		border = 'border-line',
		// The turned element, for a caller hit-testing a gesture against it.
		frame = $bindable(null),
		// What sits over the artwork: badges, a selection tick.
		children
	}: {
		src?: string;
		mark: string;
		box?: string;
		rounded?: string;
		border?: string;
		frame?: HTMLElement | null;
		children?: Snippet;
	} = $props();

	// The only state Svelte owns. Everything the pointer drives is written onto
	// the element by the field: hundreds of cards cannot re-render on pointermove.
	let missing = $state(false);
	// How the cover got here, and whether the tile it fades off is still
	// mounted.
	let cover = $state<Arrival>('coming');
	let palette = $state({ ...NEUTRAL_PALETTE });
	let tiled = $state(true);

	const plain = $derived(!src || missing || display.art === 'hide');

	// The tile a card wears until its cover lands, and keeps instead of one that
	// never arrives, so a poster that fails to load changes nothing but stays.
	const TILE =
		'flex h-full w-full items-center justify-center bg-raised text-lg font-semibold text-faint';
</script>

<!-- data-tilt is how the field finds the element it turns. --fx is the tilt
     multiplier the lift, shadow and keystone read, and --glow the sheen
     strength the finish reads, set here because the properties they feed are
     `inherits: false`. -->
<span
	bind:this={frame}
	data-tilt
	style:--fx={display.strength}
	style:--glow={display.glow}
	class={`relative block ${box}`}
>
	<!-- The cover is this element's bottom background layer; shade and sheen are
	     blended on top at paint time. A blended element over the artwork made
	     the card a render surface, which a per-frame transform stretches from
	     one texture: jagged corners and a hard shadow edge while turning. A
	     background blend is settled at raster, and costs fewer layers.
	     data-sheen is where the field writes the light; the properties are
	     `inherits: false`, so the write lands on one element. -->
	<span
		data-sheen
		style:--poster-hue={palette.hue}
		style:--poster-accent-hue={palette.accentHue}
		style:--poster-accent-saturation={`${palette.accentSaturation}%`}
		style:--poster-saturation={`${palette.saturation}%`}
		style:--cover={plain ? undefined : `url("${src}")`}
		class={`art absolute inset-0 block overflow-hidden border bg-sunken ${rounded} ${border} ${
			display.lights ? `is-${display.sheen}` : ''
		}`}
	>
		{#if plain}
			<span class={`${TILE} relative`}>
				{mark}
				<span class="scrim pointer-events-none absolute inset-x-0 bottom-0 block h-1/2"></span>
			</span>
		{:else}
			<!-- The same cover, never seen: a background image cannot say it
			     failed or landed, so this hidden element fetches it, reports
			     both, and carries the lazy load. data-cover lets $lib/covers
			     call it off on navigation. -->
			<img
				data-cover
				{src}
				alt=""
				width="500"
				height="750"
				loading="lazy"
				decoding="async"
				draggable="false"
				onload={() => {
					cover = arrival(src!);
					const asked = src!;
					void coverPalette(asked).then((found) => {
						if (src === asked) palette = found;
					});
				}}
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
					class={`${TILE} ${COVER_FADE} absolute inset-0 ${cover === 'fading' ? 'opacity-0' : ''}`}
				>
					{mark}
					<span class="scrim pointer-events-none absolute inset-x-0 bottom-0 block h-1/2"></span>
				</span>
			{/if}
		{/if}

		{@render children?.()}
	</span>
</span>

<style>
	/* The dark ramp the verdict and badges print on: 85% black at the bottom
	   to nothing at the top. A 1x256 PNG rather than a CSS gradient, because
	   Chrome re-dithers a gradient each time a card gains or loses its
	   composited layer, which read as a twitch across the bottom of every card
	   the pointer had passed. A picture comes back identical on both sides. */
	.art {
		--scrim: url('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAEACAYAAAByPhyYAAABf0lEQVR42j3E03IYABRAwaZ2aiO1baa2bdu2bdu2bdvG37T7cs/M2YQU/0twkBKpgtRIg7RBOqQPMiAjMgWZkSXIisQgG7IHOZATuYLcyBPkRT7kDwqgYFAIhYMiKIqkoBiKByVQMiiF0igTlEW5oDwqBBVRKaiMKqgaVEP1oAZqolZQG3WCuqgX1EeDoCEaoXGQjCZBUzRD86AFWgat0Dpog7ZoF7RHh6AjOgWd0QVdg27oHvRAz6AXegd90Bf90B8DMBCDMBhDMBTDMBwjMBKjMBpjMBbjMB4TMBGTMBlTMBXTMB0zMBOzMBtzMBfzMB8LsBCLsBhLsBTLsBwrsBKrsBprsBbrsB4bsBGbsBlbsBXbsB07sBO7sBt7sBf7sB8HcBCHcBhHcBTHcBwncBKncBpncBbncB4XcBGXcBlXcBXXcB03cBO3cBt3cBf3cB8P8BCP8BhP8BTP8Bwv8BKv8Bpv8Bbv8B4f8BGf8Blf8BXf8B0/8BO/8Bt/8Pcfnulj/I+bvvUAAAAASUVORK5CYII=');
	}

	.scrim {
		background-image: var(--scrim);
		background-size: 100% 100%;
	}
</style>
