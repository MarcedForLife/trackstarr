<script lang="ts">
	// A progress bar: a run against its files, or a file against its running
	// time. scaleX rather than width, which would relayout everything under it.
	let {
		// How full, 0 to 1. Separate from the numbers so a finished run fills it
		// whatever the last snapshot caught.
		fill,
		// What a screen reader is told, in the units the thing is counted in.
		now,
		max,
		text,
		// A file's bar is thinner, and moves once a second.
		file = false,
		// Eased linearly, so a bar stepping once a second reads as one line. A bar
		// that only moves on a snapshot eases out instead.
		glide = file,
		// A finished run, the one time the fill is not the accent.
		done = false,
		class: extra = ''
	}: {
		fill: number;
		now: number;
		max: number;
		text: string;
		file?: boolean;
		glide?: boolean;
		done?: boolean;
		class?: string;
	} = $props();
</script>

<div
	role="progressbar"
	aria-valuemin={0}
	aria-valuemax={max}
	aria-valuenow={now}
	aria-valuetext={text}
	class={`overflow-hidden rounded-full bg-sunken ${
		file ? 'h-[3px] min-w-0 flex-1' : 'h-1'
	} ${extra}`}
>
	<div
		class={`h-full origin-left rounded-full transition-transform ${
			glide ? 'duration-1000 ease-linear' : 'duration-500 ease-out'
		} ${file ? 'bg-accent-fill/70' : done ? 'bg-ok' : 'bg-accent-fill'}`}
		style={`transform: scaleX(${fill})`}
	></div>
</div>
