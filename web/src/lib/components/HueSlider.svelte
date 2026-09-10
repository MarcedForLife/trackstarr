<script lang="ts">
	import { spectrum, type Theme } from '$lib/hue';

	// The wheel laid flat. The track is the accent fill at each hue in the
	// current theme, so what the thumb sits on is what the page would get;
	// the thumb is the fill at the hue chosen, ringed in the knob colour.
	let {
		value,
		fill,
		theme,
		onchange
	}: {
		value: number;
		fill: string;
		theme: Theme;
		onchange: (hue: number) => void;
	} = $props();

	const track = $derived(`linear-gradient(to right, ${spectrum(theme).join(', ')})`);
</script>

<!-- `input` fires for every step of a drag and for every arrow, so the page
     follows the thumb. -->
<input
	type="range"
	min="0"
	max="359"
	step="1"
	{value}
	aria-label="Hue"
	aria-valuetext={`${value}°`}
	oninput={(event) => onchange(Number(event.currentTarget.value))}
	class="hue h-11 w-full"
	style={`--track: ${track}; --thumb: ${fill}`}
/>

<style>
	input[type='range'] {
		margin: 0;
		appearance: none;
		-webkit-appearance: none;
		background: transparent;
		/* A drag across is this control's; a drag down is the page's. */
		touch-action: pan-y;
		cursor: pointer;
	}

	/* One track and one thumb, each written twice, for both engines. The
	   track is 10px; the thumb overhangs it by 6px a side. */
	input[type='range']::-webkit-slider-runnable-track {
		height: 10px;
		border-radius: 9999px;
		border: 1px solid var(--line-strong);
		background: var(--track);
	}

	input[type='range']::-webkit-slider-thumb {
		-webkit-appearance: none;
		height: 22px;
		width: 22px;
		/* WebKit hangs the thumb from the track's top; this centres it. */
		margin-top: -7px;
		border-radius: 9999px;
		border: 3px solid var(--knob);
		background: var(--thumb);
		box-shadow:
			0 1px 3px rgb(0 0 0 / 0.35),
			0 0 0 1px var(--line-strong);
	}

	input[type='range']::-moz-range-track {
		height: 10px;
		border-radius: 9999px;
		border: 1px solid var(--line-strong);
		background: var(--track);
	}

	input[type='range']::-moz-range-thumb {
		height: 22px;
		width: 22px;
		border-radius: 9999px;
		border: 3px solid var(--knob);
		background: var(--thumb);
		box-shadow:
			0 1px 3px rgb(0 0 0 / 0.35),
			0 0 0 1px var(--line-strong);
	}
</style>
