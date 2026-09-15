<script lang="ts">
	import { fade } from 'svelte/transition';
	import { rowFade } from '$lib/motion.svelte';

	// A progress bar: a run against its files, or a file against its running
	// time. scaleX rather than width, which would relayout everything under it.
	let {
		// How full, 0 to 1. Separate from the numbers so a finished run fills it
		// whatever the last snapshot caught. Unused on an indeterminate bar.
		fill = 0,
		// What a screen reader is told, in the units the thing is counted in.
		now = 0,
		max = 0,
		text,
		// Work with nothing to measure: a segment crosses the track instead. The
		// caller decides whether the reader wants the movement.
		indeterminate = false,
		// A file's bar is thinner, and moves once a second.
		file = false,
		// Eased linearly, so a bar stepping once a second reads as one line. A bar
		// that only moves on a snapshot eases out instead.
		glide = file,
		// A finished run, the one time the fill is not the accent.
		done = false,
		class: extra = ''
	}: {
		fill?: number;
		now?: number;
		max?: number;
		text: string;
		indeterminate?: boolean;
		file?: boolean;
		glide?: boolean;
		done?: boolean;
		class?: string;
	} = $props();
</script>

<!-- No value is how a screen reader is told a bar measures nothing. -->
<div
	role="progressbar"
	aria-valuemin={indeterminate ? undefined : 0}
	aria-valuemax={indeterminate ? undefined : max}
	aria-valuenow={indeterminate ? undefined : now}
	aria-valuetext={text}
	class={`relative overflow-hidden rounded-full bg-sunken ${
		file ? 'h-[3px] min-w-0 flex-1' : 'h-1'
	} ${extra}`}
>
	<!-- Both out of flow, so the segment fades off the track as the fill fades
	     up once there is something to measure. -->
	{#if indeterminate}
		<div
			class="crossing absolute inset-y-0 left-0 w-[30%] rounded-full bg-accent-fill/70"
			transition:fade={rowFade()}
		></div>
	{:else}
		<div
			class={`absolute inset-0 origin-left rounded-full transition-transform ${
				glide ? 'duration-1000 ease-linear' : 'duration-500 ease-out'
			} ${file ? 'bg-accent-fill/70' : done ? 'bg-ok' : 'bg-accent-fill'}`}
			style={`transform: scaleX(${fill})`}
			transition:fade={rowFade()}
		></div>
	{/if}
</div>

<style>
	/* Off one end of the track to off the other: 334% of the segment's width.
	   Linear, or an eased pass would dawdle at both ends with the track empty. */
	.crossing {
		animation: cross 1.15s linear infinite;
	}

	@keyframes cross {
		from {
			transform: translateX(-100%);
		}
		to {
			transform: translateX(334%);
		}
	}
</style>
