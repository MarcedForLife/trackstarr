<script lang="ts">
	// One line over every page of the demo build, so nobody mistakes the sample
	// library for a real one or expects a change to outlive the tab. Reloading
	// is the reset: the world is rebuilt from the catalogue. The credits name
	// every poster and still shown, as their licences ask.
	import { credits } from '$lib/demo/posters';

	const shown = credits();
	let crediting = $state(false);

	const link =
		'flex-none font-medium text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg';
</script>

<div
	class="border-b border-line bg-accent-soft pt-[env(safe-area-inset-top)] text-[12.5px] text-dim lg:pt-0"
	role="note"
>
	<div class="flex items-center gap-3 px-4 py-1.5">
		<span class="min-w-0 flex-1 truncate">
			<span class="font-semibold text-fg">Demo.</span>
			A sample library of openly licensed films. Nothing here is real, and nothing is saved past a reload.
		</span>
		{#if shown.length}
			<button
				type="button"
				onclick={() => (crediting = !crediting)}
				aria-expanded={crediting}
				class={link}
			>
				Credits
			</button>
		{/if}
		<button type="button" onclick={() => location.reload()} class={link}>Start over</button>
	</div>
	{#if crediting}
		<p class="px-4 pb-2 leading-relaxed">
			Artwork from Wikimedia Commons, each under its own licence:
			{#each shown as credit, at (credit.id)}
				<!-- An address on Commons, not a route of ours. -->
				<!-- eslint-disable svelte/no-navigation-without-resolve -->
				<a
					href={credit.url}
					target="_blank"
					rel="noreferrer"
					class="text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg"
					>{credit.name}</a
				>
				<!-- eslint-enable svelte/no-navigation-without-resolve -->
				<span class="whitespace-nowrap"
					>({credit.by}, {credit.licence}){at < shown.length - 1 ? ' · ' : '.'}</span
				>
			{/each}
			The files, verdicts and history are invented.
		</p>
	{/if}
</div>
