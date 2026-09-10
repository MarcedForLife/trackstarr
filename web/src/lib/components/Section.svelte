<script lang="ts">
	import type { Snippet } from 'svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import { pageSettings } from '$lib/draft.svelte';

	// One foldable group of a long settings page. `note` makes the fold honest:
	// a shut group still answers what it holds.
	let {
		heading,
		note = '',
		open = false,
		names = [],
		// The Appearance rows are boxes rather than a ruled list, so they want air.
		spaced = false,
		children
	}: {
		heading: string;
		note?: string;
		// Whether the group starts open, as the first on a page does.
		open?: boolean;
		// The settings this group holds. Shut, its rows are unmounted and cannot
		// wear the unsaved dot, so the heading wears it for them.
		names?: string[];
		spaced?: boolean;
		children: Snippet;
	} = $props();

	const settings = pageSettings();
	const changed = $derived(!!settings?.anyChanged(names));

	// The starting state; the reader owns it from here.
	// svelte-ignore state_referenced_locally
	let shown = $state(open);

	const id = $props.id();
</script>

<!-- Sized between the title and a row label, or it reads as one more row. The
     note is right-aligned so shut groups answer down a column, and truncates
     before the heading does. -->
{#snippet line(chevron: Snippet)}
	<span class="flex-none text-[17px] font-semibold tracking-tight">{heading}</span>
	{#if changed}
		<span class="-ml-1.5 h-1.5 w-1.5 flex-none rounded-full bg-accent-fill"></span>
		<span class="sr-only">Unsaved changes</span>
	{/if}
	<span class="min-w-0 flex-1 truncate text-right text-[12.5px] text-dim">{note}</span>
	{@render chevron()}
{/snippet}

<!-- The rule belongs to the group, so the gap sits over it. -->
<section class="mt-6 border-t border-line">
	<!-- The whole row is the target, well over 44px. `relative` positions the
	     ripple; the clip beside it is the caller's call. -->
	<Disclosure
		id={`${id}-panel`}
		open={shown}
		ontoggle={() => (shown = !shown)}
		class="group relative flex w-full items-center gap-3 overflow-hidden pt-4 pb-3.5 text-left"
		mark={13}
		summary={line}
		panelClass={spaced ? 'pb-2' : ''}
		press
	>
		<!-- Every row draws its own top border. The first loses it, or the heading
		     floats between two lines; a spaced group loses all of them, since the
		     gap is the separator. -->
		{#snippet panel()}
			<div class={spaced ? 'flex flex-col gap-6 [&>*]:border-t-0' : '[&>*:first-child]:border-t-0'}>
				{@render children()}
			</div>
		{/snippet}
	</Disclosure>
</section>
