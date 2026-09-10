<script lang="ts">
	import { onDestroy } from 'svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import { field, removeButton } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';
	import type { DirCheck } from '$lib/sweep';

	// Where the sweep walks, one directory a row. Owns the rows, since a blank one
	// is not a pending change; the setting is rewritten from the rows on every
	// keystroke, and the rows rebuilt when the draft is replaced.
	let {
		settings,
		answers,
		labelledBy,
		describedBy
	}: {
		settings: SettingsDraft;
		// What the service said about each path. Empty for a viewer.
		answers: Map<string, DirCheck>;
		// The SettingRow's paragraphs, which describe the list as a whole.
		labelledBy: string;
		describedBy: string;
	} = $props();

	const draft = $derived(settings.draft);
	const locked = $derived(settings.envLocked('MEDIA_DIRS'));

	// The draft is one object for the life of the page, read once.
	// svelte-ignore state_referenced_locally
	let dirs = $state(dirsFrom(settings.draft.MEDIA_DIRS));
	// svelte-ignore state_referenced_locally
	onDestroy(settings.onreset(() => (dirs = dirsFrom(draft.MEDIA_DIRS))));

	function dirsFrom(value: unknown): string[] {
		return [...((value as string[]) ?? [])];
	}

	function sync() {
		draft.MEDIA_DIRS = dirs.map((entry) => entry.trim()).filter(Boolean);
	}

	function addDir() {
		dirs = [...dirs, ''];
	}

	function removeDir(index: number) {
		dirs = dirs.filter((_, at) => at !== index);
		sync();
	}
</script>

<div
	role="group"
	aria-labelledby={labelledBy}
	aria-describedby={describedBy}
	class="flex w-full flex-col gap-2.5 sm:w-72 sm:gap-2"
>
	{#each dirs as dir, index (index)}
		{@const answer = answers.get(dir.trim())}
		<div class="flex w-full flex-col gap-1">
			<div class="flex w-full items-center gap-2">
				<input
					bind:value={dirs[index]}
					oninput={sync}
					placeholder="/data/media/movies"
					autocapitalize="none"
					autocorrect="off"
					spellcheck="false"
					aria-label={`Media directory ${index + 1}`}
					disabled={locked}
					class={field}
				/>
				<button
					aria-label={`Remove directory ${index + 1}`}
					disabled={locked}
					onclick={() => removeDir(index)}
					class={removeButton}
				>
					<Glyph name="cross" />
				</button>
			</div>
			<!-- An unmounted path is legitimate; one with a colon cannot be saved. -->
			{#if answer && answer.state !== 'ok'}
				<p
					class={`pr-7 text-[12.5px] ${answer.state === 'invalid' ? 'text-danger' : 'text-faint'}`}
				>
					{answer.detail}
				</p>
			{/if}
		</div>
	{/each}
	<button
		onclick={addDir}
		disabled={locked}
		class="-my-1 flex h-11 items-center self-start px-2 text-[13px] font-medium text-faint hover:text-fg disabled:opacity-50 sm:my-0 sm:h-8"
	>
		+ Add a directory
	</button>
	{#if !dirs.length}
		<p class="text-[12.5px] text-faint">No directories listed, so there is nothing to sweep.</p>
	{/if}
</div>
