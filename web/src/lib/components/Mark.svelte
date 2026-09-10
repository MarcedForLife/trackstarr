<script lang="ts">
	// The Trackstarr mark: a four-point star of five upright bars, a star from
	// across the room and a level meter up close. static/favicon.svg and the
	// home-screen icons are the same star on a #1b1d23 disc, redrawn by hand
	// when this changes.
	//
	// Still, it is one path. Moving, it is five round-capped strokes of the same
	// shape, so each bar can go on its own:
	// - drop: once, the bars falling in left to right with a bounce. The login page.
	// - travel: while set, a lub-dub squash runs through the bars left to right
	//   and then rests a random while. The chrome, while a run is on.
	// `press` answers the link around the mark, its nearest .mark-press ancestor:
	// the star inhales under the pointer or with focus, squashes while pressed,
	// and springs back from either.
	import { reduced } from '$lib/motion.svelte';

	let {
		size = 19,
		class: className = '',
		motion = 'none',
		press = false
	}: {
		size?: number;
		class?: string;
		motion?: 'none' | 'drop' | 'travel';
		press?: boolean;
	} = $props();

	// Bar centres and cores; the round caps of 1.6 make the path's silhouette.
	// The outer two are dots, given a hair of length so every browser draws a
	// cap. dir is which way a dot moves when the star widens.
	const BARS = [
		{ x: 2.4, top: 12, bottom: 12.001, kind: 'dot', dir: -1 },
		{ x: 7.2, top: 9.8, bottom: 14.2, kind: 'mid', dir: 0 },
		{ x: 12, top: 2.4, bottom: 21.6, kind: 'tall', dir: 0 },
		{ x: 16.8, top: 9.8, bottom: 14.2, kind: 'mid', dir: 0 },
		{ x: 21.6, top: 12, bottom: 12.001, kind: 'dot', dir: 1 }
	];

	// One beat, the mean rest after it, and the lag from one bar to the next.
	// The beat keyframes below are written against these. The rest is long: a
	// beat every second read as twitching from across the sidebar.
	const BEAT_MS = 1300;
	const REST_MS = 2600;
	const TRAVEL_MS = 60;

	const moving = $derived(motion !== 'none' || press);

	let root = $state<SVGSVGElement>();

	// Hovered: the link around the mark is under the pointer or has visible
	// focus. Pressed: a button or Enter is down on it. Watched from here rather
	// than styled off :hover and :active so a beat can give way to them; a
	// running animation would outrank the transition.
	let hovered = $state(false);
	let pressed = $state(false);
	const held = $derived(hovered || pressed);

	$effect(() => {
		const link = press ? root?.closest('.mark-press') : null;
		if (!link) return;
		// Hover only where there is a pointer, or a tap would leave it held. Kept
		// as flags rather than asked of :hover, which Firefox still reports true
		// while pointerleave is being handled. A press is any pointer, since a tap
		// is one, and lets go on leave and cancel so nothing can stick.
		const pointer = matchMedia('(hover: hover)');
		let hovering = false;
		let focused = false;
		const handlers: Record<string, (event: Event) => void> = {
			pointerenter: () => (hovering = pointer.matches),
			pointerleave: () => ((hovering = false), (pressed = false)),
			focusin: () => (focused = link.matches(':focus-visible')),
			focusout: () => ((focused = false), (pressed = false)),
			pointerdown: (event) => {
				if ((event as PointerEvent).button === 0) pressed = true;
			},
			pointerup: () => (pressed = false),
			pointercancel: () => (pressed = false),
			keydown: (event) => {
				if ((event as KeyboardEvent).key === 'Enter') pressed = true;
			},
			keyup: () => (pressed = false)
		};
		const listeners = Object.entries(handlers).map(([name, handler]) => {
			const listener = (event: Event) => {
				handler(event);
				hovered = hovering || focused;
			};
			link.addEventListener(name, listener);
			return [name, listener] as const;
		});
		return () => {
			listeners.forEach(([name, listener]) => link.removeEventListener(name, listener));
			hovered = false;
			pressed = false;
		};
	});

	// Travel: a beat is one run of the keyframes under .beating with fresh depths
	// each time, a hard squash, a soft echo at a third to three fifths of it,
	// and each mid a little off the other. Drawn while every bar is at rest, so
	// nothing jumps. A hover or press cuts a beat short and holds the next until
	// the pointer has gone.
	let beating = $state(false);
	let depths = $state(BARS.map(() => ({ hard: 1, soft: 0.5 })));
	let generation = 0;

	function between(low: number, high: number) {
		return low + Math.random() * (high - low);
	}

	$effect(() => {
		if (held) beating = false;
	});

	$effect(() => {
		if (motion !== 'travel' || reduced()) return;
		const mine = ++generation;
		let live = true;
		let midBeat = false;
		let timer: ReturnType<typeof setTimeout>;
		const rest = () => {
			midBeat = false;
			if (generation === mine) beating = false;
			if (live) timer = setTimeout(beat, REST_MS * between(0.65, 1.35));
		};
		const beat = () => {
			if (held) {
				rest();
				return;
			}
			const hard = between(0.75, 1.2);
			const soft = hard * between(0.35, 0.6);
			depths = BARS.map((bar) => {
				const side = bar.kind === 'mid' ? between(0.88, 1.12) : 1;
				return { hard: hard * side, soft: soft * side };
			});
			midBeat = true;
			beating = true;
			timer = setTimeout(rest, BEAT_MS + (BARS.length - 1) * TRAVEL_MS + 30);
		};
		timer = setTimeout(beat, between(400, 900));
		// A beat under way finishes and only the next is called off, so the mark
		// settles rather than snapping when the run ends.
		return () => {
			live = false;
			if (!midBeat) clearTimeout(timer);
		};
	});
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	aria-hidden="true"
	bind:this={root}
	class={className}
	class:drop={motion === 'drop'}
	class:press
	class:hovered
	class:pressed
	class:beating
	style:--size={moving ? `${size}px` : undefined}
	style:--beat={moving ? `${BEAT_MS}ms` : undefined}
	style:--travel={moving ? `${TRAVEL_MS}ms` : undefined}
