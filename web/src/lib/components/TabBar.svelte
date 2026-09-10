<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { chrome } from '$lib/chrome.svelte';
	import NavIcon from '$lib/components/NavIcon.svelte';
	import { reduced } from '$lib/motion.svelte';
	import { PRIMARY, routeOf } from '$lib/nav';
	import { pause, watchPause } from '$lib/paused.svelte';

	// The phone's primary nav: three destinations a thumb reaches. The drawer
	// keeps Settings and the account. Three, so a 412px screen has room for a
	// real icon over a real label.

	const path = $derived(routeOf(page.url.pathname));
	const index = $derived(PRIMARY.findIndex((spot) => spot.href === path));

	$effect(watchPause);

	// The bar retracts on the two pages long enough for it to matter.
	const RETRACTS = new Set<string>(['/library', '/events']);

	// Above this the bar is always up.
	const TOP = 96;
	// And within this of the end.
	const END = 24;
	// How long after the last scroll event the gesture counts as over, when a
	// half-retracted bar picks an end.
	const STILL_MS = 90;

	let bar: HTMLElement | null = $state(null);

	// How far down the bar is pushed, 0 to its height. Not $state: written to the
	// node in a frame callback, since it changes on every scroll event.
	let shift = 0;

	function paint() {
		// No CSS transition: `shift` is the one place the position lives, so a
		// scroll mid-settle resumes from where the bar is. A transition made the
		// return snap.
		if (bar) bar.style.translate = `0 ${shift}px`;
	}

	// The bar's height, padding included. Measured, since the safe-area inset
	// resolves after first layout and changes with the browser chrome; a constant
	// left the inset's worth of bar on screen, unable to retract.
	let tall = 56;

	$effect(() => {
		if (!bar) return;
		const measure = () => (tall = bar?.offsetHeight ?? 56);
		// border-box: the inset arrives as padding, which a content-box observer
		// never sees.
		const watch = new ResizeObserver(measure);
		watch.observe(bar, { box: 'border-box' });
		measure();
		// The visual viewport is what changes when Android's chrome comes and
		// goes; window resize does not fire.
		window.visualViewport?.addEventListener('resize', measure);
		return () => {
			watch.disconnect();
			window.visualViewport?.removeEventListener('resize', measure);
		};
	});

	// The settle, run here rather than by CSS; see paint().
	let settling = 0;
	const SETTLE_MS = 180;

	function stopSettling() {
		if (settling) cancelAnimationFrame(settling);
		settling = 0;
	}

	function glideTo(to: number) {
		stopSettling();
		const from = shift;
		if (from === to) return;
		const began = performance.now();
		const step = (now: number) => {
			const t = Math.min(1, (now - began) / SETTLE_MS);
			// easeOutCubic: the shape a flick already has when this takes over.
			shift = from + (to - from) * (1 - Math.pow(1 - t, 3));
			paint();
			settling = t < 1 ? requestAnimationFrame(step) : 0;
		};
		settling = requestAnimationFrame(step);
	}

	// Only the route decides whether the listener exists. chrome.held is read in
	// the frame callback instead, or every change rebuilt the listener.
	$effect(() => {
		if (!RETRACTS.has(path)) {
			stopSettling();
			shift = 0;
			paint();
			return;
		}

		// Scroll-linked movement is what reduced motion least wants.
		if (reduced()) {
			stopSettling();
			shift = 0;
			paint();
			return;
		}

		let last = window.scrollY;
		let rising = false;
		let queued = false;
		let resting: ReturnType<typeof setTimeout> | null = null;

		function frame() {
			queued = false;
			const y = Math.max(0, window.scrollY);
			const bottom = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
			const dy = y - last;
			last = y;
			if (dy !== 0) rising = dy < 0;

			// Read live: the hold must win the moment the bar would move.
			if (chrome.held || y <= TOP || y >= bottom - END) shift = 0;
			else shift = Math.min(tall, Math.max(0, shift + dy));
			paint();

			if (resting) clearTimeout(resting);
			resting = setTimeout(rest, STILL_MS);
		}

		// The gesture is over; a bar caught half way picks an end by the direction
		// it was last going, since a reader scrolling back up wants the nav.
		function rest() {
			if (chrome.held || shift <= 0 || shift >= tall) return;
			glideTo(rising ? 0 : tall);
		}

		// Moved by the scroll delta, as Chrome's own toolbar tracks the finger.
		// Passive and one read a frame, on a page of several hundred cards.
		function onScroll() {
			// Momentum outlives the finger; a settle in progress is abandoned.
			stopSettling();
			if (queued) return;
			queued = true;
			requestAnimationFrame(frame);
		}

		window.addEventListener('scroll', onScroll, { passive: true });
		return () => {
			window.removeEventListener('scroll', onScroll);
			if (resting) clearTimeout(resting);
			stopSettling();
			shift = 0;
			paint();
		};
	});

	// A hold taken while the bar is down must bring it back; no frame is coming.
	// Its own effect, so it cannot disturb the listener.
	$effect(() => {
		if (chrome.held) glideTo(0);
	});

	// A keyboard reaching a retracted bar brings it back, or the focus ring is
	// off screen.
	function reveal() {
		glideTo(0);
	}
</script>

<!-- z-30: over the page, under the drawer's scrim and the title sheet.
     touch-none: a drag on the bar is not a scroll, so it cannot pull the page
     to a refresh. -->
<nav
	bind:this={bar}
	aria-label="Primary"
	onfocusin={reveal}
	class="fixed inset-x-0 bottom-0 z-30 touch-none border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden"
>
	<div class="relative grid h-14 auto-cols-fr grid-flow-col">
		<!-- The pill slides between cells like the segmented switch's thumb.
		     Hidden on a settings page. Inline transform with Tailwind's
		     transition-transform, as Segmented does: v4 composes `transform` from
		     its own variables. -->
		{#if index >= 0}
			<!-- The travelling element is a cell wide so the offset is the index;
			     the pill inside is sized to the icon. -->
			<span
				aria-hidden="true"
				class="pointer-events-none absolute inset-y-0 left-0 flex items-center justify-center transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
				style={`width: calc(100% / ${PRIMARY.length}); transform: translateX(${index * 100}%)`}
			>
				<!-- Around the whole item: a pill behind the icon alone sat on the
				     label in a 56px bar. -->
				<span class="h-11 w-[4.75rem] rounded-full bg-accent-soft"></span>
			</span>
		{/if}

		{#each PRIMARY as spot (spot.href)}
			{@const here = path === spot.href}
			<a
				href={resolve(spot.href)}
				aria-current={here ? 'page' : undefined}
				class={`relative flex flex-col items-center justify-center gap-1 ${
					here ? 'text-accent' : 'text-dim active:text-fg'
				}`}
			>
				<span class="relative">
					<NavIcon icon={spot.icon} size={19} />
					{#if spot.href === '/' && pause.current}
						<!-- On a phone this is the only nav, so the pause shows here. -->
						<span
							title="Processing is paused"
							aria-label="Processing is paused"
							class="absolute -top-0.5 -right-1 h-1.5 w-1.5 rounded-full bg-danger ring-2 ring-surface"
						></span>
					{/if}
				</span>
				<span class={`text-[11px] leading-none ${here ? 'font-semibold' : 'font-medium'}`}>
					{spot.label}
				</span>
			</a>
		{/each}
	</div>
</nav>
