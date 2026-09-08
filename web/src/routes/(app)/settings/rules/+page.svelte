<script lang="ts">
	import LangList from '$lib/components/LangList.svelte';
	import LayoutList from '$lib/components/LayoutList.svelte';
	import Page from '$lib/components/Page.svelte';
	import SaveBar from '$lib/components/SaveBar.svelte';
	import Section from '$lib/components/Section.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import { box, cell } from '$lib/controls';
	import { SettingsDraft } from '$lib/draft.svelte';
	import { belowNote, belowProblem, codecNotes, parseLang, parseRow } from '$lib/rules';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const readOnly = $derived(data.user?.role !== 'admin');

	// AUDIO_LAYOUTS is the one setting here whose written order matters.
	// svelte-ignore state_referenced_locally
	const settings = new SettingsDraft(data.snapshot.settings, {
		ordered: new Set(['AUDIO_LAYOUTS']),
		readOnly: () => readOnly
	});
	// Both records are mutated in place, so these read the live objects after a
	// save.
	const draft = settings.draft;
	const baseline = settings.baseline;
	// Named locally because the rows below say them a great many times.
	const envLocked = (name: string) => settings.envLocked(name);
	const desc = (name: string, fallback: string) => settings.desc(name, fallback);

	const exts = $derived((draft.ALLOWED_EXTS as string[]) ?? []);
	// One entry per language, carrying whether layouts are made in it.
	const langRows = $derived(((draft.LANGUAGES as string[]) ?? []).map(parseLang));
	// One entry per size, carrying what happens to it and, for an add, how.
	const rows = $derived(
		((draft.AUDIO_LAYOUTS as string[]) ?? []).map((entry) => parseRow(entry, data.snapshot.stock))
	);
	const downmixed = $derived(rows.filter((row) => row.action === 'downmix'));
	const removed = $derived(rows.filter((row) => row.action === 'remove'));

	// The names the pickers offer. An alias like `en` saves fine and comes back
	// normalised.
	const languages = $derived(data.snapshot.languages);

	// One variable per rule, so a deploy can pin one and leave the rest.
	const ruleVar = (rule: string) => `RULE_${rule.toUpperCase()}`;
	const ruleMode = (rule: string) => String(draft[ruleVar(rule)] ?? '');
	const ruleOn = (rule: string) => ruleMode(rule) !== 'never';

	const MODE_LABELS: Record<string, string> = {
		never: 'Never',
		alongside: 'Alongside',
		always: 'Always'
	};
	const modeOptions = $derived(
		data.snapshot.modes.map((value) => ({ value, label: MODE_LABELS[value] ?? value }))
	);

	// Only untagged tracks would survive an empty list.
	const langsDesc = $derived.by(() => {
		const base = desc(
			'LANGUAGES',
			'Every language this library keeps, in the order a downmix source is picked. Downmix makes every layout in it, Keep leaves what the file has, and Original is whatever Radarr or Sonarr reports.'
		);
		if (langRows.length || !ruleOn('languages')) return base;
		return `${base} This is empty and the rule below drops the rest, so only untagged tracks would survive.`;
	});

	// Both read as on while the containers row decides whether they can fire.
	const remuxDesc = $derived.by(() => {
		const base = desc(
			ruleVar('remux'),
			'Rewrite MP4 into Matroska so every rule applies. MP4 direct-plays on more devices, so Alongside is the usual choice.'
		);
		const convertible = exts.filter((ext) => ext !== '.mkv');
		if (!ruleOn('remux') || convertible.length) return base;
		return `${base} No container below is selected to convert from, so nothing is remuxed.`;
	});

	const regenerateDesc = $derived.by(() => {
		const base = desc(
			ruleVar('regenerate'),
			"Rebuild downmixes whose settings have changed. MKV only, the one container that keeps the tag marking trackstarr's own tracks."
		);
		if (!ruleOn('regenerate') || exts.includes('.mkv')) return base;
		return `${base} .mkv is not selected under Containers, so nothing is regenerated.`;
	});

	// A string, so what is typed goes over the wire untouched.
	const belowPercent = $derived(String(draft.REGENERATE_BELOW_PERCENT ?? ''));

	function belowLocked(): boolean {
		return envLocked('REGENERATE_BELOW_PERCENT') || !allScope();
	}

	// The low-bitrate number is All's alone, and All means nothing while the
	// rule itself is off.
	function allScope(): boolean {
		return ruleOn('regenerate') && draft.REGENERATE_SCOPE === 'all';
	}

	const lowProblem = $derived(belowProblem(belowPercent));
	const lowNote = $derived(
		belowNote(
			belowPercent,
			downmixed.map((row) => ({ layout: row.name, rate: row.bitrate }))
		)
	);

	// In the service's order, not click order, so the row survives a save.
	function toggleExt(ext: string, on: boolean) {
		draft.ALLOWED_EXTS = data.snapshot.containers.filter((entry) =>
			entry === ext ? on : exts.includes(entry)
		);
	}

	// Nothing listed means nothing is ever rewritten. Legal, and worth saying.
	const extsDesc = $derived.by(() => {
		const base = desc('ALLOWED_EXTS', 'The containers a rewrite may touch.');
		if (exts.length) return base;
		return `${base} Nothing is selected, so no file will be rewritten.`;
	});

	const codecs = $derived(data.snapshot.codecs);
	const codecOf = $derived(new Map(codecs.map((codec) => [codec.name, codec])));

	// Where a rewrite lands: with remux on at all everything is converted, so an
	// encoder only Matroska holds is fine.
	const outputExts = $derived(ruleOn('remux') ? ['.mkv'] : exts);

	// The row says what each encoder is for; nobody arrives knowing. Notes come
	// only from downmixed rows, since nothing else is encoded.
	const layoutsDesc = $derived.by(() => {
		const base = desc(
			'AUDIO_LAYOUTS',
			'Every size this library has an opinion about, in the audio track order. Downmix guarantees one exists, made from the best bigger track, Keep leaves it alone and Remove deletes it.'
		);
		const notes = downmixed.flatMap((row) =>
			codecNotes(row.name, codecOf.get(row.codec), outputExts)
		);
		const warning = removed.length
			? ' Removing is the one thing here you cannot undo: the mix is gone from the file, and nothing can be downmixed or rebuilt from it again.'
			: '';
		return `${base}${notes.length ? ` ${notes.join(' ')}` : ''}${warning}`;
	});

	const scopeOptions = [
		{ value: 'generated', label: 'Generated' },
		{ value: 'all', label: 'All' }
	];

	// An empty regex matches every title. Dropping the name sends null, which
	// restores the built-in.
	function setPattern(name: string, value: string) {
		if (value.trim()) draft[name] = value;
		else delete draft[name];
	}

	// One row four times; each feeds a rule above.
	const PATTERNS: { name: string; label: string; desc: string }[] = [
		{
			name: 'COMMENTARY_PATTERN',
			label: 'Commentary',
			desc: 'Matches commentary, described audio and isolated scores.'
		},
		{
			name: 'SDH_PATTERN',
			label: 'SDH subtitles',
			desc: 'Matches subtitles for the deaf and hard-of-hearing.'
		},
		{
			name: 'FORCED_PATTERN',
			label: 'Forced subtitles',
			desc: 'Matches forced subtitles, which are always kept.'
		},
		{
			name: 'JUNK_TITLE_PATTERN',
			label: 'Junk titles',
			desc: 'Release junk cleared from track and container titles: bitrates, resolutions, source and codec tags.'
		}
	];

	// The box grows to the pattern, bounded above. `rows` is the floor for a
	// browser without field-sizing.
	const area =
		'w-full resize-y rounded-lg border border-line-strong bg-field px-2.5 py-2 font-mono text-base leading-snug field-sizing-content min-h-[calc(2lh+1rem)] max-h-[14lh] disabled:opacity-50 sm:text-xs';

	// The line under the low-bitrate box, about the entry rather than the setting.
	const id = $props.id();
	const lowLine = `${id}-low`;

	// The longest page in the app, so its groups fold; each shows its count shut.
	const TITLE_RULES = ['junk_titles', 'cover_art'];
	const STREAM_RULES = ['stray_streams', 'order'];

	// Alongside counts as on. No group is named for a rule inside it.
	const ruleNote = (rules: string[]) => `${rules.filter(ruleOn).length} of ${rules.length} on`;

	// The two lists are one grid, so this group's note is what the grid makes
	// rather than how many of its four rules are on.
	const tracksNote = $derived(
		[
			`${langRows.length} ${langRows.length === 1 ? 'language' : 'languages'}`,
			downmixed.length ? `${downmixed.length} downmixed` : '',
			removed.length ? `${removed.length} removed` : '',
			ruleOn('regenerate') ? 'regenerate' : ''
		]
			.filter(Boolean)
			.join(' · ')
	);

	const containerNote = $derived(
		`${exts.length} ${exts.length === 1 ? 'container' : 'containers'}${
			ruleOn('remux') ? ' · remux' : ''
		}`
	);
