<script lang="ts">
	import { control } from '$lib/controls';

	// A setting whose answers are the same thing more of, where a segmented
	// switch is for choices that merely differ. Notched, so each stop is a word.
	let {
		options,
		value,
		labelledBy,
		describedBy,
		onchange
	}: {
		options: { value: string; label: string }[];
		value: string;
		// The SettingRow's two paragraphs, by id.
		labelledBy: string;
		describedBy: string;
		onchange: (value: string) => void;
	} = $props();

	const last = $derived(options.length - 1);
	// An unknown value is a bug; the first stop is the least surprising place
	// for it.
	const index = $derived(
		Math.max(
			0,
			options.findIndex((option) => option.value === value)
		)
	);
	const current = $derived(options[index]);
	// How far along the thumb is, 0 to 1; the fill and notches use it too.
	const along = $derived(last > 0 ? index / last : 0);
</script>

<div class="w-full sm:w-72" style={`--along: ${along}`}>
	<!-- The stop's name, above the track and still. -->
	<p class="text-[13px] font-semibold">{current?.label}</p>

	<!-- Full control height, so the target is thumb-sized. -->
	<div class={`relative flex w-full items-center ${control}`}>
		<!-- Inset by half a thumb, the box the thumb's centre travels in. -->
		<span aria-hidden="true" class="rail"></span>
		<span aria-hidden="true" class="notches">
			{#each options as option, i (option.value)}
				<span class={`notch ${i <= index ? 'is-past' : ''}`}></span>
			{/each}
		</span>
		<input
			type="range"
			min="0"
			max={last}
			step="1"
			value={index}
			aria-labelledby={labelledBy}
			aria-describedby={describedBy}
			aria-valuetext={current?.label}
			oninput={(event) => onchange(options[event.currentTarget.valueAsNumber]?.value ?? value)}
		/>
	</div>

	<!-- The two ends. Hidden from the accessibility tree; aria-valuetext has
	     the stop's word. -->
	<div aria-hidden="true" class="mt-0.5 flex justify-between text-[11px] text-faint">
		<span>{options[0]?.label}</span>
		<span>{options[last]?.label}</span>
	</div>
</div>

<style>
	.rail,
	.notches {
		position: absolute;
		inset: 0 10px;
		pointer-events: none;
	}

	.rail {
		top: 50%;
		bottom: auto;
		height: 6px;
		margin-top: -3px;
		border-radius: 9999px;
		/* A hard stop: the fill's edge lands under the thumb. */
		background: linear-gradient(
			to right,
			var(--accent) 0 calc(var(--along) * 100%),
			var(--toggle-off) calc(var(--along) * 100%) 100%
		);
	}

	.notches {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	/* Punched out of the track, so each side takes the colour it sits in. */
	.notch {
		height: 3px;
		width: 3px;
		border-radius: 9999px;
		background: var(--surface);
		opacity: 0.55;
	}

	.notch.is-past {
		background: var(--on-accent);
		opacity: 0.5;
	}

	input[type='range'] {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		margin: 0;
		appearance: none;
		-webkit-appearance: none;
		/* The track is drawn behind; the input contributes the thumb and hit area. */
		background: transparent;
		/* A drag across is this control's; a drag down is the page's. */
		touch-action: pan-y;
		cursor: pointer;
	}

	/* One thumb written twice, for both engines. */
	input[type='range']::-webkit-slider-runnable-track {
		height: 6px;
		background: transparent;
	}

	input[type='range']::-webkit-slider-thumb {
		-webkit-appearance: none;
		height: 20px;
		width: 20px;
		/* WebKit hangs the thumb from the track's top; this centres it. */
		margin-top: -7px;
		border-radius: 9999px;
		border: 2px solid var(--accent);
		background: var(--knob);
		box-shadow:
			0 1px 3px rgb(0 0 0 / 0.3),
			0 0 0 0 var(--accent-soft);
		transition: box-shadow 200ms ease-out;
	}

	input[type='range']::-moz-range-track {
		height: 6px;
		background: transparent;
	}

	input[type='range']::-moz-range-thumb {
		height: 20px;
		width: 20px;
		border-radius: 9999px;
		border: 2px solid var(--accent);
		background: var(--knob);
		box-shadow:
			0 1px 3px rgb(0 0 0 / 0.3),
			0 0 0 0 var(--accent-soft);
		transition: box-shadow 200ms ease-out;
	}

	/* The toggle's halo: a finger covers the thumb, so the answer shows around
	   it. */
	input[type='range']:active::-webkit-slider-thumb {
		box-shadow:
			0 1px 3px rgb(0 0 0 / 0.3),
			0 0 0 7px var(--accent-soft);
	}

	input[type='range']:active::-moz-range-thumb {
		box-shadow:
			0 1px 3px rgb(0 0 0 / 0.3),
			0 0 0 7px var(--accent-soft);
	}
</style>
