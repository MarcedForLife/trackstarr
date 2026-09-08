<script lang="ts">
	import Glyph from '$lib/components/Glyph.svelte';
	import { chip, removeButton, selectCell } from '$lib/controls';
	import type { Reorder } from '$lib/reorder.svelte';

	// One row of a reorderable settings list: the drag handle, the name, what
	// happens to it, and the cross. Whatever sits between the action and the
	// cross is the caller's, which for a layout is the encoder and rate.
	let {
		name,
		label,
		width,
		action,
		index,
		orderable,
		actions,
		actionLabels,
		actionWidth = 'w-[6.5rem] flex-none sm:w-24',
		locked,
		drag,
		onaction,
		onremove,
		children
	}: {
		name: string;
		// What the chip shows, where that is not the stored name.
		label?: string;
		// How the chip takes its width, the same down the list so columns line up.
		width: string;
		action: string;
		index: number;
		// Rows below this are removals: fixed at the bottom, no handle.
		orderable: number;
		actions: string[];
		actionLabels: Record<string, string>;
		// How the action takes its width. Its own column by default, so the fields
		// beside it line up; a row with nothing to configure hands it the slack.
		actionWidth?: string;
		locked: boolean;
		drag: Reorder;
		onaction: (action: string) => void;
		onremove: () => void;
		children?: import('svelte').Snippet;
	} = $props();
</script>

<div
	data-reorder-row
	class={`flex flex-wrap items-center gap-2 ${
		drag.from === index ? 'relative z-10' : 'transition-transform duration-150'
	}`}
	style={`transform: translateY(${drag.from === index ? drag.offset : drag.shift(index)}px)`}
>
	<!-- The handle owns the drag, so the fields stay selectable. `touch-none`
	     makes the drag possible: under `manipulation` the browser took it for a
	     scroll and cancelled the pointer. A removed row gives up the handle but
	     keeps the space, so the columns line up. -->
	{#if index < orderable}
		<button
			aria-label={`Move ${label ?? name}, ${index + 1} of ${orderable}`}
			disabled={locked}
			onpointerdown={(event) => drag.grab(event, index)}
			onpointermove={drag.drag}
			onpointerup={drag.drop}
			onpointercancel={drag.drop}
			onkeydown={(event) => drag.keys(event, index)}
			class="-m-2 touch-none rounded p-2 text-faint not-disabled:cursor-grab hover:text-fg disabled:opacity-40"
		>
			<svg width="12" height="16" viewBox="0 0 12 16" fill="currentColor">
				<circle cx="4" cy="4" r="1.3"></circle>
				<circle cx="8" cy="4" r="1.3"></circle>
				<circle cx="4" cy="8" r="1.3"></circle>
				<circle cx="8" cy="8" r="1.3"></circle>
				<circle cx="4" cy="12" r="1.3"></circle>
				<circle cx="8" cy="12" r="1.3"></circle>
			</svg>
		</button>
	{:else}
		<span class="w-3 flex-none" aria-hidden="true"></span>
	{/if}
	<span
		class={`${chip} ${width} justify-center px-2.5 ${
			action === 'remove' ? 'border-danger/40 text-danger' : ''
		} ${drag.from === index ? 'shadow-lg' : ''}`}
		title={name}
	>
		{label ?? name}
	</span>
	<select
		value={action}
		onchange={(event) => onaction(event.currentTarget.value)}
		aria-label={`What happens to ${label ?? name}`}
		disabled={locked}
		class={`${selectCell} ${actionWidth}`}
	>
		{#each actions as option (option)}
			<option value={option}>{actionLabels[option] ?? option}</option>
		{/each}
	</select>
	{@render children?.()}
	<button
		aria-label={`Remove ${label ?? name}`}
		disabled={locked}
		onclick={onremove}
		class={`${removeButton} ml-auto`}
	>
		<Glyph name="cross" />
	</button>
</div>
