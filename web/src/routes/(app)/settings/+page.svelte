<script lang="ts">
	import NumberField from '$lib/components/NumberField.svelte';
	import Page from '$lib/components/Page.svelte';
	import SaveBar from '$lib/components/SaveBar.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import { field, noteBox } from '$lib/controls';
	import { provideSettings, SettingsDraft } from '$lib/draft.svelte';
	import { wholeUnits } from '$lib/format';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const readOnly = $derived(data.user?.role !== 'admin');

	// svelte-ignore state_referenced_locally
	const settings = new SettingsDraft(data.snapshot.settings, { readOnly: () => readOnly });
	// The rows below name a setting and read the rest off the draft.
	provideSettings(settings);
	// Mutated in place, so this reads the live object after a save.
	const draft = settings.draft;
	// Named locally because the controls below say it a great many times.
	const envLocked = (name: string) => settings.envLocked(name);

	// A ladder: each mode does everything the one before it does.
	const MODES = [
		{ value: 'report', label: 'Report only' },
		{ value: 'imports', label: 'Imports' },
		{ value: 'all', label: 'Everything' }
	];

	// Said per mode, since imports and the sweep differ at each rung.
	const MODE_NOTES: Record<string, string> = {
		report:
			'Nothing is rewritten. Imports and sweeps are planned and recorded only, and trackstarr sweep --apply is overruled too.',
		imports:
			'Files Radarr and Sonarr import are rewritten as they land. The scheduled sweep only writes /config/pending.tsv.',
		all: 'Files are rewritten as they are imported, and the scheduled sweep rewrites what it finds.'
	};

	const mode = $derived((draft.REWRITE_MODE as string) ?? 'imports');

	// Unset is UTC.
	const zone = $derived(((draft.TZ as string) ?? '').trim());

	// The time there now, empty when the browser has no such zone. Both lists are
	// IANA's.
	const zoneNow = $derived.by(() => {
		try {
			return new Intl.DateTimeFormat(undefined, {
				timeZone: zone || 'UTC',
				hour: '2-digit',
				minute: '2-digit',
				timeZoneName: 'short'
			}).format(new Date());
		} catch {
			return '';
		}
	});

	function text(name: string): string {
		return String(draft[name] ?? '');
	}

	// Strings, as the settings file holds them, so the value travels untouched.
	function setNumber(name: string, value: string) {
		draft[name] = value;
	}

	// Startup's checks, made while the field is open rather than after a save.
	function numberProblem(name: string, least: number): string {
		const entry = text(name).trim();
		if (!/^-?\d+$/.test(entry)) return 'That has to be a whole number.';
		if (Number(entry) < least) return `That cannot be below ${least}.`;
		return '';
	}

	// The two notes about a value rather than its setting, read after the row's
	// sentence.
	const id = $props.id();
	const modeNote = `${id}-mode`;
	const zoneNote = `${id}-zone`;
</script>

{#snippet numberField(
	name: string,
	labelledBy: string,
	describedBy: string,
	least: number,
	unit: string
)}
	<NumberField
		value={text(name)}
		onchange={(value) => setNumber(name, value)}
		{labelledBy}
		{describedBy}
		{unit}
		problem={numberProblem(name, least)}
		note={unit === 'seconds' ? wholeUnits(Number(text(name))) : ''}
		disabled={envLocked(name)}
	/>
{/snippet}

