<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { resolve } from '$app/paths';
	import { fade } from 'svelte/transition';
	import Count from '$lib/components/Count.svelte';
	import PosterCard from '$lib/components/PosterCard.svelte';
	import { display } from '$lib/display.svelte';
	import { dragScroll } from '$lib/dragscroll';
	import { tiltField } from '$lib/field';
	import { getSummary, type Card, type Summary } from '$lib/library';
	import { moving } from '$lib/motion.svelte';
	import { order } from '$lib/order.svelte';
	import { poll } from '$lib/poll';

	// The library in one line over a row of the titles trackstarr last had open.
	// Owns the summary, the poll that re-reads it under a sweep, and the row's
	// scroll position.
	let {
		seed,
		onopen
	}: {
		/** The summary the loader read, or null when no *arr answered. */
		seed: Summary | null;
		/** A poster's tap opens the sheet the page holds. */
		onopen: (card: Card) => void;
	} = $props();

	// Null when the library could not be read; the section goes rather than
	// showing zeros. Written over by the poll, reset by a navigation.
	let library = $derived(seed);

	// The one number that says whether the library is as it should be.
	const pending = $derived(library?.counts['pending'] ?? 0);

	// How long an arriving poster fades in. The whole fade, since the cards it
	// displaces arrive in place rather than sliding; see the row below.
	const ARRIVE_MS = 200;

	// Whether the strip has been drawn once, so the first pass does not fade in.
	// Set on mount: the params below are read as each card is created.
	let drawn = $state(false);
	onMount(() => {
		drawn = true;
	});

	// Opacity only. No out: a leaving card would hold its width while it faded.
	const arrive = $derived({ duration: drawn && moving() ? ARRIVE_MS : 0 });

	// The row, so a redraw can put it back where the reader was.
	let strip = $state<HTMLUListElement>();

	// Whether the strip shows its start, with one card of slack.
	function atStart(): boolean {
		if (!strip) return false;
		const first = strip.firstElementChild;
		return strip.scrollLeft < (first instanceof HTMLElement ? first.offsetWidth : 0);
	}

	// Whether the reader is moving the row themselves. Touch events, not pointer:
	// the browser cancels the pointer once a touch becomes a pan.
	let fingers = 0;
	let handled = 0;

	// How long after the last movement the row still counts as theirs.
	const COAST_MS = 400;

	function driven(): boolean {
		return fingers > 0 || Date.now() - handled < COAST_MS;
	}

	$effect(() => {
		const row = strip;
		if (!row) return;
		const counted = (event: TouchEvent) => {
			fingers = event.touches.length;
			handled = Date.now();
		};
		const moved = () => (handled = Date.now());
		const passive = { passive: true } as const;
		row.addEventListener('touchstart', counted, passive);
		row.addEventListener('touchend', counted, passive);
		row.addEventListener('touchcancel', counted, passive);
		row.addEventListener('scroll', moved, passive);
		return () => {
			row.removeEventListener('touchstart', counted);
			row.removeEventListener('touchend', counted);
			row.removeEventListener('touchcancel', counted);
			row.removeEventListener('scroll', moved);
		};
	});

	// Which ends have covers under the rim, for its shadow. Measured in script
	// since Firefox has no scroll timelines.
	let underStart = $state(false);
	let underEnd = $state(false);

	function measureEdges() {
		if (!strip) return;
		underStart = strip.scrollLeft > 1;
		underEnd = strip.scrollLeft < strip.scrollWidth - strip.clientWidth - 1;
	}

	$effect(() => {
		const row = strip;
		if (!row) return;
		const resized = new ResizeObserver(measureEdges);
		resized.observe(row);
		row.addEventListener('scroll', measureEdges, { passive: true });
		return () => {
			resized.disconnect();
			row.removeEventListener('scroll', measureEdges);
		};
	});

	// The cards set the row's scroll width.
	$effect(() => {
		if (library?.head) tick().then(measureEdges);
	});

	// Back to the front once the new row is laid out. Twice, since the browser
	// may move the row at layout, after tick().
	async function toStart() {
		await tick();
		if (strip) strip.scrollLeft = 0;
		requestAnimationFrame(() => {
			if (strip) strip.scrollLeft = 0;
		});
	}

	// Re-read the strip and tally, quietly.
	async function restock() {
		try {
			const fetched = await getSummary(fetch, order.strip);
			// Read before the redraw, which moves the row. Only a new leading card
			// is worth following, and never one the reader has hold of.
			const follow = fetched.head[0]?.id !== library?.head[0]?.id && atStart() && !driven();
			library = fetched;
			if (follow) toStart();
		} catch {
			/* the shelf on screen is still the last thing anyone knew */
		}
	}

	// How often the strip is re-read under a run; see _PUBLISH_SECONDS.
	const SHELF_MS = 5000;

	// No rhythm of its own: read when the service says so and when the panel
	// notices a run moved. The gap holds a sweep to a redrawable pace.
	const posters = poll({ ask: restock, gap: SHELF_MS, kinds: ['library'] });

	onDestroy(posters.stop);

	/** Read the shelf again. `urgent` for a run that just ended. */
	export function look(urgent = false) {
		if (urgent) posters.now();
		else posters.prod();
	}
