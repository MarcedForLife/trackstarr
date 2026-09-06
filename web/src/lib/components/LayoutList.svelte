<script lang="ts">
	import { tick } from 'svelte';
	import EnvBadge from '$lib/components/EnvBadge.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { cell, chip, ghost, removeButton } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';
	import { STOCK_BITRATES, STOCK_CODECS, bitrateName, codecName } from '$lib/rules';
	import type { Codec } from '$lib/settings';

	// The layouts a downmix guarantees, each with its encoder and rate, in audio
	// track order. A list to drag rather than chips, since the order matters.
	// Owns the list and the two variables per entry, since adding one seeds both.
	let {
		settings,
		codecs,
		labelledBy,
		describedBy
	}: {
		settings: SettingsDraft;
		codecs: Codec[];
		// The SettingRow's paragraphs, which describe the list as a whole.
		labelledBy: string;
		describedBy: string;
	} = $props();

	// Both records are mutated in place, so these read the live objects after a
	// save.
	const draft = $derived(settings.draft);
	const baseline = $derived(settings.baseline);

	const layouts = $derived((draft.DOWNMIX_LAYOUTS as string[]) ?? []);
	const locked = $derived(settings.envLocked('DOWNMIX_LAYOUTS'));
	const codecOf = $derived(new Map(codecs.map((codec) => [codec.name, codec])));

	// What is half-typed in the new-layout box.
	let newLayout = $state('');

	function addLayout() {
		const layout = newLayout.trim();
		if (layout && !layouts.includes(layout)) {
			draft.DOWNMIX_LAYOUTS = [...layouts, layout];
			// Not ??=: a leftover entry is an empty string, not undefined.
			if (!draft[bitrateName(layout)]) draft[bitrateName(layout)] = STOCK_BITRATES[layout] ?? '';
			if (!draft[codecName(layout)]) draft[codecName(layout)] = STOCK_CODECS[layout] ?? 'aac';
		}
		newLayout = '';
	}

	function removeLayout(layout: string) {
		draft.DOWNMIX_LAYOUTS = layouts.filter((entry) => entry !== layout);
		delete draft[bitrateName(layout)];
		delete draft[codecName(layout)];
	}

	function moveLayout(from: number, to: number) {
		if (to < 0 || to >= layouts.length || to === from) return;
		const next = [...layouts];
		next.splice(to, 0, ...next.splice(from, 1));
		draft.DOWNMIX_LAYOUTS = next;
	}

	// Either variable can be pinned while the list stays editable, so one badge
	// covers the pair.
	function pinnedOn(layout: string): string[] {
		return [codecName(layout), bitrateName(layout)].filter((name) => baseline[name]?.env);
	}

	function pinnedNote(layout: string): string {
		const pinned = pinnedOn(layout);
		const verb = pinned.length > 1 ? 'are' : 'is';
		return `${pinned.join(' and ')} ${verb} set in the environment, which wins over anything saved here.`;
	}

	// The drag reorders nothing until it ends; passed rows move by transform. A
	// live splice would fight that.
	let listEl: HTMLDivElement;
	let dragFrom = $state<number | null>(null);
	let dragTo = $state(0);
	let dragOffset = $state(0);
	let dragStep = 0;
	let dragStart = 0;

	// Measured: the row height differs across sm.
	function rowStep(): number {
		const rows = listEl.querySelectorAll('[data-layout-row]');
		if (rows.length < 2) return 0;
		return rows[1].getBoundingClientRect().top - rows[0].getBoundingClientRect().top;
	}

	function startDrag(event: PointerEvent, index: number) {
		if (locked) return;
		// Or the browser scrolls the page and no pointermove arrives.
		event.preventDefault();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		dragStep = rowStep();
		dragStart = event.clientY;
		dragFrom = index;
		dragTo = index;
		dragOffset = 0;
	}

	function duringDrag(event: PointerEvent) {
		if (dragFrom === null) return;
		dragOffset = event.clientY - dragStart;
		const moved = dragStep ? Math.round(dragOffset / dragStep) : 0;
		dragTo = Math.min(Math.max(dragFrom + moved, 0), layouts.length - 1);
	}

	function endDrag() {
		if (dragFrom === null) return;
		moveLayout(dragFrom, dragTo);
		dragFrom = null;
		dragOffset = 0;
	}

	// Rows between the dragged row's home and its landing shift one place.
	function rowShift(index: number): number {
		if (dragFrom === null || index === dragFrom) return 0;
		if (dragFrom < index && index <= dragTo) return -dragStep;
		if (dragTo <= index && index < dragFrom) return dragStep;
		return 0;
	}

	async function handleKeys(event: KeyboardEvent, index: number) {
		const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
		if (!step || locked) return;
		event.preventDefault();
		// A browser blurs a moved element, so focus goes back or the next arrow
		// press goes nowhere.
		const handle = event.currentTarget as HTMLElement;
		moveLayout(index, index + step);
		await tick();
		handle.focus();
	}

	// Wide enough for a four-character layout at 16px; five controls must fit
	// one line on a 360px phone.
	const narrow = 'w-[3.75rem] sm:w-14';
