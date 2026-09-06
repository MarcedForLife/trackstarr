<script lang="ts">
	import LanguagePicker from '$lib/components/LanguagePicker.svelte';
	import LayoutList from '$lib/components/LayoutList.svelte';
	import Page from '$lib/components/Page.svelte';
	import SaveBar from '$lib/components/SaveBar.svelte';
	import Section from '$lib/components/Section.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import Toggle from '$lib/components/Toggle.svelte';
	import { box, cell } from '$lib/controls';
	import { SettingsDraft } from '$lib/draft.svelte';
	import {
		belowNote,
		belowProblem,
		bitrateName,
		codecName,
		codecNotes,
		langLabel
	} from '$lib/rules';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const readOnly = $derived(data.user?.role !== 'admin');

	// DOWNMIX_LAYOUTS is the one setting here whose written order matters.
	// svelte-ignore state_referenced_locally
	const settings = new SettingsDraft(data.snapshot.settings, {
		ordered: new Set(['DOWNMIX_LAYOUTS']),
		readOnly: () => readOnly
	});
	// Both records are mutated in place, so these read the live objects after a
	// save.
	const draft = settings.draft;
	const baseline = settings.baseline;
	// Named locally because the rows below say them a great many times.
	const envLocked = (name: string) => settings.envLocked(name);
	const desc = (name: string, fallback: string) => settings.desc(name, fallback);

	const langs = $derived((draft.ALWAYS_KEEP_LANGS as string[]) ?? []);
	const exts = $derived((draft.ALLOWED_EXTS as string[]) ?? []);
	const downmixLangs = $derived((draft.DOWNMIX_LANGS as string[]) ?? []);
	const layouts = $derived((draft.DOWNMIX_LAYOUTS as string[]) ?? []);

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

	// The rows under a rule go inert with it. A ride-along still reads them.
	function langLocked(name: string): boolean {
		return envLocked(name) || !ruleOn('languages');
	}

	function downmixLocked(name: string): boolean {
		return envLocked(name) || !ruleOn('downmix');
	}

	// An empty list with the original language off keeps nothing, so the planner
	// skips every file.
	const keepDesc = $derived.by(() => {
		const base = desc('ALWAYS_KEEP_LANGS', 'Languages kept in every file.');
		if (!ruleOn('languages') || langs.length || draft.KEEP_ORIGINAL_LANG) return base;
		return `${base} Nothing is listed, so every file with tagged tracks would be skipped.`;
	});

	// A downmix in a language nothing keeps looks applied and does nothing.
	const downmixLangsDesc = $derived.by(() => {
		const base = desc(
			'DOWNMIX_LANGS',
			'Extra languages guaranteed every layout, each from the best surviving bigger track of that language.'
		);
		const unkept = ruleOn('languages') ? downmixLangs.filter((code) => !langs.includes(code)) : [];
		if (!unkept.length) return base;
		return `${base} Not kept by the languages rule: ${unkept.map((code) => langLabel(languages, code)).join(', ')}.`;
	});

	// The original-language downmix needs a surviving track in that language.
	const downmixOriginalDesc = $derived.by(() => {
		const base = desc(
			'DOWNMIX_ORIGINAL_LANG',
			'Guarantee every layout in the language the title was made in.'
		);
		if (!draft.DOWNMIX_ORIGINAL_LANG || !ruleOn('languages') || draft.KEEP_ORIGINAL_LANG) {
			return base;
		}
		return `${base} Keep original language is off above, so that language is dropped before anything can be downmixed from it.`;
	});

	// Both read as on while the containers row decides whether they can fire.
	const remuxDesc = $derived.by(() => {
		const base = desc(
			ruleVar('remux'),
			'Rewrite MP4 into Matroska so every rule applies. MP4 direct-plays on more devices, so Alongside is the usual choice: convert only files another rule is already rewriting.'
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
			layouts.map((layout) => ({ layout, rate: String(draft[bitrateName(layout)] ?? '') }))
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

	// The row says what each encoder is for; nobody arrives knowing. In the
	// service's order.
	const layoutsDesc = $derived.by(() => {
		const base = desc(
			'DOWNMIX_LAYOUTS',
			'Each layout is encoded with the encoder and rate beside it. aac plays everywhere and suits stereo; ac3 is what receivers take over HDMI and stops at 5.1; libopus is best per bit and worst for direct play. Drag to set the audio track order.'
		);
		const notes = layouts.flatMap((layout) =>
			codecNotes(layout, codecOf.get((draft[codecName(layout)] as string) ?? ''), outputExts)
		);
		return notes.length ? `${base} ${notes.join(' ')}` : base;
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
	// "Junk" is what a rewrite throws out unasked: artwork read as a second video,
	// streams nothing plays, release tags. "Container" is what it does to the file
	// holding it all.
	const KEEP_RULES = ['languages', 'commentary', 'sdh'];
	const DOWNMIX_RULES = ['downmix', 'regenerate'];
	const JUNK_RULES = ['cover_art', 'junk_titles', 'stray_streams', 'order'];

	// Alongside counts as on. No group is named for a rule inside it.
	const ruleNote = (rules: string[]) => `${rules.filter(ruleOn).length} of ${rules.length} on`;

	// One rule and one list, so a count would read 1 of 1; it says what the group
	// settles instead.
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

		<Section heading="Keep and drop" note={ruleNote(KEEP_RULES)} open>
			{@render ruleRow({
				rule: 'languages',
				label: 'Languages',
				text: "Drop audio and subtitles in a language the two rows below don't keep. Untagged tracks always stay."
			})}
			<SettingRow
				label="Keep original language"
				desc={desc(
					'KEEP_ORIGINAL_LANG',
					'Keep the language the title was made in, as Radarr and Sonarr report it. Off keeps only the languages listed below.'
				)}
				env={!!baseline.KEEP_ORIGINAL_LANG?.env}
				nested
				dim={!ruleOn('languages')}
			>
				{#snippet children({ labelledBy, describedBy })}
					<Toggle
						on={draft.KEEP_ORIGINAL_LANG as boolean}
						disabled={langLocked('KEEP_ORIGINAL_LANG')}
						onchange={(on) => (draft.KEEP_ORIGINAL_LANG = on)}
						{labelledBy}
						{describedBy}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Always keep"
				desc={keepDesc}
				env={!!baseline.ALWAYS_KEEP_LANGS?.env}
				nested
				dim={!ruleOn('languages')}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<LanguagePicker
						values={langs}
						{languages}
						label="Add a language to always keep"
						{labelledBy}
						{describedBy}
						locked={langLocked('ALWAYS_KEEP_LANGS')}
						onchange={(codes) => (draft.ALWAYS_KEEP_LANGS = codes)}
					/>
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

		<Section heading="Downmix and rebuild" note={ruleNote(DOWNMIX_RULES)}>
			{@render ruleRow({
				rule: 'downmix',
				label: 'Downmix',
				text: 'Guarantee a non-commentary track for each layout below, downmixed from the best surviving bigger track. Nothing is upmixed.'
			})}
			<SettingRow
				label="Original language"
				desc={downmixOriginalDesc}
				env={!!baseline.DOWNMIX_ORIGINAL_LANG?.env}
				nested
				dim={!ruleOn('downmix')}
			>
				{#snippet children({ labelledBy, describedBy })}
					<Toggle
						on={draft.DOWNMIX_ORIGINAL_LANG as boolean}
						disabled={downmixLocked('DOWNMIX_ORIGINAL_LANG')}
						onchange={(on) => (draft.DOWNMIX_ORIGINAL_LANG = on)}
						{labelledBy}
						{describedBy}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Other languages"
				desc={downmixLangsDesc}
				env={!!baseline.DOWNMIX_LANGS?.env}
				nested
				dim={!ruleOn('downmix')}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<LanguagePicker
						values={downmixLangs}
						{languages}
						label="Add a language to downmix"
						{labelledBy}
						{describedBy}
						locked={downmixLocked('DOWNMIX_LANGS')}
						onchange={(codes) => (draft.DOWNMIX_LANGS = codes)}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				label="Layouts"
				align="start"
				desc={layoutsDesc}
				env={!!baseline.DOWNMIX_LAYOUTS?.env}
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<LayoutList {settings} {codecs} {labelledBy} {describedBy} />
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
					<div class="flex flex-col items-start gap-1.5 sm:items-end">
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
								class={`${cell} w-[4.5rem] sm:w-16`}
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
		</Section>

		<Section heading="Junk and order" note={ruleNote(JUNK_RULES)}>
			{@render ruleRow({
				rule: 'cover_art',
				label: 'Cover art',
				text: 'Strip embedded artwork that players read as a second video track.'
			})}
			{@render ruleRow({
				rule: 'junk_titles',
				label: 'Junk titles',
				text: 'Clear release junk from track and container titles, as the pattern below matches it.'
			})}
			{@render ruleRow({
				rule: 'stray_streams',
				label: 'Stray streams',
				text: 'Drop data and timecode streams nothing plays. Never keeps them, and some containers then refuse the file.'
			})}
			{@render ruleRow({
				rule: 'order',
				label: 'Track order',
				text: 'Video first, then audio in the layout order above with other sizes after by channel count, then subtitles.'
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

		<Section heading="Title patterns" note="{PATTERNS.length} regexes">
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
						<div class="flex w-full flex-col gap-2 sm:w-96">
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
