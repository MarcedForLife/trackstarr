<script module lang="ts">
	import { BARS, ROLL_BARS, CYCLE_MS, SEGMENTS, SAMPLES, markPose } from '$lib/mark-motion';

	// Bake the paths once. The browser plays transforms without a JS frame loop.
	const pieces = ROLL_BARS.flatMap((_, index) =>
		Array.from({ length: SEGMENTS }, (_, part) => {
			let previous = 0;
			const frames = Array.from({ length: SAMPLES + 1 }, (_, frame) => {
				const pose = markPose(frame / SAMPLES, index, part);
				// Equivalent tangents stay on one branch, so a stroke cannot flip.
				const rotation = pose.rotation - Math.round((pose.rotation - previous) / 180) * 180;
				previous = rotation;
				return {
					offset: frame / SAMPLES,
					transform: `translate(${pose.x}px, ${pose.y}px) rotate(${rotation}deg)`,
					scale: `scaleY(${pose.span})`
				};
			});
			return {
				dot: index === 0 || index === ROLL_BARS.length - 1,
				star: frames[0],
				frames: frames.map(({ offset, transform }) => ({ offset, transform })),
				scales: frames.map(({ offset, scale }) => ({ offset, transform: scale }))
			};
		})
	);
</script>

<script lang="ts">
	import { onDestroy } from 'svelte';
	import { reduced } from '$lib/motion.svelte';

	let {
		size = 19,
		class: className = '',
		motion = 'none'
	}: {
		size?: number;
		class?: string;
		motion?: 'none' | 'drop' | 'process';
	} = $props();

	let rotor = $state<SVGGElement>();
	let animations: Animation[] = [];

	function clear() {
		animations.forEach((animation) => animation.cancel());
		animations = [];
	}

	$effect(() => {
		const group = rotor;
		const still = reduced();
		if (!group || still) {
			clear();
			return;
		}
		if (motion !== 'process') {
			// Finish this roll at the star. A quick restart extends the same cycle.
			for (const animation of animations) {
				const time = Number(animation.currentTime ?? 0);
				animation.effect?.updateTiming({ iterations: Math.floor(time / CYCLE_MS) + 1 });
			}
			return;
		}
		if (animations.length) {
			animations.forEach((animation) => animation.effect?.updateTiming({ iterations: Infinity }));
			return;
		}

		const timing = { duration: CYCLE_MS, iterations: Infinity };
		animations = Array.from(group.children).flatMap((piece, index) => {
			const shape = pieces[index];
			const position = piece.animate(shape.frames, timing);
			return shape.dot
				? [position]
				: [position, piece.firstElementChild!.animate(shape.scales, timing)];
		});
		// All strokes share a clock, including their staggered departures and arrivals.
		const start = document.timeline.currentTime;
		animations.forEach((animation) => (animation.startTime = start));
		animations[0].onfinish = clear;
	});

	onDestroy(clear);
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	aria-hidden="true"
	class={className}
	class:drop={motion === 'drop'}
	style:--size={`${size}px`}
>
	{#if motion === 'drop'}
		{#each BARS as bar, index (bar.x)}
			<line
				x1={bar.x}
				x2={bar.x}
				y1={bar.top}
				y2={bar.bottom}
				vector-effect="non-scaling-stroke"
				style:--i={index}
			></line>
		{/each}
	{:else}
		<g bind:this={rotor}>
			{#each pieces as piece, index (index)}
				<g style:transform={piece.star.transform}>
					{#if piece.dot}
						<!-- Circles keep the outer dots round even at the end of a full turn. -->
						<circle r="1.6" fill="currentColor"></circle>
					{:else}
						<line
							x1="0"
							x2="0"
							y1="-0.5"
							y2="0.5"
							vector-effect="non-scaling-stroke"
							style:transform={piece.star.scale}
						></line>
					{/if}
				</g>
			{/each}
		</g>
	{/if}
</svg>

<style>
	svg {
		overflow: visible;
	}
	line {
		stroke: currentColor;
		stroke-width: calc(var(--size) / 7.5);
		stroke-linecap: round;
	}

	.drop line {
		animation: drop 620ms cubic-bezier(0.34, 1.5, 0.64, 1) both;
		animation-delay: calc(var(--i) * 70ms + 120ms);
	}
	@keyframes drop {
		from {
			transform: translateY(-9px);
			opacity: 0;
		}
		30% {
			opacity: 1;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.drop line {
			animation: none;
		}
	}
</style>