</script>

<div
	bind:this={listEl}
	role="group"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	class="flex w-full flex-col gap-2.5 sm:w-auto sm:items-end sm:gap-2"
>
	{#each layouts as layout, index (layout)}
		<div
			data-layout-row
			class={`flex items-center gap-2 ${
				dragFrom === index ? 'relative z-10' : 'transition-transform duration-150'
			}`}
			style={`transform: translateY(${dragFrom === index ? dragOffset : rowShift(index)}px)`}
		>
			<!-- The handle owns the drag, so the fields stay selectable. `touch-none`
			     makes the drag possible: under `manipulation` the browser took it for
			     a scroll and cancelled the pointer, and preventDefault stops only the
			     compatibility mouse events. -->
			<button
				aria-label={`Move ${layout}, ${index + 1} of ${layouts.length}`}
				disabled={locked}
				onpointerdown={(event) => startDrag(event, index)}
				onpointermove={duringDrag}
				onpointerup={endDrag}
				onpointercancel={endDrag}
				onkeydown={(event) => handleKeys(event, index)}
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
			<span
				class={`${chip} ${narrow} justify-center px-2.5 ${dragFrom === index ? 'shadow-lg' : ''}`}
			>
				{layout}
			</span>
			<!-- The known encoders, plus whatever the setting holds, so an exotic one
			     is not silently swapped for aac. -->
			<select
				value={(draft[codecName(layout)] as string) ?? ''}
				onchange={(event) => (draft[codecName(layout)] = event.currentTarget.value)}
				aria-label={`Encoder for ${layout}`}
				disabled={settings.envLocked(codecName(layout))}
				class={`${cell} w-24 pr-1 text-left sm:w-[5.5rem]`}
			>
				{#if !codecOf.has((draft[codecName(layout)] as string) ?? '')}
					<option value={(draft[codecName(layout)] as string) ?? ''}>
						{draft[codecName(layout)] || '(none)'}
					</option>
				{/if}
				{#each codecs as codec (codec.name)}
					<option value={codec.name}>{codec.name}</option>
				{/each}
			</select>
			<input
				value={(draft[bitrateName(layout)] as string) ?? ''}
				oninput={(event) => (draft[bitrateName(layout)] = event.currentTarget.value)}
				placeholder="320k"
				inputmode="text"
				autocapitalize="none"
				autocorrect="off"
				spellcheck="false"
				aria-label={`Bitrate for ${layout}`}
				disabled={settings.envLocked(bitrateName(layout))}
				class={`${cell} w-[4.5rem] sm:w-16`}
			/>
			<!-- A field can be pinned while the list is editable, so the badge sits
			     on the fields. -->
			{#if pinnedOn(layout).length}
				<EnvBadge title={pinnedNote(layout)} />
			{/if}
			<button
				aria-label={`Remove ${layout}`}
				disabled={locked}
				onclick={() => removeLayout(layout)}
				class={removeButton}
			>
				<Glyph name="cross" />
			</button>
		</div>
	{/each}
	<div class="flex items-center gap-2">
		<!-- Stands in for the drag handle, so the boxes line up. -->
		<span class="w-3 flex-none" aria-hidden="true"></span>
		<input
			bind:value={newLayout}
			placeholder="7.1"
			inputmode="decimal"
			autocapitalize="none"
			autocorrect="off"
			spellcheck="false"
			aria-label="New layout"
			disabled={locked}
			onkeydown={(event) => event.key === 'Enter' && addLayout()}
			class={`${ghost} ${narrow} text-center`}
		/>
		<button
			onclick={addLayout}
			disabled={locked}
			class="-my-1 flex h-11 items-center px-2 text-[13px] font-medium text-faint hover:text-fg disabled:opacity-50 sm:my-0 sm:h-8"
		>
			+ Add layout
		</button>
	</div>
</div>
