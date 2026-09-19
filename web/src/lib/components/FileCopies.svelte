<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import { copyOptions, type FileGroup } from '$lib/copies';
	import type { LibraryFile } from '$lib/library';
	import Segmented from './Segmented.svelte';

	let {
		group,
		selections,
		disabled = false,
		onchange,
		children
	}: {
		group: FileGroup;
		selections: Record<string, string>;
		disabled?: boolean;
		onchange: () => void;
		children: Snippet<[LibraryFile, boolean]>;
	} = $props();
	const selected = $derived(selections[group.key]);
	let replaced = $state(false);
	// A disappeared file falls back to the first remaining one. A refreshed
	// verdict never moves the reader to a different copy.
	const file = $derived(group.files.find((file) => file.path === selected) ?? group.files[0]);
	$effect(() => {
		if (!group.files.some((file) => file.path === selected)) {
			const hadSelection = selected !== undefined;
			selections[group.key] = group.files[0].path;
			if (hadSelection) {
				replaced = true;
				untrack(onchange);
			}
		}
	});
	const attention = $derived(
		group.files.filter((file) => file.status === 'pending' || file.status === 'failed').length
	);
	const options = $derived.by(() => {
		const names = group.files.map((file) => file.source?.trim() ?? '');
		const named =
			names.every((name) => name && name.length <= 16) &&
			new Set(names).size === names.length &&
			names.length <= 3;
		return copyOptions(group.files).map((option, at) => ({
			value: option.value,
			label: named ? names[at] : letter(at),
			title: `${option.label} · ${option.value}`
		}));
	});

	function letter(index: number): string {
		let label = '';
		for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) {
			label = String.fromCharCode(65 + ((value - 1) % 26)) + label;
		}
		return label;
	}
</script>

{#if group.files.length === 1}
	{#if replaced}
		<li role="status" class="text-[12px] text-dim">
			The selected file disappeared. Showing the remaining variant.
		</li>
	{/if}
	{@render children(file, false)}
{:else}
	<li class="min-w-0 rounded-xl border border-line bg-sunken">
		<div class="border-b border-line px-3 py-2.5">
			<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
				<span class="text-[12px] font-medium text-dim">
					{group.label === 'Film' ? 'Movie' : group.label}
					<span class="ml-1.5 font-normal text-faint">{group.files.length} variants</span>
				</span>
				{#if attention}
					<span class="text-[11px] text-dim">
						{attention}
						{attention === 1 ? 'variant needs' : 'variants need'} attention.
					</span>
				{/if}
			</div>
			<div class="mt-2 overflow-x-auto">
				<div style:min-width={options.length > 4 ? `${options.length * 44}px` : undefined}>
					<Segmented
						fill
						tight
						label={`File variant for ${group.label}`}
						value={file.path}
						{disabled}
						{options}
						onchange={(path) => {
							onchange();
							selections[group.key] = path;
							replaced = false;
						}}
					/>
				</div>
			</div>
			{#if replaced}
				<p role="status" class="mt-2 text-[12px] text-dim">
					The selected file disappeared. Showing another variant.
				</p>
			{/if}
		</div>
		{@render children(file, true)}
	</li>
{/if}
