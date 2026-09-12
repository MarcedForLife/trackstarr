<script lang="ts">
	import { SERVICES } from '$lib/connections';
	import { button, field } from '$lib/controls';
	import ConnectionCard from '$lib/components/ConnectionCard.svelte';
	import NumberField from '$lib/components/NumberField.svelte';
	import Page from '$lib/components/Page.svelte';
	import SaveBar from '$lib/components/SaveBar.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import Toggle from '$lib/components/Toggle.svelte';
	import { provideSettings, SettingsDraft } from '$lib/draft.svelte';
	import { wholeUnits } from '$lib/format';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const readOnly = $derived(data.user?.role !== 'admin');

	// Typed: `dropping` below reads the baseline off the draft it is handed to.
	// svelte-ignore state_referenced_locally
	const settings: SettingsDraft = new SettingsDraft(data.snapshot.settings, {
		readOnly: () => readOnly,
		// An empty field means "leave it alone", so cleared names travel as null.
		// The diff cannot see them, since the service never echoes a credential.
		dropping: () =>
			Object.keys(cleared).filter(
				(name) => cleared[name] && baseline[name]?.set && !baseline[name]?.env
			)
	});
	// The rows below name a setting and read the rest off the draft.
	provideSettings(settings);
	// Both records are mutated in place, so these read the live objects after a
	// save.
	const draft = settings.draft;
	const baseline = settings.baseline;
	const envLocked = (name: string) => settings.envLocked(name);

	// Which credentials the reader emptied. The page's, since the draft turns one
	// into a change.
	let cleared = $state<Record<string, boolean>>({});
	settings.onreset(() => (cleared = {}));

	const SOURCES = SERVICES.filter((service) => service.group === 'source');
	const LIBRARIES = SERVICES.filter((service) => service.group === 'library');

	// Test all is a press passed on to each card.
	const cards: Record<string, ConnectionCard | undefined> = $state({});

	function testAll() {
		for (const service of SERVICES) cards[service.name]?.test();
	}

	function text(name: string): string {
		return String(draft[name] ?? '');
	}

	// A string, as the settings file holds it. Checked while the field is open.
	function recheckProblem(): string {
		const entry = text('HARDLINK_RECHECK').trim();
		if (!/^-?\d+$/.test(entry)) return 'That has to be a whole number.';
		if (Number(entry) < 0) return 'That cannot be below 0.';
		return '';
	}

	// Zero is a real answer, distinct from the row above being off.
	const recheckNote = $derived(
		text('HARDLINK_RECHECK').trim() === '0'
			? 'At 0 nothing waits: a seeding file is skipped as it arrives and left to the next sweep.'
			: ''
	);
</script>

<Page
	eyebrow="Settings"
	lead="The services trackstarr talks to, and how imports are handled. Changes apply without a restart."
>
	<!-- Inert while a save is in flight, so the response cannot land on a
	     keystroke it never carried. min-w-0, or a fieldset will not shrink. -->
	<fieldset disabled={settings.busy} class="min-w-0">
		{#if readOnly}
			<p class="mt-2 text-[13px] text-faint">Viewing only. An admin can change or test these.</p>
		{/if}

		<div class="mt-7 flex items-end justify-between gap-3">
			<div class="min-w-0">
				<p class="text-[13px] font-semibold">Sources</p>
				<p class="mt-0.5 text-[13px] leading-snug text-pretty text-dim">
					Radarr and Sonarr, which call trackstarr as they import. Each gets a webhook connection on
					save.
				</p>
			</div>
			<button onclick={testAll} disabled={readOnly} class={`flex-none ${button}`}>Test all</button>
		</div>
		<div class="mt-3 flex flex-col gap-2">
			{#each SOURCES as service (service.name)}
				<ConnectionCard
					bind:this={cards[service.name]}
					{service}
					{settings}
					{readOnly}
					cleared={!!cleared[service.key]}
					onclear={(yes) => (cleared[service.key] = yes)}
				/>
			{/each}
		</div>

		<div class="mt-7">
			<p class="text-[13px] font-semibold">Libraries</p>
			<p class="mt-0.5 text-[13px] leading-snug text-pretty text-dim">
				The media servers told to rescan what was rewritten. Most installs have one, not both.
			</p>
		</div>
		<div class="mt-3 flex flex-col gap-2">
			{#each LIBRARIES as service (service.name)}
				<ConnectionCard
					bind:this={cards[service.name]}
					{service}
					{settings}
					{readOnly}
					cleared={!!cleared[service.key]}
					onclear={(yes) => (cleared[service.key] = yes)}
				/>
			{/each}
		</div>

		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Callback</p>
			<SettingRow
				name="WEBHOOK_URL"
				label="Webhook address"
				desc="The address Radarr and Sonarr call, so it must be reachable from their containers. Base URL only, the path is added."
				stack
			>
				{#snippet children({ labelledBy, describedBy })}
					<input
						value={(draft.WEBHOOK_URL as string) ?? ''}
						oninput={(event) => (draft.WEBHOOK_URL = event.currentTarget.value)}
						placeholder="http://trackstarr:5120"
						inputmode="url"
						autocapitalize="none"
						autocorrect="off"
						spellcheck="false"
						aria-labelledby={labelledBy}
						aria-describedby={describedBy}
						disabled={envLocked('WEBHOOK_URL')}
						class={`${field} sm:w-64`}
					/>
				{/snippet}
			</SettingRow>
		</section>

		<section class="mt-8">
			<p class="pb-2 text-[13px] font-semibold">Imports</p>
			<p class="pb-1 text-[13px] text-dim">
				What happens to an imported file the download client is still seeding.
			</p>
			<SettingRow
				name="SKIP_HARDLINKS"
				label="Leave seeding files alone"
				desc="Leave a file alone while the download client still hard-links it. Rewriting breaks the link, so the file takes disk twice until the torrent is removed."
			>
				{#snippet children({ labelledBy, describedBy })}
					<Toggle
						on={draft.SKIP_HARDLINKS as boolean}
						{labelledBy}
						{describedBy}
						disabled={envLocked('SKIP_HARDLINKS')}
						onchange={(on) => (draft.SKIP_HARDLINKS = on)}
					/>
				{/snippet}
			</SettingRow>
			<SettingRow
				name="HARDLINK_RECHECK"
				label="Recheck every"
				align="start"
				desc="How often a waiting file is checked again, so it is rewritten shortly after seeding ends."
				note={recheckNote}
				nested
				dim={!draft.SKIP_HARDLINKS}
			>
				{#snippet children({ labelledBy, describedBy })}
					<NumberField
						value={text('HARDLINK_RECHECK')}
						onchange={(value) => (draft.HARDLINK_RECHECK = value)}
						{labelledBy}
						{describedBy}
						unit="seconds"
						problem={recheckProblem()}
						note={wholeUnits(Number(text('HARDLINK_RECHECK')))}
						disabled={envLocked('HARDLINK_RECHECK')}
					/>
				{/snippet}
			</SettingRow>
		</section>

		<SaveBar {settings} />
	</fieldset>
</Page>