>
	{#if moving}
		{#each BARS as bar, index (bar.x)}
			<!-- non-scaling-stroke keeps the caps round while a bar is scaled, and
			     measures the width in CSS px, hence --size. -->
			<line
				x1={bar.x}
				x2={bar.x}
				y1={bar.top}
				y2={bar.bottom}
				vector-effect="non-scaling-stroke"
				class={bar.kind}
				style:--i={index}
				style:--dir={bar.dir}
				style:--hard={depths[index].hard.toFixed(3)}
				style:--soft={depths[index].soft.toFixed(3)}
				style:transform-origin={`${bar.x}px 12px`}
			></line>
		{/each}
	{:else}
		<path
			fill="currentColor"
			d="M0.8 12a1.6 1.6 0 0 1 3.2 0v0a1.6 1.6 0 0 1-3.2 0zM5.6 9.8a1.6 1.6 0 0 1 3.2 0v4.4a1.6 1.6 0 0 1-3.2 0zM10.4 2.4a1.6 1.6 0 0 1 3.2 0v19.2a1.6 1.6 0 0 1-3.2 0zM15.2 9.8a1.6 1.6 0 0 1 3.2 0v4.4a1.6 1.6 0 0 1-3.2 0zM20 12a1.6 1.6 0 0 1 3.2 0v0a1.6 1.6 0 0 1-3.2 0z"
		></path>
	{/if}
</svg>

<style>
	svg {
		/* A squashed centre bar's caps pass the edge of the box. */
		overflow: visible;
		--attack: cubic-bezier(0.2, 0.8, 0.3, 1);
		--soft-out: cubic-bezier(0.22, 1, 0.36, 1);
		/* The hover release: a damped spring, or one bounce without linear(). */
		--spring: cubic-bezier(0.34, 1.56, 0.64, 1);
	}
	@supports (animation-timing-function: linear(0, 1)) {
		svg {
			--spring: linear(
				0,
				0.287,
				0.605,
				0.882,
				1.077,
				1.184,
				1.214,
				1.191,
				1.139,
				1.079,
				1.027,
				0.988,
				0.967,
				0.96,
				0.964,
				0.973,
				0.984,
				0.994,
				1.002,
				1.006,
				1.007,
				1.007,
				1
			);
		}
	}
	line {
		stroke: currentColor;
		stroke-width: calc(var(--size) / 7.5);
		stroke-linecap: round;
	}

	/* Drop: each bar falls from above and lands with a bounce, 70ms apart. */
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

	/* Travel: a fast squash and a damped wobble back, twice for a lub-dub. The
	   mids stretch as the centre shrinks and the dots step outward. --hard and
	   --soft are the two depths for this beat. */
	.beating .mid {
		animation: beat-mid var(--beat) both;
	}
	.beating .tall {
		animation: beat-tall var(--beat) both;
	}
	.beating .dot {
		animation: beat-dot var(--beat) both;
	}
	/* After the shorthands, which reset the delay, and at their specificity. */
	.beating :is(.mid, .tall, .dot) {
		animation-delay: calc(var(--i) * var(--travel));
	}
	@keyframes beat-mid {
		0% {
			transform: scaleY(1);
			animation-timing-function: var(--attack);
		}
		12% {
			transform: scaleY(calc(1 + 0.36 * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		24% {
			transform: scaleY(calc(1 - 0.08 * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		32% {
			transform: scaleY(calc(1 + 0.025 * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		38% {
			transform: scaleY(1);
			animation-timing-function: var(--attack);
		}
		48% {
			transform: scaleY(calc(1 + 0.36 * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		60% {
			transform: scaleY(calc(1 - 0.08 * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		68% {
			transform: scaleY(calc(1 + 0.025 * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		76%,
		100% {
			transform: scaleY(1);
		}
	}
	@keyframes beat-tall {
		0% {
			transform: scaleY(1);
			animation-timing-function: var(--attack);
		}
		12% {
			transform: scaleY(calc(1 - 0.12 * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		24% {
			transform: scaleY(calc(1 + 0.03 * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		32% {
			transform: scaleY(calc(1 - 0.01 * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		38% {
			transform: scaleY(1);
			animation-timing-function: var(--attack);
		}
		48% {
			transform: scaleY(calc(1 - 0.12 * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		60% {
			transform: scaleY(calc(1 + 0.03 * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		68% {
			transform: scaleY(calc(1 - 0.01 * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		76%,
		100% {
			transform: scaleY(1);
		}
	}
	@keyframes beat-dot {
		0% {
			transform: translateX(0);
			animation-timing-function: var(--attack);
		}
		12% {
			transform: translateX(calc(var(--dir) * 1px * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		24% {
			transform: translateX(calc(var(--dir) * -0.3px * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		32% {
			transform: translateX(calc(var(--dir) * 0.1px * var(--hard)));
			animation-timing-function: ease-in-out;
		}
		38% {
			transform: translateX(0);
			animation-timing-function: var(--attack);
		}
		48% {
			transform: translateX(calc(var(--dir) * 1px * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		60% {
			transform: translateX(calc(var(--dir) * -0.3px * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		68% {
			transform: translateX(calc(var(--dir) * 0.1px * var(--soft)));
			animation-timing-function: ease-in-out;
		}
		76%,
		100% {
			transform: translateX(0);
		}
	}

	/* Hovered: the star inhales, mids and centre up, dots out. Pressed: the
	   beat's squash. Every move but the press itself rides the spring, so
	   arriving, letting go and leaving all bounce; the press is a quick push. */
	.press line {
		transition: transform 640ms var(--spring);
	}
	.hovered .mid {
		transform: scaleY(1.22);
	}
	.hovered .tall {
		transform: scaleY(1.04);
	}
	.hovered .dot {
		transform: translateX(calc(var(--dir) * 1px));
	}
	.pressed line {
		transition: transform 120ms var(--attack);
	}
	.pressed .mid {
		transform: scaleY(1.34);
	}
	.pressed .tall {
		transform: scaleY(0.88);
	}
	.pressed .dot {
		transform: translateX(calc(var(--dir) * 1.2px));
	}
</style>
