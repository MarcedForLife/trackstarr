<script lang="ts">
	import Glyph from '$lib/components/Glyph.svelte';
	import { chip, ghost } from '$lib/controls';
	import { langCode, langLabel } from '$lib/rules';

	// Chips for the languages set, and a datalist to add one, so an unnamed code
	// is still typeable. Holds only what is half-typed; the list is the caller's.
	let {
		values,
		// ISO 639-2/B code to display name, as the settings snapshot carries it.
		languages,
		// The input's name for a screen reader, since one page carries two of
		// these; the ids below describe the list.
		label,
		labelledBy,
		describedBy,
		locked = false,
		onchange
	}: {
		values: string[];
		languages: Record<string, string>;
		label: string;
		labelledBy: string;
		describedBy: string;
		locked?: boolean;
		onchange: (codes: string[]) => void;
	} = $props();

	let typed = $state('');

	// Each picker's datalist is its own.
	const options = $props.id();

	function add() {
		const code = langCode(languages, typed);
		if (code && !values.includes(code)) onchange([...values, code]);
		typed = '';
	}

	function remove(code: string) {
		onchange(values.filter((entry) => entry !== code));
	}

	// Wider than a code box: this one holds a name.
	const field = `${ghost} w-[9.5rem] sm:w-32`;
</script>

<div
	role="group"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	class="flex flex-wrap items-center gap-2 sm:max-w-[22rem] sm:justify-end"
>
	{#each values as code (code)}
		<span class={`${chip} gap-0.5 pr-1 pl-2.5 sm:pr-0.5 sm:pl-2`} title={code}>
			{langLabel(languages, code)}
			{#if !locked}
				<button
					aria-label={`Remove ${langLabel(languages, code)}`}
					onclick={() => remove(code)}
					class="rounded p-2 text-faint hover:text-danger sm:p-1"
				>
					<Glyph name="cross" size={11} />
				</button>
			{/if}
		</span>
	{/each}
	<input
		bind:value={typed}
		list={options}
		placeholder="Add language"
		inputmode="text"
		autocapitalize="none"
		autocorrect="off"
		spellcheck="false"
		aria-label={label}
		aria-describedby={describedBy}
		disabled={locked}
		onkeydown={(event) => event.key === 'Enter' && add()}
		onchange={add}
		onblur={add}
		class={field}
	/>
</div>

<datalist id={options}>
	{#each Object.entries(languages) as [code, name] (code)}
		<option value={name}></option>
	{/each}
</datalist>
