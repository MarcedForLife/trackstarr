<script lang="ts">
	import Spinner from '$lib/components/Spinner.svelte';
	import { onDestroy, onMount } from 'svelte';
	import { popover } from '$lib/popover.svelte';
	import Select from '$lib/components/Select.svelte';
	import Toggle from '$lib/components/Toggle.svelte';
	import { refusalText } from '$lib/api';
	import { button, frosted, primary } from '$lib/controls';
	import { describe } from '$lib/format';
	import type { Track } from '$lib/library';
	import {
		changes,
		FLAGS,
		held,
		retagTracks,
		summarise,
		UNDEFINED,
		type Flag,
		type Outcome,
		type Target
	} from '$lib/retag';

	// One track's language and flags. The edit goes to every twin named, which
	// fixes a season's untagged original track in one press.
	let {
		track,
		trigger,
		twins,
		languages,
		many = false,
		ondone,
		oncancel
	}: {
		track: Track;
		trigger: HTMLElement | null;
		// Every file in the title carrying this track, this file first.
		twins: Target[];
		// ISO 639-2/B code to display name, or null while it is being fetched.
		languages: Record<string, string> | null;
		// Whether every twin is taken by default: a series is many files ripped
		// alike, a film's twin is an extra nobody meant.
		many?: boolean;
		// Something changed: what each file came to. The editor stays up on its
		// own when nothing did, with the reasons.
		ondone: (outcomes: Outcome[]) => void;
		oncancel: () => void;
	} = $props();

	// Seeded once, which is what the ignores say: the sheet makes this component
	// afresh for each row it opens under, and a reload of the title while it is
	// up must not reset what the reader has already set, least of all the switch
	// below that widens the edit to the whole season.
	// svelte-ignore state_referenced_locally
	const offered = FLAGS[track.kind] ?? [];
	// svelte-ignore state_referenced_locally
	const was = track.lang ?? UNDEFINED;
	let lang = $state(was);
	let flags = $state<Partial<Record<Flag, boolean>>>(
		Object.fromEntries(offered.map((flag) => [flag.name, held(track, flag.name)]))
	);
	// svelte-ignore state_referenced_locally
	let all = $state(many && twins.length > 1);

	const edit = $derived(changes(track, lang, flags));
	const changed = $derived(edit.lang !== undefined || edit.flags !== undefined);

	// No language first, then every language by name. A code the table lacks
	// is still the file's, so it is offered as itself.
	const options = $derived.by(() => {
		const known = Object.entries(languages ?? {}).map(([code, name]) => ({
			value: code,
			label: `${name} (${code})`
		}));
		if (was !== UNDEFINED && !known.some((option) => option.value === was)) {
			known.unshift({ value: was, label: was });
		}
		return [{ value: UNDEFINED, label: 'No language (und)' }, ...known];
	});

	// Which files the press reaches: this one, or every twin.
	const targets = $derived(all ? twins : twins.slice(0, 1));

	let busy = $state(false);
	// Why the last press came to nothing: the service's refusal, or a line per
	// file it would not change. Cleared as the form moves on.
	let problems = $state<string[]>([]);

	let gone = false;
	onDestroy(() => {
		gone = true;
	});

	async function apply() {
		if (!changed || busy || !targets.length) return;
		const done = ondone;
		busy = true;
		problems = [];
		try {
			const outcomes = await retagTracks(targets, edit);
			if (gone) return;
			const told = summarise(outcomes);
			// Nothing took, so the form stays as set and says why; the sheet has
			// nothing new to show.
			if (told.changed || told.same) done(outcomes);
			else problems = told.problems.length ? told.problems : [told.line];
		} catch (caught) {
			if (!gone) problems = [refusalText(caught)];
		} finally {
			if (!gone) busy = false;
		}
	}

	// Any close is the sheet's cancel.
	let root: HTMLElement;
	const form = popover({ edge: 'right', onclose: () => oncancel() });
	onMount(() => {
		if (!trigger) return;
		void form.raise(trigger, root).then(() => root.querySelector('select')?.focus());
	});

	// Light dismiss on click rather than touch, so a finger landing to scroll
	// keeps a half-made edit. Never while saving.
	function outside(event: MouseEvent) {
		if (form.open && !busy && form.outside(event.target as Node)) form.lower();
	}

	// Follows the pencil as the sheet scrolls.
	$effect(() => {
		const follow = () => form.place();
		window.addEventListener('scroll', follow, true);
		return () => window.removeEventListener('scroll', follow, true);
	});

	const ids = $props.id();