</script>

{#if library?.titles}
	<section class="min-w-0" aria-labelledby="library-heading">
		<div class="flex items-center gap-3 px-3 pb-3 sm:px-4">
			<div class="min-w-0 flex-1">
				<h2 id="library-heading" class="text-[17px] leading-tight font-semibold tracking-tight">
					Library
				</h2>
				<p class="mt-1 text-[12.5px] text-dim">
					<Count value={library.titles} />
					{library.titles === 1 ? 'title' : 'titles'} ·
					<span class={pending ? 'font-medium text-accent' : ''}
						>{#if pending}<Count value={pending} /> pending{:else}nothing pending{/if}</span
					>
				</p>
			</div>
			<a
				href={resolve('/library')}
				class="relative -mr-1.5 flex-none rounded px-1.5 py-1.5 text-[12px] font-medium text-accent after:absolute after:-inset-2 after:content-[''] hover:underline"
			>
				View library
			</a>
		</div>

		{#if library.head.length}
			<div
				class="shelf relative overflow-hidden rounded-2xl border border-line bg-sunken"
				class:under-start={underStart}
				class:under-end={underEnd}
			>
				<!-- This browser's strip order, since worst-first put the same stuck titles
				     up front every day. No scroll snap, since a snapped strip does not
				     fling. The vertical padding leaves room for a raised card's shadow,
				     since a scroller clips at its padding box. -->
				<ul
					use:tiltField={{
						active: moving,
						reach: () => display.reach,
						strength: () => display.strength,
						lights: () => display.lights
					}}
					use:dragScroll
					bind:this={strip}
					class="strip flex gap-2 overflow-x-auto p-3 sm:p-4"
				>
					{#each library.head as title (title.id)}
						<!-- Bigger art where there is room. Twelve at 8.5rem still
						     overflow 1200px, so the row keeps cutting a card off. No
						     animate:flip: a transform on a card mid-restock takes the
						     phone's document scroll to the top. -->
						<li in:fade={arrive} class="w-[5.25rem] flex-none lg:w-[7rem] xl:w-[8.5rem]">
							<PosterCard pan="both" lift={false} card={title} {onopen} />
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</section>
{/if}

<style>
	/* No scrollbar: a card cut off at the edge says the same, the cursor drags,
	   and a keyboard scrolls it. overscroll-behavior stops a drag past the end
	   becoming Chrome's back navigation. */
	.strip {
		scrollbar-width: none;
		overscroll-behavior-x: contain;
	}

	.strip::-webkit-scrollbar {
		display: none;
	}

	/* The rim's shadow on covers under it, a quarter of a card wide. */
	.shelf {
		isolation: isolate;
	}

	/* Over a raised card's z-index of 1. */
	.shelf::before,
	.shelf::after {
		content: '';
		position: absolute;
		inset-block: 0;
		z-index: 2;
		width: 1.25rem;
		pointer-events: none;
		opacity: 0;
		transition: opacity 150ms ease-out;
	}

	.shelf::before {
		left: 0;
		background: linear-gradient(to right, var(--rim-shadow), 30%, transparent);
	}

	.shelf::after {
		right: 0;
		background: linear-gradient(to left, var(--rim-shadow), 30%, transparent);
	}

	.under-start::before,
	.under-end::after {
		opacity: 1;
	}

	/* The cards' lg and xl widths. */
	@media (min-width: 64rem) {
		.shelf::before,
		.shelf::after {
			width: 1.75rem;
		}
	}

	@media (min-width: 80rem) {
		.shelf::before,
		.shelf::after {
			width: 2rem;
		}
	}
</style>
