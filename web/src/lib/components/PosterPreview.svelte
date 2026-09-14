<script lang="ts">
	import PosterCard from '$lib/components/PosterCard.svelte';
	import { display } from '$lib/display.svelte';
	import { tiltField, type Driver } from '$lib/field';
	import type { Card } from '$lib/library';
	import { moving } from '$lib/motion.svelte';

	// The settings under this strip govern something that only happens under a
	// pointer, so five posters come here to demonstrate it. The real PosterCard
	// and field, so it cannot drift from the grid. The raise is left to a real
	// finger: a driven pointer stopping dead read as the page freezing.

	// An abstract poster: real covers would read as titles being talked about.
	function art(from: string, to: string, horizon: number): string {
		const svg =
			`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 750">` +
			`<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">` +
			`<stop offset="0" stop-color="${from}"/><stop offset="1" stop-color="${to}"/>` +
			`</linearGradient></defs>` +
			`<rect width="500" height="750" fill="url(#g)"/>` +
			`<circle cx="250" cy="286" r="132" fill="#fff" opacity=".14"/>` +
			`<path d="M0 ${horizon} L500 ${horizon - 96} V750 H0 Z" fill="#000" opacity=".22"/>` +
			`</svg>`;
		// Encoded rather than base64'd: an SVG this small is shorter as text,
		// and it stays readable in the element inspector.
		return `data:image/svg+xml,${encodeURIComponent(svg)}`;
	}

	// Five, the fewest that make narrow and wide spread different pictures. Short
	// names, and no series, whose label wraps.
	const samples: { card: Card; src: string }[] = [
		{
			card: { id: 'p1', name: 'Aurora', kind: 'movie', state: 'unchecked', year: 2019, files: 1 },
			src: art('#3f5efb', '#6a3de8', 470)
		},
		{
			card: { id: 'p2', name: 'Beacon', kind: 'movie', state: 'unchecked', year: 2021, files: 1 },
			src: art('#0f7a6e', '#134e5e', 430)
		},
		{
			card: { id: 'p3', name: 'Cinder', kind: 'movie', state: 'unchecked', year: 2014, files: 1 },
			src: art('#c2410c', '#7c2d12', 505)
		},
		{
			card: {
				id: 'p4',
				name: 'Driftwood',
				kind: 'movie',
				state: 'unchecked',
				year: 2016,
				files: 1
			},
			src: art('#475569', '#1e293b', 445)
		},
		{
			card: { id: 'p5', name: 'Ember', kind: 'movie', state: 'unchecked', year: 2023, files: 1 },
			src: art('#be185d', '#6d28d9', 490)
		}
	];

	// The library's own condition, so the two agree.
	const tilting = moving;

	let stage: HTMLElement | null = $state(null);
	let strip: HTMLElement | null = $state(null);
	let ghost: HTMLElement | null = $state(null);
	let driver: Driver | null = null;
	let frame = 0;

	// One pass across the row, slow enough that a card is seen turning: the tilt
	// eases over 420ms.
	const PASS = 2800;

	// How many times the path crosses the middle, and how far it strays as a
	// fraction of poster height, so both tilt axes show.
	const WAVES = 3;
	const STRAY = 0.32;

	// The share of the pass spent approaching and leaving the row at each end.
	const LEAD = 0.13;

	function hide() {
		ghost?.style.setProperty('--on', '0');
	}

	// Every way a pass ends goes through here, so a row is never left leaning at
	// a pointer that has gone. Except a handover to a real pointer: putting the
	// field down there snapped the row flat for a frame before the reader's own
	// move reached it.
	function stop(handing = false) {
		if (frame) cancelAnimationFrame(frame);
		frame = 0;
		if (waiting !== null) {
			clearTimeout(waiting);
			waiting = null;
		}
		if (!handing) driver?.away();
	}

	// Three waits on one timer, since all end with a pass starting and only the
	// last asked for should run.
	//
	// After the last setting change, so dragging the slider through four stops is
	// one pass.
	const SETTLE = 320;
	// After the reader has finished with the strip: plainly a resumption, not the
	// row twitching as a finger lifts.
	const RESUME = 4000;
	// Between passes: a clear gap, so each reads as a separate demonstration.
	const REPLAY = 1600;

	let waiting: ReturnType<typeof setTimeout> | null = null;
	// Whether a real pointer is on the strip. A parked cursor sends no events, so
	// the timer alone would walk the pass back under it.
	let theirs = false;
	// Whether the strip is on screen; a loop nobody sees is five composited cards
	// a frame for nothing.
	let watched = true;

	function again(delay: number) {
		if (waiting !== null) clearTimeout(waiting);
		waiting = setTimeout(() => {
			waiting = null;
			// Neither reschedules: whatever makes it true starts this clock itself.
			if (!theirs && watched) sweep();
		}, delay);
	}

	// A pointer nobody is holding, walked across the row. This is why the strip
	// works on a phone, which has no hover.
	function sweep() {
		stop();
		if (!strip || !driver || !tilting()) return;
		const first = strip.getBoundingClientRect();
		if (!first.width) return;
		// How far outside the row the pass begins and ends, from the lead.
		const lead = first.width * (LEAD / (1 - 2 * LEAD));
		// The artwork's box, not the strip's, which includes the labels. Kept as
		// offsets down the strip: the strip itself is measured every frame, since
		// a scroll or a group folding above moves it mid-pass, and the aim must
		// move with the ghost.
		const art = strip.querySelector<HTMLElement>('[data-tilt]')?.getBoundingClientRect() ?? first;
		const midY = art.top - first.top + art.height / 2;
		const stray = art.height * STRAY;
		const began = performance.now();
		const step = (now: number) => {
			if (!strip) return;
			const box = strip.getBoundingClientRect();
			const t = Math.min(1, (now - began) / PASS);
			// Eased across the row, linear past either end, so the slow stretch is
			// not spent where there is nothing to look at.
			const inner = (t - LEAD) / (1 - 2 * LEAD);
			const along = inner <= 0 || inner >= 1 ? inner : inner * inner * (3 - 2 * inner);
			const x = along * box.width;
			// Whole half-turns on the clock, so the pointer leaves and arrives at
			// the middle.
			const y = midY + Math.sin(t * Math.PI * WAVES) * stray;
			// The field wants viewport coordinates.
			driver?.aim(box.left + x, box.top + y);
			// Written onto the element, not through state, at sixty a second.
			if (ghost) {
				ghost.style.setProperty('--cx', `${x}px`);
				ghost.style.setProperty('--cy', `${y}px`);
				// Full over the row, faded only in the lead.
				const outside = Math.max(0, -x, x - box.width);
				ghost.style.setProperty('--on', `${Math.max(0, 1 - outside / lead)}`);
			}
			if (t < 1) {
				frame = requestAnimationFrame(step);
				return;
			}
			frame = 0;
			hide();
			driver?.away();
			// `again` checks the reader has not taken the strip over.
			again(REPLAY);
		};
		frame = requestAnimationFrame(step);
	}

	// A real pointer on the strip, pressing or merely crossing: the pass gives
	// way rather than arguing with it, and comes back a few seconds after.
	function taken() {
		theirs = true;
		if (frame) {
			stop(true);
			hide();
		}
		// Also keeps the clock from expiring mid-press.
		again(RESUME);
	}

	// Their pointer off the strip. The wait starts here, so the strip stays
	// theirs while they are on it.
	function gone() {
		theirs = false;
		again(RESUME);
	}

	// Bound here because the strip is aria-hidden and a markup handler wants a
	// role. On the stage, not the window: the click that changed the setting
	// moved a real cursor too.
	$effect(() => {
		const on = stage;
		if (!on) return;
		// pointermove, so a cursor crossing gives the pass away; touchend too,
		// since a finger's `pointerleave` only arrives where pointer events did.
		on.addEventListener('pointerenter', taken, { passive: true });
		on.addEventListener('pointermove', taken, { passive: true });
		on.addEventListener('pointerdown', taken, { passive: true });
		on.addEventListener('touchstart', taken, { passive: true });
		on.addEventListener('touchmove', taken, { passive: true });
		on.addEventListener('pointerleave', gone, { passive: true });
		on.addEventListener('touchend', gone, { passive: true });
		on.addEventListener('touchcancel', gone, { passive: true });
		return () => {
			on.removeEventListener('pointerenter', taken);
			on.removeEventListener('pointermove', taken);
			on.removeEventListener('pointerdown', taken);
			on.removeEventListener('touchstart', taken);
			on.removeEventListener('touchmove', taken);
			on.removeEventListener('pointerleave', gone);
			on.removeEventListener('touchend', gone);
			on.removeEventListener('touchcancel', gone);
		};
	});

	// The strip leaving the page, and coming back to it.
	$effect(() => {
		const on = stage;
		if (!on) return;
		const seen = new IntersectionObserver(([entry]) => {
			watched = entry.isIntersecting;
			if (watched) {
				again(SETTLE);
				return;
			}
			stop();
			hide();
		});
		seen.observe(on);
		// The first answer comes while the disclosure around the strip is still
		// opening, with the strip clipped out of sight, and Firefox says nothing
		// more when that animation ends: the pass never started. Its end bubbles
		// to `.reveal`, and observing afresh asks again, as $lib/reveal does.
		const reveal = on.closest('.reveal');
		const rearm = () => {
			seen.unobserve(on);
			seen.observe(on);
		};
		reveal?.addEventListener('animationend', rearm);
		return () => {
			reveal?.removeEventListener('animationend', rearm);
			seen.disconnect();
		};
	});

	// What the current pass demonstrates, so a re-render changing nothing does
	// not restart it.
	let showing = '';

	// On mount and whenever one of the three motion settings changes. Not the art
	// setting, which shows standing still.
	$effect(() => {
		const next = `${display.effects}/${display.spread}/${display.sheen}`;
		if (next !== showing) {
			showing = next;
			// The pass on screen demonstrates the old setting. The ghost goes too,
			// or it freezes mid-row.
			stop();
			hide();
			again(SETTLE);
		}
		return stop;
	});
