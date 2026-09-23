<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { field, removeButton } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';

	// The paths a media server spells differently, as pairs, ours first. Owns the
	// rows, since a half-typed pair is not a value; the setting is rewritten from
	// the rows on every keystroke, and the rows rebuilt when the draft is
	// replaced. The row above holds the Add button, so an empty list draws nothing.
	let {
		settings,
		name,
		label,
		gutter,
		labelledBy,
		describedBy
	}: {
		settings: SettingsDraft;
		// The setting this list is the value of.
		name: string;
		// The service, which names the right-hand field.
		label: string;
		// The card's remove slot, so a pair ends where the address above it does.
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

	let firstFields = $state<HTMLInputElement[]>([]);

	export async function add() {
		pairs = [...pairs, { local: '', remote: '' }];
		await tick();
		firstFields[pairs.length - 1]?.focus();
	}

	function removePair(index: number) {
		pairs = pairs.filter((_, at) => at !== index);
		sync();
	}
</script>

{#if pairs.length}
	<div
		role="group"
		aria-labelledby={labelledBy}
		aria-describedby={describedBy}
		class="mt-3 flex flex-col gap-6 sm:gap-2"
	>
		{#each pairs as pair, index (index)}
			<!-- Side by side from sm up, stacked on a phone. The arrow turns with them,
			     and fills the gap so a pair sits closer than its neighbours. -->
			<div class="flex items-center gap-2">
				<div class="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
					<input
						bind:this={firstFields[index]}
						bind:value={pair.local}
						oninput={sync}
						placeholder="/data/media"
						autocapitalize="none"
						autocorrect="off"
						spellcheck="false"
						aria-label={`Our path, pair ${index + 1}`}
						disabled={locked}
						class={`${field} min-w-0 sm:flex-1`}
					/>
					<span class="-my-1 flex-none rotate-90 self-center text-faint sm:my-0 sm:rotate-0">
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
						class={`${field} min-w-0 sm:flex-1`}
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
	</div>
{/if}
