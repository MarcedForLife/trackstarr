<script lang="ts">
	import { saveSettings, type ArrInstance, type SettingValue } from '$lib/settings';
	import {
		ARR_FIELDS,
		arrField,
		connectionSettings,
		connectionChanges,
		SERVICES,
		newInstance,
		sources,
		type Service,
		type ServiceName
	} from '$lib/connections';
	import { button, field } from '$lib/controls';
	import ConnectionCard from '$lib/components/ConnectionCard.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import NewConnection from '$lib/components/NewConnection.svelte';
	import NumberField from '$lib/components/NumberField.svelte';
	import Page from '$lib/components/Page.svelte';
	import SaveBar from '$lib/components/SaveBar.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import Toggle from '$lib/components/Toggle.svelte';
	import { provideSettings, SettingsDraft } from '$lib/draft.svelte';
	import { wholeUnits } from '$lib/format';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Descriptors persist separately from editable fields; names never move IDs.
	// svelte-ignore state_referenced_locally
	let savedInstances = data.snapshot.arr_instances;
	let instances = $state<ArrInstance[]>([...savedInstances]);

	const readOnly = $derived(data.user?.role !== 'admin');

	// Typed: `dropping` below reads the baseline off the draft it is handed to.
	// svelte-ignore state_referenced_locally
	const settings: SettingsDraft = new SettingsDraft(connectionSettings(data.snapshot), {
		save: async (changes) => {
			const snapshot = await saveSettings(
				connectionChanges(instances, baseline, changes, new Set(Object.keys(removed)))
			);
			savedInstances = snapshot.arr_instances;
			return connectionSettings(snapshot);
		},
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
	// Cards taken off, held until the save so Undo can put them back.
	let removed = $state<Record<string, { service: Service; values: Record<string, SettingValue> }>>(
		{}
	);
	// Fixed services added this visit and still empty keep a card.
	let shown = $state<Record<string, boolean>>({});
	// The card just added opens on arrival.
	let opened = $state<Record<string, boolean>>({});
	settings.onreset(() => {
		cleared = {};
		removed = {};
		opened = {};
		shown = {};
		instances = [...savedInstances];
	});

	/** The setting names one card edits. */
	function names(service: Service): string[] {
		return [service.url, service.key, service.publicUrl, service.map, service.nameField].filter(
			(name): name is string => !!name
		);
	}
	/** Whether the saved settings hold anything for this service. */
	function saved(service: Service): boolean {
		return names(service).some(
			(name) => baseline[name]?.set || !!baseline[name]?.value || baseline[name]?.env
		);
	}
	/** A fixed service earns its card by holding something, saved or typed, or
	 * by being added; a named instance has one for as long as it has keys. */
	function present(service: Service): boolean {
		if (service.instance && service.name !== service.type) return true;
		return saved(service) || !!shown[service.name] || names(service).some((name) => draft[name]);
	}
	const SOURCES = $derived(
		SERVICES.filter((service) => service.group === 'source').flatMap((kind) =>
			sources(instances, draft).filter(
				(service) => service.type === kind.name && (present(service) || removed[service.name])
			)
		)
	);
	const LIBRARIES = $derived(
		SERVICES.filter((service) => service.group === 'library' && present(service))
	);
	const ADDED = $derived(
		new Set(
			SERVICES.filter(
				(service) => service.group === 'library' && (present(service) || removed[service.name])
			).map((service) => service.name)
		)
	);

	function add(name: ServiceName) {
		const fixed =
			sources(instances, draft).find((service) => service.name === name) ??
			SERVICES.find((service) => service.name === name)!;
		if (!present(fixed) && !removed[name]) {
			shown[name] = true;
			opened[name] = true;
			return;
		}
		if (name !== 'radarr' && name !== 'sonarr') return;
		const instance = newInstance(name);
		instances.push(instance);
		for (const field of ARR_FIELDS) draft[arrField(instance.id, field)] = '';
		opened[instance.id] = true;
	}

	function rename(service: Service, text: string) {
		draft[service.nameField!] = text;
	}

	/** Removal is held for Undo, then sent as one connection operation. */
	function remove(service: Service) {
		delete shown[service.name];
		delete opened[service.name];
		if (!(service.url in baseline)) {
			instances = instances.filter((instance) => instance.id !== service.name);
			for (const name of names(service)) delete draft[name];
			return;
		}
		const values: Record<string, SettingValue> = {};
		for (const name of names(service)) {
			if (name in draft) values[name] = draft[name];
			delete draft[name];
		}
		removed[service.name] = { service, values };
	}

	function undoRemoval(name: string) {
		Object.assign(draft, removed[name].values);
		delete removed[name];
	}
	function removable(service: Service): boolean {
		return !readOnly && !names(service).some(envLocked);
	}

	// Test all is a press passed on to each card.
	const cards: Record<string, ConnectionCard | undefined> = $state({});
	let testingAll = $state(false);

	/** Every card at once, held busy until the slowest answers. The floor is its
	 * own, since a page where nothing is configured has nothing to wait for. */
	async function testAll() {
		const feedback = new Promise<void>((resolve) => setTimeout(resolve, 600));
		testingAll = true;
		try {
			await Promise.all(
				[...SOURCES, ...LIBRARIES].map((service) => cards[service.name]?.test(600))
			);
		} finally {
			await feedback;
			testingAll = false;
		}
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

{#snippet card(service: Service)}
	{#if removed[service.name]}
		<div class="rounded-xl border border-line bg-raised p-3">
			<p class="text-sm font-semibold">{removed[service.name].service.label}</p>
			<p role="status" class="mt-1 text-[13px] text-dim">
				{service.group === 'source'
					? 'Will be removed on save. Imports from this connection will no longer be accepted.'
					: 'Will be removed on save. It will no longer be asked to rescan.'}
			</p>
			<button
				type="button"
				class={`${button} mt-2`}
				onclick={() => undoRemoval(service.name)}
				aria-label={`Undo removal of ${removed[service.name].service.label}`}>Undo</button
			>
		</div>
	{:else}
		<ConnectionCard
			bind:this={cards[service.name]}
			{service}
			{settings}
			{readOnly}
			cleared={!!cleared[service.key]}
			onclear={(yes) => (cleared[service.key] = yes)}
			opened={!!opened[service.name]}
			onname={service.nameField ? (typed) => rename(service, typed) : undefined}
			onremove={removable(service) ? () => remove(service) : undefined}
		/>
	{/if}
{/snippet}

<Page
	lead="The services trackstarr talks to, and how imports are handled. Changes apply without a restart."
>
	<!-- Inert while a save is in flight, so the response cannot land on a
	     keystroke it never carried. min-w-0, or a fieldset will not shrink. -->
	<fieldset disabled={settings.busy} class="min-w-0">
		{#if readOnly}
			<p class="mt-2 text-[13px] text-faint">Viewing only. An admin can change or test these.</p>
		{/if}

		<div class="mt-6 flex items-center justify-between gap-2">
			{#if readOnly}
				<span></span>
			{:else}
				<NewConnection added={ADDED} onpick={add} />
			{/if}
			<!-- Held wide, so Checking does not shift it. -->
			<button
				type="button"
				onclick={testAll}
				disabled={readOnly || testingAll}
				aria-label={testingAll ? 'Checking every connection' : 'Test every connection'}
				aria-busy={testingAll}
				class={`min-w-30 flex-none ${button}`}
			>
				<span class="flex" class:turning={testingAll}><Glyph name="refresh" /></span>
				<span role="status">{testingAll ? 'Checking…' : 'Test all'}</span>
			</button>
		</div>

		<div class="mt-6">
			<p class="text-[13px] font-semibold">Sources</p>
			<p class="mt-0.5 text-[13px] leading-snug text-pretty text-dim">
				Radarr and Sonarr, which call trackstarr as they import. Each gets a webhook connection on
				save.
			</p>
		</div>
		<div class="mt-3 flex flex-col gap-2">
			{#each SOURCES as service (service.name)}
				{@render card(service)}
			{:else}
				<p class="text-[13px] text-faint">None yet.</p>
			{/each}
		</div>

		<div class="mt-7">
			<p class="text-[13px] font-semibold">Libraries</p>
			<p class="mt-0.5 text-[13px] leading-snug text-pretty text-dim">
				The media servers told to rescan what was rewritten.
			</p>
		</div>
		<div class="mt-3 flex flex-col gap-2">
			{#each LIBRARIES as service (service.name)}
				{@render card(service)}
			{:else}
				<p class="text-[13px] text-faint">None yet.</p>
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
				desc="Leave a file alone while the download client still hard-links it. Rewriting breaks the link."
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
