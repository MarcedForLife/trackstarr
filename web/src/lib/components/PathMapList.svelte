<script lang="ts">
	import { onDestroy } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { removeButton } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';

	// The paths a media server spells differently, as pairs: ours left, theirs
	// right. Owns the rows, since a half-typed pair is not a value; the setting
	// is rewritten from the rows on every keystroke, and the rows rebuilt when
	// the draft is replaced.
	let {
		settings,
		name,
		label,
		entry,
		entryRow,
		gutter,
		labelledBy,
		describedBy
	}: {
		settings: SettingsDraft;
		// The setting this list is the value of.
		name: string;
		// The service, which names the right-hand field.
		label: string;
		// The card's field, row and remove-slot classes, so a pair lands on the
		// same grid as the address above it.
		entry: string;
		entryRow: string;
		gutter: string;
		// The SettingRow's paragraphs, which describe the list as a whole.
		labelledBy: string;
		describedBy: string;
	} = $props();

	const draft = $derived(settings.draft);
	const locked = $derived(settings.envLocked(name));

	type Pair = { local: string; remote: string };

	// The draft is one object for the life of the page, read once.
	// svelte-ignore state_referenced_locally
	let pairs = $state(pairsFrom(settings.draft[name]));
	// svelte-ignore state_referenced_locally
	onDestroy(settings.onreset(() => (pairs = pairsFrom(draft[name]))));

	function pairsFrom(value: unknown): Pair[] {
		return ((value as string[]) ?? []).map((written) => {
			const at = written.indexOf('=');
			return at < 0
				? { local: written, remote: '' }
				: { local: written.slice(0, at), remote: written.slice(at + 1) };
		});
	}

	function sync() {
		draft[name] = pairs
			.filter((pair) => pair.local.trim() && pair.remote.trim())
			.map((pair) => `${pair.local.trim()}=${pair.remote.trim()}`);
	}

	function addPair() {
		pairs = [...pairs, { local: '', remote: '' }];
	}

	function removePair(index: number) {
		pairs = pairs.filter((_, at) => at !== index);
		sync();
	}
</script>

<div
	role="group"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	class="flex w-full flex-col gap-2.5 sm:w-auto sm:gap-2"
>
	{#each pairs as pair, index (index)}
		<!-- Side by side from sm up, stacked on a phone. The arrow turns with them. -->
		<div class={entryRow}>
			<div
				class="flex min-w-0 flex-1 flex-col gap-1.5 sm:flex-none sm:flex-row sm:items-center sm:gap-2"
			>
				<input
					bind:value={pair.local}
					oninput={sync}
					placeholder="/data/media"
					autocapitalize="none"
					autocorrect="off"
					spellcheck="false"
					aria-label={`Our path, pair ${index + 1}`}
					disabled={locked}
					class={entry}
				/>
				<span class="flex-none rotate-90 self-center text-faint sm:rotate-0">
					<Glyph name="arrow" size={14} />
				</span>
				<input
					bind:value={pair.remote}
					oninput={sync}
					placeholder="/srv/media"
					autocapitalize="none"
					autocorrect="off"
					spellcheck="false"
					aria-label={`${label}'s path, pair ${index + 1}`}
					disabled={locked}
					class={entry}
				/>
			</div>
			<div class={gutter}>
				<button
					aria-label={`Remove pair ${index + 1}`}
					disabled={locked}
					onclick={() => removePair(index)}
					class={removeButton}
				>
					<Glyph name="cross" />
				</button>
			</div>
		</div>
	{/each}
	<!-- Under the right-hand field, whose column it adds to. -->
	<div class={entryRow}>
		<button
			onclick={addPair}
			disabled={locked}
			class="-my-1 flex h-11 items-center px-2 text-[13px] font-medium text-faint hover:text-fg disabled:opacity-50 sm:my-0 sm:ml-auto sm:h-8 sm:pr-0"
		>
			+ Add a pair
		</button>
		<div class={gutter}></div>
	</div>
</div>
