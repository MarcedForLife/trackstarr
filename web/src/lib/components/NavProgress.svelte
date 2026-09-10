<script lang="ts">
	import { navigating } from '$app/state';

	// Between the tap and the new page there is nothing to look at, up to 500ms
	// on a phone. This answers "did that register", not how far along it is.

	// A navigation landing inside this is not worth a bar that would flicker.
	const QUIET_MS = 140;
	// Where the crawl stops and waits, short of the end, so it never lies.
	const CEILING = 0.9;
	const CRAWL_MS = 9000;
	// The run-out once the page is there, and the fade. Short: punctuation.
	const DONE_MS = 220;
	const FADE_MS = 200;

	let progress = $state(0);
	let shown = $state(false);
	let ease = $state(`${CRAWL_MS}ms`);

	$effect(() => {
		// Reading it subscribes the effect; the cleanup is the navigation ending.
		if (navigating.to === null) return;

		const waiting = setTimeout(() => {
			shown = true;
			ease = `${CRAWL_MS}ms`;
			progress = CEILING;
		}, QUIET_MS);

		let running: ReturnType<typeof setTimeout>[] = [];
		return () => {
			clearTimeout(waiting);
			running.forEach(clearTimeout);
			running = [];
			// Nothing was ever drawn, so there is nothing to finish.
			if (!shown) {
				progress = 0;
				return;
			}
			ease = `${DONE_MS}ms`;
			progress = 1;
			running.push(
				setTimeout(() => {
					shown = false;
					// Rewound once invisible, or it slides back in view.
					running.push(
						setTimeout(() => {
							ease = '0ms';
							progress = 0;
						}, FADE_MS)
					);
				}, DONE_MS)
			);
		};
	});
</script>

<!-- Decorative: SvelteKit announces the route change itself. -->
<div class="rail" class:on={shown} aria-hidden="true">
	<div class="bar" style:--p={progress} style:--ease={ease}></div>
</div>

<style>
	.rail {
		/* Above the drawer, since a navigation can start inside it. */
		position: fixed;
		top: env(safe-area-inset-top, 0px);
		right: 0;
		left: 0;
		z-index: 60;
		height: 2px;
		opacity: 0;
		/* Matches FADE_MS, which is what the script waits out before rewinding. */
		transition: opacity 200ms ease;
		pointer-events: none;
	}

	.rail.on {
		opacity: 1;
	}

	.bar {
		height: 100%;
		background: var(--accent-fill);
		/* scaleX, so the crawl is a compositor job. Written here because
		   Tailwind v4 composes `transform` from its own variables. */
		transform: scaleX(var(--p));
		transform-origin: left center;
		transition: transform var(--ease) cubic-bezier(0, 0.7, 0.25, 1);
	}
</style>