</script>

<svelte:window onclick={outside} onresize={() => form.place()} />

<!-- Resets font and colour, since the row it hangs from is mono and faint
     when dropped. -->
<div
	bind:this={root}
	popover="manual"
	role="dialog"
	aria-label={`Edit tags on ${describe(track)}`}
	class={`fixed m-0 w-96 max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong ${frosted} p-3.5 font-sans text-fg shadow-lg`}
>
	<p class="text-[12px] font-medium">
		Edit tags
		<span class="ml-1 font-mono text-[11px] font-normal text-faint">{describe(track)}</span>
	</p>

	<div class="mt-3 flex flex-col gap-3">
		<div class="flex items-center justify-between gap-3">
			<span id={`${ids}-lang`} class="text-[12.5px]">Language</span>
			{#if languages}
				<Select
					{options}
					value={lang}
					disabled={busy}
					labelledBy={`${ids}-lang`}
					onchange={(code) => {
						lang = code;
						problems = [];
					}}
				/>
			{:else}
				<span class="text-[12px] text-faint">Loading…</span>
			{/if}
		</div>

		{#each offered as flag (flag.name)}
			<div class="flex items-center justify-between gap-3">
				<span class="min-w-0">
					<span id={`${ids}-${flag.name}`} class="block text-[12.5px]">{flag.label}</span>
					<span id={`${ids}-${flag.name}-hint`} class="block text-[12px] text-dim">
						{flag.hint}
					</span>
				</span>
				<Toggle
					on={!!flags[flag.name]}
					labelledBy={`${ids}-${flag.name}`}
					describedBy={`${ids}-${flag.name}-hint`}
					disabled={busy}
					onchange={(on) => {
						flags = { ...flags, [flag.name]: on };
						problems = [];
					}}
				/>
			</div>
		{/each}

		{#if twins.length > 1}
			<!-- Off by default for a film, since its twin is usually an extra. -->
			<div class="flex items-center justify-between gap-3">
				<span class="min-w-0">
					<span id={`${ids}-all`} class="block text-[12.5px]">
						Apply to all {twins.length} files with this track
					</span>
					<span id={`${ids}-all-hint`} class="block text-[12px] text-dim">
						Same place in the file, same codec, channels, language and title.
					</span>
				</span>
				<Toggle
					on={all}
					labelledBy={`${ids}-all`}
					describedBy={`${ids}-all-hint`}
					disabled={busy}
					onchange={(on) => (all = on)}
				/>
			</div>
		{/if}
	</div>

	{#if problems.length}
		<div role="alert" class="mt-3 flex flex-col gap-0.5 text-[12px] text-danger">
			{#each problems as problem, at (at)}
				<p>{problem}</p>
			{/each}
		</div>
	{/if}

	<div class="mt-3 flex justify-end gap-2">
		<button type="button" onclick={() => form.lower()} disabled={busy} class={button}>Cancel</button
		>
		<!-- Names the file count when it applies to more than one. -->
		<button
			type="button"
			onclick={apply}
			disabled={!changed || busy || !targets.length}
			aria-busy={busy}
			class={primary}
		>
			<Spinner {busy} />
			{busy ? 'Applying…' : targets.length > 1 ? `Apply to ${targets.length} files` : 'Apply'}
		</button>
	</div>
</div>
