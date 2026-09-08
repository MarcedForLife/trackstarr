<script lang="ts">
	import type { Snippet } from 'svelte';
	import EnvBadge from '$lib/components/EnvBadge.svelte';

	// A two-column grid: label and description stack on the left, the control
	// beside both. A `stack` control drops to its own row.
	let {
		label,
		desc,
		env = false,
		align = 'center',
		stack = false,
		full = false,
		nested = false,
		dim = false,
		children
	}: {
		label: string;
		desc: string;
		env?: boolean;
		align?: 'center' | 'start';
		stack?: boolean;
		// A list control, too wide for the right column at any width.
		full?: boolean;
		// A setting the row above governs, indented behind a rule.
		nested?: boolean;
		// The governing row is off. Dimmed; the caller disables the control.
		dim?: boolean;
		children: Snippet<[{ labelledBy: string; describedBy: string }]>;
	} = $props();

	// The ids the control is named and described by. By id rather than <label>,
	// since most controls are a group of buttons, and an aria-label left the
	// description and ENV badge unread.
	const id = $props.id();
	const labelledBy = `${id}-label`;
	const describedBy = `${id}-desc`;

	// Each branch spells out its placement: competing utilities are settled by
	// stylesheet order.
	const controlClass = $derived(
		[
			'flex min-w-0',
			align === 'start' ? 'self-start' : 'self-center',
			full
				? 'col-span-2 col-start-1 row-start-3 mt-3 justify-start sm:justify-end'
				: stack
					? 'col-span-2 col-start-1 row-start-3 mt-2.5 justify-start sm:col-span-1 sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0 sm:justify-end'
					: 'col-start-2 row-span-2 row-start-1 justify-end'
		].join(' ')
	);
</script>

<div
	class={[
		// One control column from sm, so every description wraps at one width
		// rather than at whatever sits beside it. Below sm a stacked control has
		// its own row and an inline one needs the room.
		'grid grid-cols-[minmax(0,1fr)_auto] grid-rows-[auto_1fr] items-center gap-x-4 border-t border-line py-4 sm:grid-cols-[minmax(0,1fr)_21rem] sm:gap-x-8 sm:py-3.5',
		// The left rule is the row's own border, so nested rows join into one line.
		nested && 'border-l pl-3.5 sm:pl-5',
		dim && 'opacity-50'
	]}
>
	<p
		id={labelledBy}
		class="col-start-1 row-start-1 flex flex-wrap items-center gap-2 text-sm font-medium"
	>
		{label}
		{#if env}
			<EnvBadge />
		{/if}
	</p>
	<p
		id={describedBy}
		class="col-start-1 row-start-2 mt-0.5 self-start text-[13px] leading-snug text-pretty text-dim"
	>
		{desc}
	</p>
	<div class={controlClass}>
		{@render children({ labelledBy, describedBy })}
	</div>
</div>
