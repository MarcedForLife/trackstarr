<script lang="ts">
	import RunButtons from '$lib/components/RunButtons.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import { dragDismiss } from '$lib/drag';
	import { type RunMode } from '$lib/library';
	import type { Run } from '$lib/runs';
	import { settle } from '$lib/settle';

	// The bar at the bottom of the library while there is a selection to act on
	// or a run to watch. One box through four states, moving between them rather
	// than jumping. It owns standing there: the rise, the grip, the dissolve. The
	// page owns why it is up.
	let {
		// Whether it stands: a selection, a run, or a receipt.
		up,
		// Whether a tap selects a poster. False for an adopted run.
		picking = false,
		run = null,
		stopping = false,
		picked = 0,
		// Whether "Select all" has anything to reach.
		selectable = false,
		mayRewrite = false,
		// Why a run cannot start now.
		refuses = '',
		// A run answered for but not yet in a snapshot.
		warming = false,
		busy = '',
		// The service's words when it refused this bar's last press.
		refusal = '',
		offline = '',
		// What the last run came to.
		done = '',
		onall,
		onclear,
		onrun,
		onstop,
		// The bar was put away. Spending its entry is the caller's.
		ondismiss
	}: {
		up: boolean;
		picking?: boolean;
		run?: Run | null;
		stopping?: boolean;
		picked?: number;
		selectable?: boolean;
		mayRewrite?: boolean;
		refuses?: string;
		warming?: boolean;
		busy?: '' | RunMode;
		refusal?: string;
		offline?: string;
		done?: string;
		onall: () => void;
		onclear: () => void;
		onrun: (mode: RunMode) => void;
		onstop: () => void;
		ondismiss: () => void;
	} = $props();

	// Putting the bar away. The Select button is a scroll away by then, so the
	// bar carries the sheet's grip: drag it off or press it. All three ways go
	// through `dismiss`, so it leaves the same way whichever was used.

	let bar: HTMLElement | null = $state(null);
	// Whether a drag is under way: the bar wears its transition when not dragged.
	// The offset is not state; $lib/drag writes it onto the element per frame.
	let dragging = $state(false);
	// Dismissed and fading, still mounted so it has something to fade.
	let going = $state(false);

	// Past this the bar goes rather than springing back. Shorter than the
	// sheet's, since the bar is a third its height.
	const DISMISS = 80;

	// Long enough to read as leaving rather than blinking out.
	const FADE = 160;

	// How far it keeps moving down while it fades: the point is the direction.
	const AWAY = 28;

	// Cleared here rather than when the fade ends: the pop that removes the bar
	// lands a frame or two after history.back(), and cleared early the bar
	// flashed on for those frames.
	$effect(() => {
		if (!up) going = false;
	});

	/**
	 * Start leaving; how many milliseconds that takes. For the ways out this
	 * never hears as a gesture: a back swipe pops the entry and the page calls
	 * this from the popstate. Zero when there is nothing to wait for: the grip
	 * and buttons fade before spending the entry, and a run keeps the bar up.
	 */
	export function fade(): number {
		if (going || run) return 0;
		going = true;
		// The drift the grip's dismissal ends on, from where the bar stands.
		if (bar) bar.style.transform = `translateY(${AWAY}px)`;
		return FADE;
	}

	/**
	 * Abandon a fade because the bar was asked for again inside it. Left alone it
	 * would stay invisible and refuse every press.
	 */
	export function stay() {
		going = false;
		if (bar) bar.style.transform = '';
	}

	// Where the grip has dragged the bar to.
	function moved(offset: number, live: boolean) {
		dragging = live;
		if (bar) bar.style.transform = offset ? `translateY(${offset}px)` : '';
	}

	/** Take the bar away. `at` is where the finger left it, to carry on from.
	 * Also called by the button at the top of the page. */
	export function dismiss(at = 0) {
		if (going) return;
		// A run keeps the bar up; only the picking half goes, with no fade.
		if (run) {
			ondismiss();
			return;
		}
		going = true;
		if (bar) bar.style.transform = `translateY(${at + AWAY}px)`;
		setTimeout(ondismiss, FADE);
	}

	// A state on its way out, taken out of the flow so the box settles to the
	// arriving state's height, and faded where it was. The arriving state fades
	// up over it (`.swap`), so the change is a dissolve.
	function dissolve(node: HTMLElement) {
		const top = node.offsetTop;
		return {
			duration: 150,
			css: (t: number) =>
				`position: absolute; inset-inline: 0; top: ${top}px; opacity: ${t}; pointer-events: none`
		};
	}
</script>

