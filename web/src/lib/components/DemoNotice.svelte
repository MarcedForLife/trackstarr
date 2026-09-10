<script lang="ts">
	// One line over every page of the demo build, so nobody mistakes the sample
	// library for a real one or expects a change to outlive the tab. Reloading
	// is the reset: the world is rebuilt from the catalogue.
</script>

<div
	class="flex items-center gap-3 border-b border-line bg-accent-soft px-4 py-1.5 pt-[calc(0.375rem+env(safe-area-inset-top))] text-[12.5px] text-dim lg:pt-1.5"
	role="note"
>
	<span class="min-w-0 flex-1 truncate">
		<span class="font-semibold text-fg">Demo.</span>
		A sample library of openly licensed films. Nothing here is real, and nothing is saved past a reload.
	</span>
	<button
		type="button"
		onclick={() => location.reload()}
		class="flex-none font-medium text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg"
	>
		Start over
	</button>
</div>
