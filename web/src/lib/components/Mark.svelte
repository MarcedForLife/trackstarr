<script module lang="ts">
	import { BARS, ROLL_BARS, CYCLE_MS, SEGMENTS, markPose } from '$lib/mark-motion';

	// Gives the zero-span outer dots a cap in every browser.
	const HAIR = 0.001;
	// In viewBox units: the drop's size / 7.5 at any size.
	const STROKE = 3.2;

	type Segment = (fromX: number, fromY: number, toX: number, toY: number) => void;

	/** Each piece of the mark at one point in the roll, as a round-capped segment. */
	function trace(time: number, segment: Segment) {
		ROLL_BARS.forEach((_, index) => {
			for (let part = 0; part < SEGMENTS; part++) {
				const pose = markPose(time, index, part);
				const angle = (pose.rotation * Math.PI) / 180;
				const half = Math.max(pose.span, HAIR) / 2;
				const alongX = -Math.sin(angle) * half;
				const alongY = Math.cos(angle) * half;
				segment(pose.x - alongX, pose.y - alongY, pose.x + alongX, pose.y + alongY);
			}
		});
	}

	let star = '';
	trace(0, (fromX, fromY, toX, toY) => {
		star += `M${fromX.toFixed(3)} ${fromY.toFixed(3)}L${toX.toFixed(3)} ${toY.toFixed(3)}`;
	});
	const STAR = star;
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

	// The star at rest is the SVG, and the roll a canvas over it. A canvas frame
	// skips the style, layout and paint that an SVG change costs.
	let box = $state<HTMLElement>();
	let art = $state<SVGSVGElement>();
	let canvas = $state<HTMLCanvasElement>();

	// Not state, since nothing renders from these.
	let context: CanvasRenderingContext2D | null = null;
	let ink: CSSStyleDeclaration | null = null;
	let started = 0;
	let finishAt = Infinity;
	let frame = 0;
	let rolling = false;

	// In the same frame as a draw, so the swap lands on an identical star.
	function show(roll: boolean) {
		if (roll === rolling || !canvas || !art) return;
		rolling = roll;
		canvas.style.visibility = roll ? 'visible' : 'hidden';
		art.style.visibility = roll ? 'hidden' : 'visible';
	}

	function draw(time: number) {
		if (!canvas || !context || !ink) return;
		// The box snapped to device pixels, so the canvas maps 1:1. Measured, since
		// devicePixelContentBoxSize reports CSS pixels under DPR emulation.
		const rect = canvas.getBoundingClientRect();
		const snap = (edge: number) => Math.round(edge * devicePixelRatio);
		const width = snap(rect.right) - snap(rect.left);
		const height = snap(rect.bottom) - snap(rect.top);
		if (canvas.width !== width || canvas.height !== height) {
			canvas.width = width;
			canvas.height = height;
		}
		// The SVG scales its view by the unsnapped size, from the snapped corner.
		const scale = (size * devicePixelRatio) / 24;
		context.setTransform(1, 0, 0, 1, 0, 0);
		context.clearRect(0, 0, width, height);
		context.setTransform(scale, 0, 0, scale, 0, 0);
		// Read each frame, so a palette swap mid-roll follows its transition.
		context.strokeStyle = ink.color;
		context.lineWidth = STROKE;
		context.lineCap = 'round';
		context.beginPath();
		trace(time, (fromX, fromY, toX, toY) => {
			context!.moveTo(fromX, fromY);
			context!.lineTo(toX, toY);
		});
		context.stroke();
	}

	function rest() {
		cancelAnimationFrame(frame);
		frame = 0;
		started = 0;
		show(false);
	}

	function roll(now: number) {
		if (!started) started = now;
		if (now >= finishAt) {
			rest();
			return;
		}
		frame = requestAnimationFrame(roll);
		// The drawer's copy is hidden below lg and the header's above.
		if (box?.checkVisibility?.({ visibilityProperty: true }) === false) return;
		draw(((now - started) % CYCLE_MS) / CYCLE_MS);
		show(true);
	}

	$effect(() => {
		context = canvas?.getContext('2d') ?? null;
		ink = canvas ? getComputedStyle(canvas) : null;
	});

	$effect(() => {
		if (!canvas || reduced()) {
			rest();
			return;
		}
		if (motion === 'process') {
			// A quick restart extends the same cycle.
			finishAt = Infinity;
			if (!frame) frame = requestAnimationFrame(roll);
		} else if (frame) {
			// Finish this roll at the star, or before it starts.
			const elapsed = started ? performance.now() - started : 0;
			finishAt = started + Math.ceil(elapsed / CYCLE_MS) * CYCLE_MS;
		}
	});

	onDestroy(() => cancelAnimationFrame(frame));
</script>

{#if motion === 'drop'}
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		aria-hidden="true"
		class="drop {className}"
		style:--size={`${size}px`}
	>
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
	</svg>
{:else}
	<span bind:this={box} aria-hidden="true" class="stack {className}">
		<svg bind:this={art} width={size} height={size} viewBox="0 0 24 24">
			<path d={STAR} stroke-width={STROKE}></path>
		</svg>
		<canvas bind:this={canvas} style:width={`${size}px`} style:height={`${size}px`}></canvas>
	</span>
{/if}

<style>
	svg {
		overflow: visible;
	}
	line {
		stroke: currentColor;
		stroke-width: calc(var(--size) / 7.5);
		stroke-linecap: round;
	}
	path {
		fill: none;
		stroke: currentColor;
		stroke-linecap: round;
	}
	.stack {
		display: inline-grid;
	}
	.stack > * {
		grid-area: 1 / 1;
	}
	canvas {
		visibility: hidden;
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