<datalist id="zone-options">
	{#each data.snapshot.zones as option (option)}
		<option value={option}></option>
	{/each}
</datalist>

<Page eyebrow="Settings" title="General" lead="Settings that apply to the whole service.">
	<!-- Inert while a save is in flight, so the response cannot land on a
	     keystroke it never carried. min-w-0, or a fieldset will not shrink. -->
	<fieldset disabled={settings.busy} class="min-w-0">
		{#if readOnly}
			<p class="mt-2 text-[13px] text-faint">Viewing only. An admin can change these.</p>
		{/if}

		<section class="mt-7">
			<p class="pb-2 text-[13px] font-semibold">Mode</p>
			<SettingRow
				name="REWRITE_MODE"
				label="What gets rewritten"
				align="start"
				desc="How far trackstarr may go. Each step includes the one before it."
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<div class="flex w-full flex-col gap-2 sm:w-72">
						<Segmented
							fill
							options={MODES}
							{labelledBy}
							describedBy={MODE_NOTES[mode] ? `${describedBy} ${modeNote}` : describedBy}
							value={mode}
							disabled={envLocked('REWRITE_MODE')}
							onchange={(value) => (draft.REWRITE_MODE = value)}
						/>
						{#if MODE_NOTES[mode]}
							<p id={modeNote} class={`${noteBox} text-dim`}>{MODE_NOTES[mode]}</p>
						{/if}
					</div>
				{/snippet}
			</SettingRow>
		</section>

		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Clock</p>
			<SettingRow
				name="TZ"
				label="Time zone"
				align="start"
				desc="The clock the sweep schedule, events and log lines use. An IANA name such as Pacific/Auckland. Empty means UTC."
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<div class="flex w-full flex-col gap-2 sm:w-72">
						<!-- A datalist: 600 zones is no select. -->
						<input
							value={(draft.TZ as string) ?? ''}
							oninput={(event) => (draft.TZ = event.currentTarget.value)}
							list="zone-options"
							placeholder="UTC"
							inputmode="text"
							autocapitalize="none"
							autocorrect="off"
							spellcheck="false"
							aria-labelledby={labelledBy}
							aria-describedby={zoneNow || zone ? `${describedBy} ${zoneNote}` : describedBy}
							disabled={envLocked('TZ')}
							class={field}
						/>
						<!-- The browser's own list, answering per keystroke. -->
						{#if zoneNow}
							<p id={zoneNote} class={`${noteBox} text-dim`}>That clock reads {zoneNow} now.</p>
						{:else if zone}
							<p id={zoneNote} class={`${noteBox} text-danger`}>There is no zone named {zone}.</p>
						{/if}
					</div>
				{/snippet}
			</SettingRow>
		</section>

		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Limits</p>
			<SettingRow
				name="MAX_CONCURRENT_REWRITES"
				label="Rewrites at once"
				align="start"
				desc="Rewrites running at once, shared by imports, sweeps and anything else on the same state directory."
			>
				{#snippet children({ labelledBy, describedBy })}
					{@render numberField('MAX_CONCURRENT_REWRITES', labelledBy, describedBy, 1, '')}
				{/snippet}
			</SettingRow>
			<SettingRow
				name="PROBE_WORKERS"
				label="Probes at once"
				align="start"
				desc="Files a sweep probes at once. Separate from Rewrites at once: a probe is short and cheap, so a disk that wants one rewrite at a time still takes several probes."
			>
				{#snippet children({ labelledBy, describedBy })}
					{@render numberField('PROBE_WORKERS', labelledBy, describedBy, 1, '')}
				{/snippet}
			</SettingRow>
			<SettingRow
				name="FFMPEG_TIMEOUT"
				label="Rewrite timeout"
				align="start"
				desc="How long one ffmpeg run may take before it is killed and the file left as it was."
			>
				{#snippet children({ labelledBy, describedBy })}
					{@render numberField('FFMPEG_TIMEOUT', labelledBy, describedBy, 1, 'seconds')}
				{/snippet}
			</SettingRow>
			<SettingRow
				name="PROBE_TIMEOUT"
				label="Probe timeout"
				align="start"
				desc="How long ffprobe may take to describe one file."
			>
				{#snippet children({ labelledBy, describedBy })}
					{@render numberField('PROBE_TIMEOUT', labelledBy, describedBy, 1, 'seconds')}
				{/snippet}
			</SettingRow>
		</section>

		<SaveBar {settings} />
	</fieldset>
</Page>
