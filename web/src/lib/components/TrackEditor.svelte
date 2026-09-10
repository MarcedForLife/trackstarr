<script lang="ts">
	import { onMount } from 'svelte';
	import Select from '$lib/components/Select.svelte';
	import Toggle from '$lib/components/Toggle.svelte';
	import { refusalText } from '$lib/api';
	import { button, primary } from '$lib/controls';
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

	// One track's language and flags, open for changing. Inline under its row
	// rather than over the rows below: a reader is editing this one and reads
	// the rest after. The edit is sent for every twin named, which is how a
	// season's untagged original track is fixed in one press.
	let {
		track,
		twins,
		languages,
		many = false,
		ondone,
		oncancel
	}: {
		track: Track;
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

	async function apply() {
		if (!changed || busy || !targets.length) return;
		busy = true;
		problems = [];
		try {
			const outcomes = await retagTracks(targets, edit);
			const told = summarise(outcomes);
			// Nothing took, so the form stays as set and says why; the sheet has
			// nothing new to show.
			if (told.changed || told.same) ondone(outcomes);
			else problems = told.problems.length ? told.problems : [told.line];
		} catch (caught) {
			problems = [refusalText(caught)];
		} finally {
			busy = false;
		}
	}

	// Into view where it opened under a row near the bottom of the sheet, or
	// the press seems to do nothing.
	let root: HTMLElement;
	onMount(() => root.scrollIntoView({ block: 'nearest' }));

	const ids = $props.id();
</script>

<!-- Its own text colour: the row above is faint for a dropped track, and this
     is a form. -->
<div bind:this={root} class="rounded-xl border border-line-strong bg-raised p-3 text-fg shadow-lg">
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
		<button type="button" onclick={oncancel} disabled={busy} class={button}>Cancel</button>
		<!-- Names the reach when it is the season, since the switch above is on
		     by default there. -->
		<button
			type="button"
			onclick={apply}
			disabled={!changed || busy || !targets.length}
			class={primary}
		>
			{busy ? 'Applying…' : targets.length > 1 ? `Apply to ${targets.length} files` : 'Apply'}
		</button>
	</div>
</div>
