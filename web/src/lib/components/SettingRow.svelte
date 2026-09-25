<script lang="ts">
	import type { Snippet } from 'svelte';
	import EnvBadge from '$lib/components/EnvBadge.svelte';
	import { pageSettings } from '$lib/draft.svelte';

	// A two-column grid: label and description stack on the left, the control
	// beside both. A `stack` control drops to its own row.
	let {
		name = '',
		label,
		desc,
		note = '',
		align = 'center',
		stack = false,
		full = false,
		dim = false,
		children,
		below,
		nested
	}: {
		// The setting this row is for. The ENV badge, the unsaved dot and the
		// sentence about the environment all follow from it, so the row says it
		// once. Empty for a row that presses a button rather than holding a value.
		name?: string;
		label: string;
		// What the setting is for, replaced when the environment owns the value.
		desc: string;
		// A sentence about the value held rather than about the setting, so it
		// survives that replacement.
		note?: string;
		align?: 'center' | 'start';
		stack?: boolean;
		// A list control, too wide for the right column at any width.
		full?: boolean;
		// The governing row is off. Dimmed; the caller disables the control.
		dim?: boolean;
		children: Snippet<[{ labelledBy: string; describedBy: string }]>;
		// A list the control adds to, full width under the row.
		below?: Snippet<[{ labelledBy: string; describedBy: string }]>;
		// The rows this one governs, in a tray under its control.
		nested?: Snippet;
	} = $props();

	// Every settings page provides its draft, so a named row reads its own state
	// off it.
	const settings = pageSettings();
	const env = $derived(!!name && !!settings?.baseline[name]?.env);
	const changed = $derived(!!name && !!settings?.changed(name));
	const sentence = $derived.by(() => {
		const said = name && settings ? settings.desc(name, desc) : desc;
		return note ? `${said} ${note}` : said;
	});

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
		'grid grid-cols-[minmax(0,1fr)_auto] grid-rows-[auto_1fr] items-center gap-x-4 border-t border-line py-4 sm:grid-cols-[minmax(0,1fr)_var(--control-column,21rem)] sm:gap-x-8 sm:py-3.5',
		dim && 'opacity-50'
	]}
>
	<p
		id={labelledBy}
		class="col-start-1 row-start-1 flex flex-wrap items-center gap-2 text-sm font-medium"
	>
		{label}
		{#if changed}
			<span class="h-1.5 w-1.5 flex-none rounded-full bg-accent-fill"></span>
			<span class="sr-only">Unsaved</span>
		{/if}
		{#if env}
			<EnvBadge />
		{/if}
	</p>
	<p
		id={describedBy}
		class="col-start-1 row-start-2 mt-0.5 self-start text-[13px] leading-snug text-pretty text-dim"
	>
		{sentence}
	</p>
	<div class={controlClass}>
		{@render children({ labelledBy, describedBy })}
	</div>
	{#if below}
		<div class="col-span-2 col-start-1 row-start-3 min-w-0">
			{@render below({ labelledBy, describedBy })}
		</div>
	{/if}
	<!-- The control column is narrower by the tray's inset, so it lines up with
	     the page's. -->
	{#if nested}
		<div
			class="col-span-2 col-start-1 row-start-4 mt-4 min-w-0 rounded-xl border border-line bg-sunken px-3.5 [--control-column:calc(21rem-1rem-1px)] sm:mt-3.5 sm:px-4 [&>*:first-child]:border-t-0"
		>
			{@render nested()}
		</div>
	{/if}
</div>
