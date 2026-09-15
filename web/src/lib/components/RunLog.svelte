<script lang="ts">
	import { onMount } from 'svelte';
	import Sheet, { SLIDE } from './Sheet.svelte';
	import { button } from '$lib/controls';
	import { overlay } from '$lib/overlay';
	import { portal } from '$lib/portal';
	import { titled } from '$lib/runs';

	// A worker's output over the page rather than in the row, which gave hundreds
	// of lines a well six deep. The caller reads them, on the service's own poll.
	let {
		path,
		lines,
		failure,
		onclose
	}: { path: string; lines: string[] | null; failure: string; onclose: () => void } = $props();

	// Raised on mount, lowered before the caller unmounts it, so both directions
	// animate.
	let open = $state(false);
	const sheet = overlay({
		name: 'log',
		close: () => {
			open = false;
			setTimeout(onclose, SLIDE);
		}
	});

	onMount(() => {
		open = true;
		sheet.raise();
	});

	// Newest at the bottom, as a log reads, so it opens at its end. Unless the
	// reader has scrolled off it.
	let tail: HTMLDivElement | undefined = $state();
	let following = $state(true);

	$effect(() => {
		void lines;
		if (tail && following) tail.scrollTop = tail.scrollHeight;
	});

	// A hair of slack: a line's own height would drop the follow on every arrival.
	function scrolled(event: Event & { currentTarget: HTMLDivElement }) {
		const box = event.currentTarget;
		following = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
	}
</script>

<!-- The row's panel is transformed and clipped as it opens, which a sheet left
     inside it would be placed against. -->
<div use:portal>
	<Sheet {open} onclose={() => sheet.lower()} label={`Worker log for ${titled(path)}`}>
		<div class="flex max-h-[min(44rem,calc(88dvh-1.5rem))] min-h-0 flex-col">
			<header class="flex items-start justify-between gap-3 px-4 pt-3">
				<div class="min-w-0">
					<h2 class="text-base font-semibold tracking-tight">Worker log</h2>
					<p class="mt-1 font-mono text-[11px] wrap-anywhere text-faint">{path}</p>
				</div>
				<button class={button} onclick={() => sheet.lower()}>Close</button>
			</header>
			<div
				bind:this={tail}
				onscroll={scrolled}
				class="mt-3 min-h-0 flex-1 space-y-1.5 overflow-y-auto overscroll-contain border-t border-line px-4 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
			>
				{#if failure}
					<p role="alert" class="text-[12px] text-danger">{failure}</p>
				{:else if lines === null}
					<p class="text-[12px] text-faint">Reading the log…</p>
				{:else if !lines.length}
					<!-- A file far enough back has had its lines dropped. -->
					<p class="text-[12px] text-faint">No log kept for this file.</p>
				{:else}
					{#each lines as line, at (at)}
						<p class="font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-dim">{line}</p>
					{/each}
				{/if}
			</div>
		</div>
	</Sheet>
</div>
