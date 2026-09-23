<script lang="ts">
	import Glyph, { type GlyphName } from './Glyph.svelte';

	// The glyph swaps for a turning arc while busy. Without one, the arc takes no
	// room at rest.
	let { busy, glyph, size = 12 }: { busy: boolean; glyph?: GlyphName; size?: number } = $props();
</script>

{#snippet arc()}
	<svg
		class="arc"
		width={size}
		height={size}
		viewBox="0 0 16 16"
		fill="none"
		stroke="currentColor"
		stroke-width="1.75"
	>
		<circle cx="8" cy="8" r="6" opacity="0.25"></circle>
		<circle cx="8" cy="8" r="6" pathLength="100" stroke-dasharray="28 100" stroke-linecap="round"
		></circle>
	</svg>
{/snippet}

{#if glyph}
	<span class="slot" class:busy aria-hidden="true">
		<span class="mark"><Glyph name={glyph} {size} /></span>
		{@render arc()}
	</span>
{:else if busy}
	<span class="slot busy lone" aria-hidden="true">{@render arc()}</span>
{/if}

<style>
	.slot {
		display: inline-grid;
		flex: none;
	}

	.mark,
	.arc {
		grid-area: 1 / 1;
		transition:
			opacity 180ms var(--reveal-ease),
			scale 180ms var(--reveal-ease);
	}

	.mark {
		display: flex;
	}

	/* Paused, not removed, so a leaving arc fades where it stood. */
	.arc {
		opacity: 0;
		scale: 0.6;
		animation: turn 750ms linear infinite paused;
	}

	.busy .mark {
		opacity: 0;
		scale: 0.6;
	}

	.busy .arc {
		opacity: 1;
		scale: 1;
		animation-play-state: running;
	}

	@starting-style {
		.lone .arc {
			opacity: 0;
			scale: 0.6;
		}
	}

	@keyframes turn {
		to {
			rotate: 360deg;
		}
	}

	@keyframes breathe {
		from {
			opacity: 0.35;
		}
	}

	/* Under reduced motion the arc pulses in place. */
	@media (prefers-reduced-motion: reduce) {
		.mark,
		.arc,
		.busy .mark,
		.busy .arc {
			scale: 1;
		}

		.busy .arc {
			animation: breathe 900ms ease-in-out infinite alternate;
		}
	}
</style>
