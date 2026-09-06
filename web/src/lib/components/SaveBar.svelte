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
	<!-- The safe-area inset keeps the bar clear of a phone's home indicator, and
	     --tabbar clear of the tab bar sitting on top of it. -->
	<div
		class="sticky bottom-[calc(1rem+env(safe-area-inset-bottom)+var(--tabbar))] mt-6 flex flex-col rounded-xl border border-line-strong bg-raised py-2 pr-2 pl-3.5 shadow-lg sm:py-2.5 sm:pr-2.5 sm:pl-4 lg:bottom-5"
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
			<span class="h-1.5 w-1.5 flex-none rounded-full bg-accent"></span>
			<span class="min-w-0 flex-1 truncate text-[13px] text-dim">
				{settings.count} unsaved{settings.count === 1 ? ' change' : ' changes'}
			</span>
			<button onclick={() => settings.discard()} disabled={settings.busy} class={quiet}>
				Discard
			</button>
			<button onclick={() => settings.save()} disabled={settings.busy} class={primary}>Save</button>
		</div>
	</div>
{/if}
