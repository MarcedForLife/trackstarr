<script lang="ts">
	import { primary, quiet } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';

	// The bar that follows an edited page down the screen, and where a refused
	// save says why. Every settings page saves the same way.
	let { settings }: { settings: SettingsDraft } = $props();
</script>

<!-- Mounted whether the bar is or not, because the bar going is the answer it
     has to read out: a live region that leaves the page with the press cannot
     say what the press did. Only while there is nothing left to save, so a
     fresh edit takes the last answer off rather than leaving it standing. -->
<p role="status" class="sr-only">{settings.count ? '' : settings.answered}</p>

{#if settings.count}
	<!-- Below sm the bar spans the screen and sits on the tab bar, so it reads as
	     chrome rather than as one more settings row. -mb-10 cancels the page's
	     2.5rem bottom padding, so where it comes to rest is where it already
	     stands and the last scroll cannot lift it. From sm it is an inset card
	     floating 1rem clear. The safe-area inset keeps it off a phone's home
	     indicator, and --tabbar off the tab bar over that. -->
	<div
		class="bar sticky bottom-[calc(env(safe-area-inset-bottom)+var(--tabbar))] z-20 -mx-5 mt-6 -mb-10 flex flex-col rounded-t-xl rounded-b-none border border-b-0 border-accent/40 bg-raised/95 py-2 pr-3 pl-5 shadow-float backdrop-blur-md sm:bottom-[calc(1rem+env(safe-area-inset-bottom)+var(--tabbar))] sm:mx-0 sm:mb-0 sm:rounded-xl sm:border-b sm:py-2.5 sm:pr-2.5 sm:pl-4 lg:bottom-5"
	>
		<!-- With the bar rather than at the foot of the page: a refusal lands on
		     the rules page a screen and a half below where the reader is looking.
		     The region is here empty rather than arriving with its words, since a
		     live region and its content appearing in the one update is a change
		     some readers have nothing to compare against. It carries its own
		     spacing, so an empty one costs the bar no height. -->
		<div role="alert" class={settings.problems.length ? 'pt-0.5 pr-1.5 pb-2' : ''}>
			<ul class="flex flex-col gap-1 text-[13px] text-danger">
				{#each settings.problems as problem (problem)}
					<li>{problem}</li>
				{/each}
			</ul>
		</div>
		<div class="flex items-center gap-2 sm:gap-3">
			<!-- The dot a changed row wears too, so the count and what it counts are
			     marked alike. -->
			<span class="h-1.5 w-1.5 flex-none rounded-full bg-accent"></span>
			<span class="min-w-0 flex-1 truncate text-[13px] font-medium">
				{settings.count} unsaved{settings.count === 1 ? ' change' : ' changes'}
			</span>
			<button onclick={() => settings.discard()} disabled={settings.busy} class={quiet}>
				Discard
			</button>
			<!-- Wider than the shape every other control shares, since it is the one
			     press the bar exists for. -->
			<button
				onclick={() => settings.save()}
				disabled={settings.busy}
				class={`${primary} min-w-24`}
			>
				Save
			</button>
		</div>
	</div>
{/if}

<style>
	/* Rising from under the edge it sits on, so it arrives in the corner of the
	   eye rather than appearing already there. 280ms out-cubic matches the
	   selection bar; layout.css flattens it under reduced motion. */
	.bar {
		animation: bar-rise 280ms cubic-bezier(0.33, 1, 0.68, 1);
	}

	@keyframes bar-rise {
		from {
			transform: translateY(calc(100% + 0.75rem));
			opacity: 0;
		}
	}
</style>
