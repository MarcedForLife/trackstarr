<script module lang="ts">
	// How long the exit takes. Exported so a caller keeps the contents until the
	// sheet has gone, or the panel collapses to its handle. Must match the style
	// block below.
	export const SLIDE = 320;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import { carry } from '$lib/carry';
	import { dragDismiss } from '$lib/drag';
	import { behind, keyboard } from '$lib/modal';
	import { reduced } from '$lib/motion.svelte';

	// A panel that comes up from the bottom over the page. A sheet rather than a
	// route, so the grid keeps its scroll position. The backdrop and handle are
	// its own ways out; Escape and back belong to whoever holds its history
	// entry (see $lib/overlay) and arrive as the same onclose.
	let {
		open,
		onclose,
		label,
		children
	}: { open: boolean; onclose: () => void; label: string; children: Snippet } = $props();

	// The overlay and the panel: the scrim stays reachable while the page does
	// not.
	let root: HTMLElement | null = $state(null);
	let panel: HTMLElement | null = $state(null);

	// Whether a drag is under way: the panel wears its transition when not
	// dragged. The offset is not state; $lib/drag writes it per frame.
	let dragging = $state(false);
	// A growth still easing out, which a finger on the panel takes over from.
	let growth: Animation | null = null;

	// Past this the sheet goes rather than springing back: about a thumb's
	// travel.
	const DISMISS = 110;

	// How long a click may still be coming from the tap that opened this: a touch
	// synthesises one after the release, and it would land on the backdrop.
	const SETTLE = 300;

	// When the sheet last came up.
	let raised = 0;

	$effect(() => {
		if (!open) return;
		raised = performance.now();
		// Drop the offset a dismissing drag left. Down, it costs nothing; this is
		// where it would be seen, and needs no timer.
		if (panel) panel.style.transform = '';
	});

	function backdrop() {
		if (performance.now() - raised < SETTLE) return;
		dismiss();
	}

	function dismiss() {
		// Only while up: a click on the fading backdrop is not a second dismissal.
		if (!open) return;
		onclose();
	}

	// Where the handle has dragged the panel to. The finger owns the panel from
	// here, so anything still easing lets go of it.
	function moved(offset: number, live: boolean) {
		dragging = live;
		growth?.cancel();
		if (panel) panel.style.transform = offset ? `translateY(${offset}px)` : '';
	}

	// The sheet stands on the floor, so content landing after it is up moves its
	// top edge in one step. Ease the difference out on the panel and the edge
	// travels instead. 260ms against the slide's 400ms, which it usually lands
	// inside.
	const GROWTH = 260;
	const CURVE = 'cubic-bezier(0.33, 1, 0.68, 1)';

	$effect(() => {
		const box = panel;
		if (!box) return;
		let height = box.getBoundingClientRect().height;
		// The first change after it comes up is the sheet filling, which the
		// slide covers.
		let filling = true;
		const watch = new ResizeObserver(() => {
			const now = box.getBoundingClientRect().height;
			const grew = now - height;
			height = now;
			if (!open || dragging || reduced()) filling = true;
			else if (filling) filling = false;
			else if (Math.abs(grew) >= 1) {
				// Taken over, not stacked on. A block animating its own height grows
				// a few pixels a frame.
				const was = growth;
				growth = carry(box, grew, GROWTH, CURVE);
				was?.cancel();
			}
		});
		watch.observe(box);
		return () => watch.disconnect();
	});

	// The page underneath stops scrolling and goes inert; the keyboard comes here
	// and goes back after. See $lib/modal.
	$effect(() => {
		if (!open || !root || !panel) return;
		// The keyboard first: it notes where it came from before that goes inert.
		const undo = [keyboard(panel), behind(root)];
		return () => undo.forEach((restore) => restore());
	});
</script>

<!-- Kept in the DOM so both directions animate; `inert` makes a closed one
     unreachable. -->
<div
	bind:this={root}
	class={`fixed inset-0 z-50 flex items-end justify-center ${open ? '' : 'pointer-events-none'}`}
>
	<button
		type="button"
		tabindex={open ? 0 : -1}
		aria-label="Close"
		onclick={backdrop}
		class={`absolute inset-0 bg-black/60 transition-opacity ${
			open
				? 'opacity-100 duration-400 ease-[cubic-bezier(0.33,1,0.68,1)]'
				: 'opacity-0 duration-320 ease-[cubic-bezier(0.4,0,0.2,1)]'
		}`}
	></button>

	<!-- The way in and the springback. Out-cubic over 400ms: out-quint put 48%
	     of an 805px slide in one frame then crawled, reading as a snap and a
	     stall. The exit is in the style block, on the class a closed sheet
	     carries. -->
	<div
		bind:this={panel}
		role="dialog"
		aria-modal="true"
		aria-label={label}
		tabindex="-1"
		inert={!open}
		class={`relative flex max-h-[88dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl border border-line bg-raised/85 shadow-2xl backdrop-blur-md outline-none sm:mb-4 sm:rounded-2xl ${
			dragging ? '' : 'transition-transform duration-400 ease-[cubic-bezier(0.33,1,0.68,1)]'
		} ${open ? 'translate-y-0' : 'tucked translate-y-full'}`}
	>
		<!-- The whole strip is the grab area; a button, so it also closes for
		     anyone who cannot drag. `touch-none` makes the drag possible: under
		     `manipulation` the browser took it for a scroll and cancelled the
		     pointer. -->
		<button
			type="button"
			aria-label="Close"
			use:dragDismiss={{ threshold: DISMISS, onmove: moved, ondismiss: dismiss }}
			class="flex-none cursor-grab touch-none pt-2.5 pb-1 active:cursor-grabbing"
		>
			<span class="mx-auto block h-1 w-10 rounded-full bg-line-strong"></span>
		</button>

		<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain">
			{@render children()}
		</div>
	</div>
</div>

<style>
	/* A closed sheet is translated down by its own height, which empty is the
	   20px handle: not off screen, and Android's URL bar retracting moved the
	   viewport by more than that, so the handle rose over the grid. So once
	   the slide out has run the sheet is hidden outright, with the delay on
	   `visibility` alone so it slides first.

	   Both `translate` (Tailwind's translate-y-full) and `transform` (the
	   inline drag offset) are named: a shorthand replaces Tailwind's list, and
	   naming one animated nothing.

	   Leaving is 320ms on $lib/settle's SHRINK curve, against 400ms out-cubic
	   arriving: a sheet easing into the floor it is about to hide behind spends
	   its last frames moving three pixels. 320 is the grid's slide, and 260
	   read as dismissed rather than leaving. SLIDE above is this number. */
	.tucked {
		visibility: hidden;
		transition:
			translate 320ms cubic-bezier(0.4, 0, 0.2, 1),
			transform 320ms cubic-bezier(0.4, 0, 0.2, 1),
			visibility 0s 320ms;
	}
</style>