<!-- Sticky rather than fixed, so it stays in this column clear of the sidebar
     and scrolls away once the grid runs out. The offset carries the safe-area
     inset as well as --tabbar, like the save bar; --tabbar is the row without
     its inset, so without it the bar overprinted the tab bar on a phone with
     gesture navigation. 1rem so the bar floats over the tab bar. -->
{#if up}
	<div
		bind:this={bar}
		use:settle
		class={`bar sticky bottom-[calc(1rem+env(safe-area-inset-bottom)+var(--tabbar))] z-20 mt-6 rounded-xl border border-line-strong bg-raised/95 px-3 pt-2 pb-3 shadow-lg backdrop-blur-md sm:px-3.5 sm:pb-3.5 ${
			dragging ? '' : 'transition-transform duration-300 ease-[cubic-bezier(0.33,1,0.68,1)]'
		} ${going ? 'going' : ''}`}
	>
		<!-- One child holding the lot, which `settle` measures. `flow-root` keeps
		     the grip's negative margin inside the measured height; `relative` is
		     what the leaving state is positioned against. -->
		<div class="relative flow-root">
			{#if run}
				<!-- A run owns the bar; the selection stays underneath to run again.
				     The same lines the sheet's panel shows. -->
				<div class="swap" out:dissolve>
					<RunProgress {run} {stopping} {onstop} />
				</div>
			{:else}
				<!-- The fading state must be this branch's own child: an `out:` on
				     something nested in a further {#if} never runs. -->
				<div class="swap" out:dissolve>
					<!-- No grip while a run is going; Stop is the way out of that.
					     `touch-none` makes the drag possible: under `manipulation` the
					     browser took it for a page scroll and cancelled the pointer. -->
					<button
						type="button"
						aria-label="Stop picking titles"
						use:dragDismiss={{
							threshold: DISMISS,
							may: () => !going,
							onmove: moved,
							ondismiss: dismiss
						}}
						class="-mx-3 -mt-2 block w-[calc(100%+1.5rem)] cursor-grab touch-none pt-2.5 pb-2 active:cursor-grabbing sm:-mx-3.5 sm:w-[calc(100%+1.75rem)]"
					>
						<!-- The whole strip is the target; a 4px pill is not. -->
						<span class="mx-auto block h-1 w-10 rounded-full bg-line-strong"></span>
					</button>

					{#if picking}
						<!-- The count and its two buttons share a line, with the run pair
						     under them. -->
						<div class="flex items-baseline gap-x-3">
							<p class="min-w-0 flex-1 text-[13px] font-medium">
								{picked
									? `${picked} ${picked === 1 ? 'title' : 'titles'} selected`
									: 'Select a title'}
							</p>
							<div class="flex flex-none items-center gap-1">
								<button
									type="button"
									onclick={onall}
									disabled={!selectable}
									class="rounded-md px-2 py-1 text-[12px] font-medium text-dim transition-colors hover:text-fg disabled:opacity-50"
								>
									Select all
								</button>
								{#if picked}
									<button
										type="button"
										onclick={onclear}
										class="rounded-md px-2 py-1 text-[12px] font-medium text-dim transition-colors hover:text-fg"
									>
										Clear
									</button>
								{/if}
							</div>
						</div>

						<div class="mt-2.5">
							<!-- `refuses` goes separately: an empty selection stops the press
							     but is not a refusal. The pair fills the bar's width. -->
							<RunButtons
								fill
								{mayRewrite}
								{refuses}
								disabled={!picked || warming}
								{busy}
								{onrun}
							/>
						</div>
					{/if}

					<!-- One line, the loudest first. Only this bar's own refusal; a sheet's
					     was answered there. A lost connection ranks behind it, and why the
					     pair is dead behind that. -->
					{#if refusal}
						<p role="alert" class="swap mt-2 text-[12.5px] text-danger">{refusal}</p>
					{:else if offline}
						<p role="alert" class="swap mt-2 text-[12.5px] text-danger">{offline}</p>
					{:else if picking && refuses}
						<p class="swap mt-2 text-[12.5px] text-dim">{refuses}</p>
					{:else if done}
						<!-- What the last run came to, kept until the next starts. -->
						<p role="status" class={`swap text-[12.5px] text-dim ${picking ? 'mt-2' : ''}`}>
							{done}
						</p>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	/* The bar rising from under the edge it sits on. No fill, or a `forwards`
	   fill would outrank the inline offset a drag writes. Out-cubic like the
	   sheet: out-quint put 26% of the rise in one frame and left a dead tail. */
	.bar {
		animation: bar-rise 280ms cubic-bezier(0.33, 1, 0.68, 1);
	}

	/* The bar leaving. One rule for both properties: two Tailwind transition
	   utilities set the same `transition-property`, and the last emitted won,
	   so the bar snapped invisible. 160ms is FADE above; the 300ms transform
	   transition is the springback, which this replaces. */
	.going {
		opacity: 0;
		pointer-events: none;
		transition:
			opacity 160ms cubic-bezier(0.4, 0, 0.2, 1),
			transform 160ms cubic-bezier(0.4, 0, 0.2, 1);
	}

	@keyframes bar-rise {
		from {
			transform: translateY(calc(100% + 0.75rem));
			opacity: 0;
		}
	}

	/* The contents changing, the other half of the height settling. No delay: a
	   60ms hold was four blank frames. */
	.swap {
		animation: swap-in 150ms;
	}

	@keyframes swap-in {
		from {
			opacity: 0;
		}
	}
</style>
