<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';

	// Both halves are hooks.client.ts's to say, since it is the one place that
	// knows what happened; the status is the heading only where that had nothing
	// better to offer, which for an unreachable service it does.
	const heading = $derived(page.error?.heading ?? String(page.status));
</script>

<svelte:head><title>{heading} · Trackstarr</title></svelte:head>

<main
	class="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 px-6 pt-[calc(1.5rem+env(safe-area-inset-top))] pb-[calc(1.5rem+env(safe-area-inset-bottom))]"
>
	<div>
		<h1 class="text-2xl font-semibold">{heading}</h1>
		<p class="text-sm text-dim">{page.error?.message}</p>
	</div>
	<a href={resolve('/')} class="text-sm text-dim underline hover:text-fg">back to trackstarr</a>
</main>
