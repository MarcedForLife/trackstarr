<script lang="ts">
	import Glyph from '$lib/components/Glyph.svelte';
	import ReorderRow from '$lib/components/ReorderRow.svelte';
	import { ghost, removeButton } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';
	import { Reorder } from '$lib/reorder.svelte';
	import { formatLang, langCode, langLabel, parseLang } from '$lib/rules';

	// Every language kept, in the order a downmix source is picked. One setting,
	// so one env badge on the row above covers it.
	let {
		settings,
		languages,
		actions,
		originalName,
		labelledBy,
		describedBy
	}: {
		settings: SettingsDraft;
		// ISO 639-2/B code to display name, as the settings snapshot carries it.
		languages: Record<string, string>;
		actions: string[];
		// The reserved name for the title's own language.
		originalName: string;
		labelledBy: string;
		describedBy: string;
	} = $props();

	const draft = $derived(settings.draft);

	const entries = $derived((draft.LANGUAGES as string[]) ?? []);
	const rows = $derived(entries.map(parseLang));
	const locked = $derived(settings.envLocked('LANGUAGES'));

	// Every row is kept, so every row drags.
	const orderable = $derived(rows.length);

	const ACTION_LABELS: Record<string, string> = { downmix: 'Downmix', keep: 'Keep' };

	let typed = $state('');
	const options = $props.id();

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
		draft.LANGUAGES = next;
	}

	function label(name: string): string {
		return name === originalName ? 'Original' : langLabel(languages, name);
	}

	function addLang() {
		// "Original" is no language name, so it falls through langCode as itself.
		const name = langCode(languages, typed);
		if (name && !rows.some((row) => row.name === name)) {
			write([...entries, formatLang({ name, action: 'downmix' })]);
		}
		typed = '';
	}

	function setAction(index: number, action: string) {
		write(entries.map((entry, at) => (at === index ? formatLang({ ...rows[at], action }) : entry)));
	}

	function removeLang(index: number) {
		write(entries.filter((_, at) => at !== index));
	}

	// Holds a name, not a code, and takes whatever the row has spare.
	const wide = 'min-w-0 flex-1';
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
			label={label(row.name)}
			width={wide}
			action={row.action}
			{index}
			{orderable}
			{actions}
			actionLabels={ACTION_LABELS}
			{locked}
			{drag}
			onaction={(action) => setAction(index, action)}
			onremove={() => removeLang(index)}
		/>
	{/each}
	<div class="flex items-center gap-2">
		<!-- Stands in for the drag handle, so the boxes line up. -->
		<span class="w-3 flex-none" aria-hidden="true"></span>
		<input
			bind:value={typed}
			list={options}
			placeholder="Add language"
			inputmode="text"
			autocapitalize="none"
			autocorrect="off"
			spellcheck="false"
			aria-label="New language"
			disabled={locked}
			onkeydown={(event) => event.key === 'Enter' && addLang()}
			class={`${ghost} ${wide}`}
		/>
		<!-- The action's column, then the cross's, so the field takes exactly the
		     chips' width above it. -->
		<button
			onclick={addLang}
			disabled={locked}
			class="-my-1 flex h-11 w-[6.5rem] flex-none items-center px-2 text-[13px] font-medium text-faint hover:text-fg disabled:opacity-(--disabled) sm:my-0 sm:h-8 sm:w-24"
		>
			+ Add
		</button>
		<!-- The cross's own box and margin, so the column matches it exactly. -->
		<span class={`${removeButton} invisible ml-auto`} aria-hidden="true"
			><Glyph name="cross" /></span
		>
	</div>
</div>

<datalist id={options}>
	<option value="Original"></option>
	{#each Object.entries(languages) as [code, name] (code)}
		<option value={name}></option>
	{/each}
</datalist>
