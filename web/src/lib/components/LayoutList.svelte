<script lang="ts">
	import ReorderRow from '$lib/components/ReorderRow.svelte';
	import { control, ghost, radius, selectCell } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';
	import { Reorder } from '$lib/reorder.svelte';
	import { formatRow, parseRow, placeRow, rateNotches, sameRate } from '$lib/rules';
	import type { Codec } from '$lib/settings';

	// Every layout this install has an opinion about, in audio track order. A
	// list to drag rather than chips, since the order matters. One setting, so
	// one env badge on the row above covers the lot.
	let {
		settings,
		codecs,
		actions,
		stock,
		rates,
		labelledBy,
		describedBy
	}: {
		settings: SettingsDraft;
		codecs: Codec[];
		// What a row may be set to, in the service's order.
		actions: string[];
		// What a new row is made at, by size. The service's table, not a copy.
		stock: Record<string, string[]>;
		// The rates offered per size, low to high.
		rates: Record<string, string[]>;
		// The SettingRow's paragraphs, which describe the list as a whole.
		labelledBy: string;
		describedBy: string;
	} = $props();

	// Mutated in place, so this reads the live object after a save.
	const draft = $derived(settings.draft);

	const entries = $derived((draft.AUDIO_LAYOUTS as string[]) ?? []);
	const rows = $derived(entries.map((entry) => parseRow(entry, stock)));
	const locked = $derived(settings.envLocked('AUDIO_LAYOUTS'));
	const codecOf = $derived(new Map(codecs.map((codec) => [codec.name, codec])));

	// Removed layouts sort to the bottom and cannot be dragged, since ordering a
	// track that will not exist reads as a mistake.
	const orderable = $derived(rows.filter((row) => row.action !== 'remove').length);

	const ACTION_LABELS: Record<string, string> = {
		downmix: 'Downmix',
		keep: 'Keep',
		remove: 'Remove'
	};

	// Fills the second line where a kept or removed size has no rates, so every
	// row is one height: the drag measures one row's step and applies it to all.
	const ACTION_NOTES: Record<string, string> = {
		keep: 'left as found',
		remove: 'every track removed'
	};

	// What is half-typed in the new-layout box.
	let newLayout = $state('');

	let listEl: HTMLDivElement;
	const drag = new Reorder({
		list: () => listEl,
		orderable: () => orderable,
		locked: () => locked,
		move: (from, to) => {
			const next = [...entries];
			next.splice(to, 0, ...next.splice(from, 1));
			write(next);
		}
	});

	function write(next: string[]) {
		draft.AUDIO_LAYOUTS = next;
	}

	function addLayout() {
		const name = newLayout.trim();
		if (name && !rows.some((row) => row.name === name)) {
			const [codec = 'aac', bitrate = '320k'] = stock[name] ?? [];
			// Above the removed rows, which stay last.
			const next = [...entries];
			next.splice(orderable, 0, formatRow({ name, action: 'downmix', codec, bitrate }));
			write(next);
		}
		newLayout = '';
	}

	function setAction(index: number, action: string) {
		const row = rows[index];
		const [codec = 'aac', bitrate = '320k'] = stock[row.name] ?? [];
		const written = formatRow({
			...row,
			action,
			// A row that was not encoding has no spec to carry back.
			codec: row.codec || codec,
			bitrate: row.bitrate || bitrate
		});
		write(placeRow(entries, index, written, row.action, action, orderable));
	}

	function setField(index: number, field: 'codec' | 'bitrate', value: string) {
		const next = [...entries];
		next[index] = formatRow({ ...rows[index], [field]: value });
		write(next);
	}

	function removeLayout(index: number) {
		write(entries.filter((_, at) => at !== index));
	}

	// Wide enough for a four-character layout at 16px.
	const narrow = 'flex-none w-[3.75rem] sm:w-14';

	// One rate chip. They share the strip evenly, so a row reads as a scale from
	// its lowest rate to its highest.
	const rateChip = `${control} ${radius} min-w-0 flex-1 border px-1 font-mono text-base transition-colors disabled:opacity-50 sm:text-xs`;
</script>

<div
	bind:this={listEl}
	role="group"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	class="flex w-full flex-col gap-2.5 sm:gap-2"
>
	{#each rows as row, index (row.name)}
		<ReorderRow
			name={row.name}
			width={narrow}
			action={row.action}
			{index}
			{orderable}
			{actions}
			actionLabels={ACTION_LABELS}
			{locked}
			{drag}
			onaction={(action) => setAction(index, action)}
			onremove={() => removeLayout(index)}
		>
			<!-- Nothing is encoded at a size that is kept or removed. The known
			     encoders, plus whatever the entry holds, so an exotic one is not
			     silently swapped for aac. -->
			{#if row.action === 'downmix'}
				<select
					value={row.codec}
					onchange={(event) => setField(index, 'codec', event.currentTarget.value)}
					aria-label={`Encoder for ${row.name}`}
					disabled={locked}
					class={`${selectCell} min-w-0 flex-1`}
				>
					{#if !codecOf.has(row.codec)}
						<option value={row.codec}>{row.codec || '(none)'}</option>
					{/if}
					{#each codecs as codec (codec.name)}
						<option value={codec.name}>{codec.name}</option>
					{/each}
				</select>
			{/if}
			<!-- The rates take the row's second line at every width: five of them
			     never fit beside three controls. `basis-full` forces the wrap, every
			     row carries this group so they all stay one height, and pr-9 clears
			     the cross's column so the chips end where the controls do. -->
			<div class={`order-last flex basis-full items-center gap-1.5 pr-9 pl-5 ${control}`}>
				{#if row.action === 'downmix'}
					{#each rateNotches(rates[row.name] ?? [], row.bitrate) as rate (rate)}
						{@const held = sameRate(rate, row.bitrate)}
						<button
							onclick={() => setField(index, 'bitrate', rate)}
							disabled={locked}
							aria-pressed={held}
							aria-label={`${rate} for ${row.name}`}
							class={`${rateChip} ${
								held ? 'border-accent bg-accent/12 text-fg' : 'border-line text-faint hover:text-fg'
							}`}
						>
							{rate}
						</button>
					{/each}
				{:else}
					<span class="text-[13px] text-faint sm:text-xs">
						{ACTION_NOTES[row.action] ?? ''}
					</span>
				{/if}
			</div>
		</ReorderRow>
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
