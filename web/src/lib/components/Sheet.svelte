<script module lang="ts">
	// How long the exit takes. Exported so a caller keeps the contents until the
	// sheet has gone, or the panel collapses to its handle. Must match the style
	// block below.
	export const SLIDE = 320;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { frosted, scrim } from '$lib/controls';
	import { OUT, dropOf, slide, speedOf, type Motion } from '$lib/slide';
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
	// The run the panel is on, so content landing while it slides is taken over
	// at the speed it has; see $lib/slide.
	let motion: Motion | null = null;

	// Past this the sheet goes rather than springing back: about a thumb's
	// travel.
	const DISMISS = 110;

	// How long a click may still be coming from the tap that opened this: a touch
	// synthesises one after the release, and it would land on the backdrop.
	const SETTLE = 300;

	// When the sheet last came up.
	let raised = 0;

	// How long the way in takes. Must match the class on the panel below.
	const ARRIVE = 400;

	// Whether the panel has been given its offset for this open. The class
	// swap waits on it, since the offset is measured off the content the open
	// mounts; see below.
	let placed = $state(false);

	// Whether the panel was at rest below the screen as this open began, read
	// before the DOM changes. Reopened mid-exit, the transition reverses from
	// where it is instead.
	let rested = true;
	// Whether the slide covers the first change of height after this open: it
	// does from rest, where the content mounts under the floor. Brought back
	// mid-exit, the new content lands on a panel already in view.
	let covered = true;

	// The offset a closed panel rests at, `--tuck`, in pixels rather than the
	// `translate-y-full` it used to be. A percentage is measured again every
	// frame: a title landing mid-slide grew the panel, the offset left grew with
	// it, and the panel dropped back before the takeover caught up. Set as
	// the sheet goes, from the height it has, and again as it comes up, from
	// the height its content mounts at; the panel is still hidden then, and the
	// transition is off while the offset moves under it, or the slide would
	// start from the old one.
	$effect.pre(() => {
		if (!panel) return;
		if (open) rested = getComputedStyle(panel).visibility === 'hidden';
		else {
			// A takeover's inline run would outrank the class the exit is on.
			panel.style.translate = '';
			panel.style.transition = '';
			motion = null;
			panel.style.setProperty('--tuck', `${panel.offsetHeight}px`);
		}
	});

	$effect(() => {
		if (!panel) return;
		if (!open) {
			placed = false;
			return;
		}
		raised = performance.now();
		// Drop the offset a dismissing drag left. Down, it costs nothing; this is
		// where it would be seen, and needs no timer.
		panel.style.transform = '';
		covered = rested;
		if (rested) {
			panel.style.transition = 'none';
			panel.style.setProperty('--tuck', `${panel.offsetHeight}px`);
			void panel.offsetHeight;
			panel.style.transition = '';
		}
		placed = true;
		// From the floor, or from wherever an exit had got to.
		motion = {
			at: raised,
			from: rested ? panel.offsetHeight : dropOf(getComputedStyle(panel).translate),
			span: ARRIVE,
			curve: OUT
		};
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
	// here, so a takeover still running lets go of it.
	function moved(offset: number, live: boolean) {
		dragging = live;
		if (!panel) return;
		if (motion) {
			panel.style.translate = '';
			panel.style.transition = '';
			motion = null;
		}
		panel.style.transform = offset ? `translateY(${offset}px)` : '';
	}

	// The sheet stands on the floor, so content landing after it is up moves its
	// top edge in one step. The slide is taken over instead: the difference
	// added to what is left of it, from where it is and at the speed it has, so
	// the edge makes one motion to the new rest rather than a second on top of
	// the first. 260ms from a standstill, against the slide's 400ms, which
	// content usually lands inside.
	const GROWTH = 260;

	$effect(() => {
		const box = panel;
		if (!box) return;
		let height = box.getBoundingClientRect().height;
		// The first change after it comes up is the sheet filling, which the
		// slide covers from rest; see `covered`.
		let filling = true;
		const watch = new ResizeObserver(() => {
			const measured = box.getBoundingClientRect().height;
			const grew = measured - height;
			height = measured;
			if (!open || dragging || reduced()) filling = true;
			else if (filling && covered) filling = false;
			else if (Math.abs(grew) >= 1) {
				filling = false;
				const at = performance.now();
				const rising = motion !== null && at < motion.at + motion.span;
				const from = dropOf(getComputedStyle(box).translate) + grew;
				if (Math.abs(from) >= 1)
					motion = slide(box, from, speedOf(motion, at), rising ? ARRIVE : GROWTH);
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
		class={`absolute inset-0 ${scrim} transition-opacity ${
			open
				? 'opacity-100 duration-400 ease-[cubic-bezier(0.33,1,0.68,1)]'
				: 'opacity-0 duration-320 ease-[cubic-bezier(0.4,0,0.2,1)]'
		}`}
	></button>

	<!-- The way in and the springback. Out-cubic over 400ms, ARRIVE above:
	     out-quint put 48% of an 805px slide in one frame then crawled, reading
	     as a snap and a stall. The exit is in the style block, on the class a
	     closed sheet carries. -->
	<div
		bind:this={panel}
		role="dialog"
		aria-modal="true"
		aria-label={label}
		tabindex="-1"
		inert={!open}
		class={`relative flex max-h-[88dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl border border-line ${frosted} shadow-2xl outline-none sm:mb-4 sm:rounded-2xl ${
			dragging ? '' : 'transition-transform duration-400 ease-[cubic-bezier(0.33,1,0.68,1)]'
		} ${open && placed ? 'translate-y-0' : 'tucked'}`}
	>
		<!-- The whole strip is the grab area; `touch-none` makes the drag possible:
		     under `manipulation` the browser took it for a scroll and cancelled the
		     pointer. The cross is the way out for anyone who cannot drag, so the
		     strip itself is out of the tab order rather than a second Close. -->
		<div class="relative h-12 flex-none">
			<!-- Laid out, not left to the button's own centring, which put the bar
			     below the cross rather than through it. -->
			<button
				type="button"
				tabindex="-1"
				aria-hidden="true"
				use:dragDismiss={{ threshold: DISMISS, onmove: moved, ondismiss: dismiss }}
				class="absolute inset-0 flex cursor-grab touch-none items-start justify-center pt-4.5 active:cursor-grabbing"
			>
				<span class="block h-1 w-10 rounded-full bg-line-strong"></span>
			</button>
			<!-- Quiet until it is reached for: a sheet is dismissed far more often by
			     the page behind it or a drag than by this. -->
			<button
				type="button"
				aria-label="Close"
				onclick={dismiss}
				class="absolute top-1.5 right-2.5 inline-flex h-9 w-9 items-center justify-center rounded-full text-faint transition-colors after:absolute after:-inset-1 after:content-[''] hover:bg-raised hover:text-fg active:bg-sunken"
			>
				<Glyph name="cross" size={14} />
			</button>
		</div>

		<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain">
			{@render children()}
		</div>
	</div>
</div>

<style>
	/* A closed sheet is translated down by its own height, `--tuck` from the
	   script, which empty is the 20px handle: not off screen, and Android's
	   URL bar retracting moved the viewport by more than that, so once the
	   slide out has run the sheet is hidden outright, with the delay on
	   `visibility` alone so it slides first.

	   Both `translate` and `transform` (the inline drag offset) are named: a
	   shorthand replaces Tailwind's list, and naming one animated nothing.

	   Leaving is 320ms on $lib/settle's SHRINK curve, against 400ms out-cubic
	   arriving: a sheet easing into the floor it is about to hide behind spends
	   its last frames moving three pixels. 320 is the grid's slide, and 260
	   read as dismissed rather than leaving. SLIDE above is this number. */
	.tucked {
		translate: 0 var(--tuck, 100%);
		visibility: hidden;
		transition:
			translate 320ms cubic-bezier(0.4, 0, 0.2, 1),
			transform 320ms cubic-bezier(0.4, 0, 0.2, 1),
			visibility 0s 320ms;
	}
</style>
