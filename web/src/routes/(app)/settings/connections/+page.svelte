<script lang="ts">
	import type { SettingValue } from '$lib/settings';
	import {
		INSTANCE_SUFFIXES,
		SERVICES,
		instanceId,
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
	// Cards taken off, held until the save so Undo can put them back.
	let removed = $state<Record<string, { service: Service; values: Record<string, SettingValue> }>>(
		{}
	);
	// Fixed services added this visit and still empty, so they keep a card. By prefix.
	let shown = $state<Record<string, boolean>>({});
	// The card just added opens on arrival, by prefix.
	let opened = $state<Record<string, boolean>>({});
	// A fresh instance's keys follow its name, so its card is keyed by a token
	// that moves with them, or every keystroke would remount it.
	let tokens = $state<Record<string, number>>({});
	let nextToken = 1;
	settings.onreset((saved) => {
		cleared = {};
		removed = {};
		opened = {};
		// A discard puts the page back as the snapshot has it. After a save an
		// added card stays, filled or not.
		if (!saved) {
			shown = {};
			tokens = {};
		}
	});

	/** The setting names one card edits. */
	function names(service: Service): string[] {
		return [service.url, service.key, service.publicUrl, service.map, service.labelName].filter(
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
		if (service.kind) return true;
		return saved(service) || !!shown[service.prefix] || names(service).some((name) => draft[name]);
	}
	/** Not yet saved, so its keys still follow its name. */
	function fresh(service: Service): boolean {
		return !!service.kind && !(service.url in baseline);
	}

	// Keep each kind together: its fixed connection, saved instances, then new
	// ones in creation order. Renaming a draft must not move the card being edited.
	// A removed one keeps its place, as the notice standing in for it.
	const SOURCES = $derived.by(() => {
		const standing = Object.fromEntries(
			Object.values(removed).map(({ service }) => [service.url, ''])
		);
		const all = sources({ ...draft, ...standing });
		const ordered = [
			...all.filter((service) => !fresh(service) && present(service)),
			...all
				.filter(fresh)
				.sort((one, other) => (tokens[one.prefix] ?? 0) - (tokens[other.prefix] ?? 0))
		];
		return SERVICES.filter((service) => service.group === 'source').flatMap((kind) =>
			ordered.filter((service) => (service.kind ?? service.name) === kind.name)
		);
	});
	const LIBRARIES = $derived(
		SERVICES.filter((service) => service.group === 'library' && present(service))
	);
	// A media server is one card, so the menu dims it once it has one.
	const ADDED = $derived(
		new Set(
			SERVICES.filter(
				(service) => service.group === 'library' && (present(service) || removed[service.name])
			).map((service) => service.name)
		)
	);

	/** The IDs in use for a kind, the card named `except` aside. */
	function instanceIds(kind: ServiceName, except = ''): Set<string> {
		const held = [...sources(draft), ...Object.values(removed).map(({ service }) => service)];
		return new Set(
			held
				.filter((service) => service.kind === kind && service.prefix !== except)
				.map((service) => service.prefix.slice(kind.length + 1))
		);
	}

	/** A card for the service picked: the fixed one, when it has none, else a
	 * fresh instance under the next free number until it is named. */
	function add(name: ServiceName) {
		const fixed = SERVICES.find((service) => service.name === name)!;
		if (!present(fixed) && !removed[name]) {
			shown[fixed.prefix] = true;
			opened[fixed.prefix] = true;
			return;
		}
		if (fixed.group === 'library') return;
		const prefix = `${name.toUpperCase()}_${instanceId('', instanceIds(name))}`;
		for (const suffix of INSTANCE_SUFFIXES) draft[`${prefix}_${suffix}`] = '';
		tokens[prefix] = nextToken++;
		opened[prefix] = true;
	}

	/** Name a card. A fresh instance's keys move with the ID its name earns; a
	 * saved one keeps its keys and changes only its label. */
	function rename(service: Service, text: string) {
		let prefix = service.prefix;
		if (fresh(service)) {
			const kind = service.kind!;
			const to = `${kind.toUpperCase()}_${instanceId(text, instanceIds(kind, prefix))}`;
			if (to !== prefix) {
				for (const suffix of INSTANCE_SUFFIXES) {
					draft[`${to}_${suffix}`] = draft[`${prefix}_${suffix}`] ?? '';
					delete draft[`${prefix}_${suffix}`];
				}
				tokens[to] = tokens[prefix];
				opened[to] = opened[prefix];
				delete tokens[prefix];
				delete opened[prefix];
				prefix = to;
			}
		}
		draft[`${prefix}_LABEL`] = text;
	}

	/** Take a card off. A saved connection is removed on save, with its values
	 * held for Undo; one never saved just goes, a fixed one back to its blanks. */
	function remove(service: Service) {
		delete shown[service.prefix];
		delete opened[service.prefix];
		if (!saved(service)) {
			for (const name of names(service)) {
				if (service.kind) delete draft[name];
				else draft[name] = Array.isArray(baseline[name]?.value) ? [] : '';
			}
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
			opened={!!opened[service.prefix]}
			onname={service.labelName ? (typed) => rename(service, typed) : undefined}
			onremove={removable(service) ? () => remove(service) : undefined}
		/>
	{/if}
{/snippet}

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
			{#each SOURCES as service (tokens[service.prefix] ?? service.name)}
				{@render card(service)}
			{:else}
				<p class="text-[13px] text-faint">None yet.</p>
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