</script>

<!-- Hidden from the accessibility tree: the rows underneath say it in words. -->
<div class="preview" aria-hidden="true">
	<p class="mb-2 text-[11px] font-medium tracking-wide text-faint uppercase">Preview</p>
	<!-- The stage holds the ghost, which cannot be in the row: the field takes
	     every child for a card. -->
	<div bind:this={stage} class="stage">
		<ul
			bind:this={strip}
			use:tiltField={{
				active: tilting,
				reach: () => display.reach,
				strength: () => display.strength,
				lights: () => display.lights,
				drive: (handle) => (driver = handle)
			}}
			class="row grid grid-cols-5 gap-x-3"
		>
			{#each samples as sample (sample.card.id)}
				<li class="tile">
					<PosterCard
						card={sample.card}
						src={sample.src}
						tabbable={false}
						verdict={false}
						onopen={() => {}}
					/>
				</li>
			{/each}
		</ul>
		<span bind:this={ghost} class="ghost"></span>
	</div>
	<p class="mt-3 text-[12px] leading-snug text-pretty text-dim">
		{#if !tilting()}
			Posters stay flat.
		{:else}
			A poster leans toward a passing pointer and catches the light as it turns. Its neighbours lean
			with it as far as the spread reaches. Holding one lifts it out of the row, which takes a real
			finger.
		{/if}
	</p>
</div>

<style>
	.preview {
		padding: 0.75rem 0 0.25rem;
	}

	/* Room for a turned card's keystone, given as padding and taken back as
	   margin so the pitch matches the grid. */
	.tile {
		padding: 6px;
		margin: -6px;
	}

	/* Nothing may clip: the lifted card grows 6% and casts 34px. */
	.row {
		overflow: visible;
	}

	/* What the ghost's coordinates are measured against; the row is its only
	   laid-out child, so the boxes coincide. */
	.stage {
		position: relative;
	}

	/* The driven pointer, made visible, so the lean has something to follow.
	   Above a raised card's own z-index. */
	.ghost {
		position: absolute;
		top: 0;
		left: 0;
		z-index: 2;
		height: 26px;
		width: 26px;
		margin: -13px 0 0 -13px;
		border-radius: 9999px;
		pointer-events: none;
		/* A ring with a core, so it does not hide the poster turning under it.
		   White edged in black rather than the accent, which vanished against
		   an orange poster: this passes over artwork, not the page. */
		border: 2px solid rgb(255 255 255 / 0.92);
		background: radial-gradient(
			circle,
			rgb(255 255 255 / 0.95) 0 16%,
			rgb(255 255 255 / 0.1) 28% 100%
		);
		box-shadow:
			0 0 0 1px rgb(0 0 0 / 0.38),
			inset 0 0 0 1px rgb(0 0 0 / 0.22),
			0 2px 8px rgb(0 0 0 / 0.35);
		opacity: var(--on, 0);
		/* No transition on the position, which is written every frame. */
		translate: var(--cx, 0) var(--cy, 0);
		transition: opacity 180ms ease-out;
	}

	/* No pass runs under reduced motion, so a parked ghost is a smudge. */
	@media (prefers-reduced-motion: reduce) {
		.ghost {
			display: none;
		}
	}
</style>
