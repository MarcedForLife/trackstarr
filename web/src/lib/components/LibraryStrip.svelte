<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { resolve } from '$app/paths';
	import { fade } from 'svelte/transition';
	import Glyph from '$lib/components/Glyph.svelte';
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
	<section class="min-w-0">
		<h2 class="text-[11px] font-semibold tracking-wider text-faint uppercase">Library</h2>
		<div class="mt-2.5 rounded-xl border border-line bg-raised p-4">
			<div class="flex items-baseline gap-2.5">
				<p class="min-w-0 flex-1 text-[13.5px] font-medium">
					{library.titles.toLocaleString()}
					{library.titles === 1 ? 'title' : 'titles'}
				</p>
				<p class={`flex-none text-[12px] font-medium ${pending ? 'text-accent' : 'text-faint'}`}>
					{pending ? `${pending.toLocaleString()} pending` : 'nothing pending'}
				</p>
			</div>

			<!-- This browser's strip order, not the shelf's worst-first, which put
			     the same stuck titles under the dashboard every day. No scroll snap:
			     a snapped container does not fling, and a flick that carried 700px
			     free carried 250 snapped. $lib/dragscroll settles a cursor drag by
			     hand. The vertical padding gives a raised card's shadow somewhere to
			     go, since a scroll container clips at its padding box. -->
			{#if library.head.length}
				<ul
					use:tiltField={{
						active: moving,
						reach: () => display.reach,
						strength: () => display.strength,
						lights: () => display.lights
					}}
					use:dragScroll
					bind:this={strip}
					class="strip -mx-1 -my-3 flex gap-2 overflow-x-auto px-1 py-3"
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
			{/if}

			<a
				href={resolve('/library')}
				class="mt-2.5 inline-flex items-center gap-1 text-[12.5px] font-medium text-dim hover:text-fg"
			>
				See the library
				<Glyph name="next" size={11} />
			</a>
		</div>
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
</style>