</script>

<!-- One row per rule. `text` is the fallback; an env-pinned rule says so. -->
{#snippet ruleRow({ rule, label, text }: { rule: string; label: string; text: string })}
	<SettingRow {label} desc={desc(ruleVar(rule), text)} env={!!baseline[ruleVar(rule)]?.env} stack>
		{#snippet children({ labelledBy, describedBy })}
			<Segmented
				fill
				options={modeOptions}
				{labelledBy}
				{describedBy}
				value={ruleMode(rule)}
				disabled={envLocked(ruleVar(rule))}
				onchange={(value) => (draft[ruleVar(rule)] = value)}
			/>
		{/snippet}
	</SettingRow>
{/snippet}

<Page eyebrow="Settings" title="Rules" lead="What a rewrite keeps, drops and generates.">
	<!-- Inert while a save is in flight, so the response cannot land on a
	     keystroke it never carried. min-w-0, or a fieldset will not shrink. -->
	<fieldset disabled={settings.busy} class="min-w-0">
		{#if readOnly}
			<p class="mt-2 text-[13px] text-faint">Viewing only; an admin can change these.</p>
		{/if}

		<p class="mt-3 text-[13px] text-dim">
			Alongside acts only on a file another rule is already rewriting.
		</p>

		<Section heading="Languages and audio" note={tracksNote} open>
			<SettingRow
				label="Languages"
				align="start"
				desc={langsDesc}
				env={!!baseline.LANGUAGES?.env}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<LangList
						{settings}
						{languages}
						actions={data.snapshot.lang_actions}
						originalName={data.snapshot.original_lang}
						{labelledBy}
						{describedBy}
					/>
				{/snippet}
			</SettingRow>
			{@render ruleRow({
				rule: 'languages',
				label: 'Unlisted languages',
				text: 'Drop audio and subtitles in a language the list above does not name. Untagged tracks always stay.'
			})}
			<SettingRow
				label="Layouts"
				align="start"
				desc={layoutsDesc}
				env={!!baseline.AUDIO_LAYOUTS?.env}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<LayoutList
						{settings}
						{codecs}
						actions={data.snapshot.actions}
						stock={data.snapshot.stock}
						rates={data.snapshot.rates}
						{labelledBy}
						{describedBy}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Regenerate downmixes"
				desc={regenerateDesc}
				env={!!baseline[ruleVar('regenerate')]?.env}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<Segmented
						fill
						options={modeOptions}
						{labelledBy}
						{describedBy}
						value={ruleMode('regenerate')}
						disabled={envLocked(ruleVar('regenerate'))}
						onchange={(value) => (draft[ruleVar('regenerate')] = value)}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Rebuild which tracks"
				desc={desc(
					'REGENERATE_SCOPE',
					"Generated rebuilds trackstarr's own tracks once their codec or bitrate no longer matches the settings above. All also replaces any other layout-sized track reporting under the share of its rate set below. Either way the replacement is a fresh downmix from a surviving bigger track."
				)}
				env={!!baseline.REGENERATE_SCOPE?.env}
				nested
				stack
				dim={!ruleOn('regenerate')}
			>
				{#snippet children({ labelledBy, describedBy })}
					<Segmented
						fill
						options={scopeOptions}
						{labelledBy}
						{describedBy}
						value={draft.REGENERATE_SCOPE as string}
						disabled={envLocked('REGENERATE_SCOPE') || !ruleOn('regenerate')}
						onchange={(value) => (draft.REGENERATE_SCOPE = value)}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Low bitrate below"
				align="start"
				desc={desc(
					'REGENERATE_BELOW_PERCENT',
					"How far under its layout's rate a track must report before All calls it low-bitrate. A track with no bigger source is left alone, since a downmix holds no more than its source."
				)}
				env={!!baseline.REGENERATE_BELOW_PERCENT?.env}
				nested
				stack
				dim={!allScope()}
			>
				{#snippet children({ labelledBy, describedBy })}
					<div class="flex w-full flex-col items-start gap-1.5 sm:items-end">
						<div class="flex items-center gap-2">
							<input
								value={belowPercent}
								oninput={(event) => (draft.REGENERATE_BELOW_PERCENT = event.currentTarget.value)}
								inputmode="numeric"
								autocapitalize="none"
								autocorrect="off"
								spellcheck="false"
								aria-labelledby={labelledBy}
								aria-describedby={lowProblem || lowNote ? `${describedBy} ${lowLine}` : describedBy}
								disabled={belowLocked()}
								class={`${cell} w-[4.5rem] flex-none sm:w-16`}
							/>
							<span class="text-[13px] text-faint">% of its rate</span>
						</div>
						{#if lowProblem}
							<p id={lowLine} class="text-[12.5px] text-danger">{lowProblem}</p>
						{:else if lowNote}
							<p id={lowLine} class="text-[12.5px] text-faint">{lowNote}</p>
						{/if}
					</div>
				{/snippet}
			</SettingRow>
			{@render ruleRow({
				rule: 'commentary',
				label: 'Commentary',
				text: 'Drop commentary, described-audio and isolated-score tracks in every language. They are never downmix sources either way.'
			})}
			{@render ruleRow({
				rule: 'sdh',
				label: 'SDH subtitles',
				text: 'Drop an SDH subtitle when the same language keeps a full one. Forced subtitles always stay.'
			})}
		</Section>

		<Section heading="Titles and artwork" note={ruleNote(TITLE_RULES)}>
			{@render ruleRow({
				rule: 'junk_titles',
				label: 'Junk titles',
				text: 'Clear release junk from track and container titles, as the pattern below matches it.'
			})}
			{@render ruleRow({
				rule: 'cover_art',
				label: 'Cover art',
				text: 'Strip embedded artwork that players read as a second video track.'
			})}
		</Section>

		<Section heading="Streams and order" note={ruleNote(STREAM_RULES)}>
			{@render ruleRow({
				rule: 'stray_streams',
				label: 'Stray streams',
				text: 'Drop data and timecode streams nothing plays. Never keeps them, and some containers then refuse the file.'
			})}
			{@render ruleRow({
				rule: 'order',
				label: 'Track order',
				text: 'Video first, then audio in the Layouts order with other sizes after by channel count, then subtitles.'
			})}
		</Section>

		<Section heading="File format" note={containerNote}>
			<SettingRow label="Containers" desc={extsDesc} env={!!baseline.ALLOWED_EXTS?.env} stack>
				{#snippet children({ labelledBy, describedBy })}
					<!-- Chips, since the service refuses anything else. -->
					<div
						role="group"
						aria-labelledby={labelledBy}
						aria-describedby={describedBy}
						class="flex flex-wrap items-center gap-2 sm:justify-end"
					>
						{#each data.snapshot.containers as ext (ext)}
							{@const on = exts.includes(ext)}
							<button
								aria-pressed={on}
								disabled={envLocked('ALLOWED_EXTS')}
								onclick={() => toggleExt(ext, !on)}
								class={`${box} flex-none border px-3 font-mono disabled:opacity-50 ${
									on
										? 'border-accent bg-accent-soft font-semibold text-accent'
										: 'border-line-strong bg-field text-dim hover:text-fg'
								}`}
							>
								{ext}
							</button>
						{/each}
					</div>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Remux to MKV"
				desc={remuxDesc}
				env={!!baseline[ruleVar('remux')]?.env}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<Segmented
						fill
						options={modeOptions}
						{labelledBy}
						{describedBy}
						value={ruleMode('remux')}
						disabled={envLocked(ruleVar('remux'))}
						onchange={(value) => (draft[ruleVar('remux')] = value)}
					/>
				{/snippet}
			</SettingRow>
		</Section>

		<Section heading="Patterns" note="{PATTERNS.length} regexes">
			<p class="pb-1 text-[13px] text-dim">
				Matched against track titles when the container's disposition flags are unset, as most rips
				leave them. Case-insensitive Python regular expressions; clear one to restore the built-in.
			</p>
			{#each PATTERNS as pattern (pattern.name)}
				<SettingRow
					label={pattern.label}
					align="start"
					desc={desc(pattern.name, pattern.desc)}
					env={!!baseline[pattern.name]?.env}
					stack
				>
					{#snippet children({ labelledBy, describedBy })}
						<div class="flex w-full flex-col gap-2">
							<!-- A textarea: the junk-title default is 200 characters. -->
							<textarea
								value={String(draft[pattern.name] ?? '')}
								oninput={(event) => setPattern(pattern.name, event.currentTarget.value)}
								rows="2"
								autocapitalize="none"
								spellcheck="false"
								aria-labelledby={labelledBy}
								aria-describedby={describedBy}
								disabled={envLocked(pattern.name)}
								class={area}></textarea>
						</div>
					{/snippet}
				</SettingRow>
			{/each}
		</Section>

		<SaveBar {settings} />
	</fieldset>
</Page>
