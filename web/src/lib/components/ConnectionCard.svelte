<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import PathMapList from '$lib/components/PathMapList.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import { testConnection, type ConnectionResult, type Service } from '$lib/connections';
	import { button, field, noteBox, removeButton } from '$lib/controls';
	import type { SettingsDraft } from '$lib/draft.svelte';

	// One service: shut, a line saying whether it answers; open, its fields. Owns
	// its own check, so a slow Plex never holds up the Radarr card, and asks
	// again when a save touches its fields.
	let {
		service,
		settings,
		readOnly,
		cleared,
		onclear
	}: {
		service: Service;
		settings: SettingsDraft;
		readOnly: boolean;
		// Whether the reader emptied the stored credential. The page's, since an
		// empty field means "leave it alone" and a clear is the draft sending null.
		cleared: boolean;
		onclear: (yes: boolean) => void;
	} = $props();

	const draft = $derived(settings.draft);
	const baseline = $derived(settings.baseline);

	// All shut on arrival: the shut row says which services work.
	let open = $state(false);
	let result = $state<ConnectionResult | null>(null);
	let checking = $state(false);

	// A credential is on file and the reader has not emptied it.
	const kept = $derived(!!baseline[service.key]?.set && !cleared);
	// A cleared key counts as gone at once, or Test would answer for it.
	const configured = $derived(!!(draft[service.url] as string) && (!!draft[service.key] || kept));

	// Edits to a shut card still count towards the save bar, so the row says so.
	// The two optional names fall back to '', which no setting is called.
	const edited = $derived(
		settings.anyChanged([service.url, service.key, service.map ?? '', service.publicUrl ?? ''])
	);

	// An answer arriving after the card has gone is dropped.
	let gone = false;

	/** Ask the service whether it is there, if there is enough to ask with. */
	export async function test() {
		if (!configured) return;
		checking = true;
		try {
			const answer = await testConnection(
				service.name,
				(draft[service.url] as string) ?? '',
				// Only what was typed: an untouched field sent blank reads as cleared.
				(draft[service.key] as string) ?? ''
			);
			if (!gone) result = answer;
		} catch {
			if (!gone) {
				result = { ok: false, detail: 'Could not reach the service.', hint: '', webhook: '' };
			}
		} finally {
			if (!gone) checking = false;
		}
	}

	// Once on arrival, for an admin; a viewer gets the stored state. From an
	// effect for the teardown, tracking nothing, or every keystroke would ask.
	$effect(() => {
		untrack(() => {
			if (!readOnly) test();
		});
		return () => {
			gone = true;
		};
	});

	// A save has landed, so the saved values matter now, and a touched *arr has a
	// fresh webhook connection.
	// svelte-ignore state_referenced_locally
	onDestroy(
		settings.onreset((saved) => {
			if (saved && (service.url in saved || service.key in saved)) test();
		})
	);

	const pill = $derived.by(() => {
		if (checking) return { label: 'Checking', class: 'text-faint' };
		if (!configured) return { label: 'Not set', class: 'text-faint' };
		if (!result) return { label: 'Configured', class: 'text-dim' };
		return result.ok
			? { label: 'Connected', class: 'text-ok' }
			: { label: 'No answer', class: 'text-danger' };
	});

	// What an *arr holding the wrong connection, or none, means for the reader.
	const WEBHOOK_STATE: Record<string, string> = {
		connected: 'Webhook connected',
		missing: 'No webhook connection yet',
		stale: 'Its webhook points somewhere else',
		unknown: 'Could not read its connections list'
	};

	function webhookNote(answer: ConnectionResult): string {
		const state = WEBHOOK_STATE[answer.webhook] ?? '';
		if (!state || answer.webhook === 'connected') return state;
		return `${state}. Trackstarr registers it on save.`;
	}

	// The shut card's line: what is wrong, what is nearly wrong, where it lives,
	// what it is for.
	const line = $derived.by(() => {
		if (result && !result.ok) return { text: result.detail, class: 'text-danger', mono: false };
		if (result?.ok && result.webhook && result.webhook !== 'connected') {
			return { text: WEBHOOK_STATE[result.webhook] ?? '', class: 'text-accent', mono: false };
		}
		const address = (draft[service.url] as string) ?? '';
		if (address) return { text: address, class: 'text-dim', mono: true };
		return { text: service.lead, class: 'text-faint', mono: false };
	});

	// One field width, and a 28px slot for the clear button whether or not the
	// row has one, so every box ends on the same line.
	const entry = `${field} sm:w-64`;
	const entryRow = 'flex w-full items-center gap-2 sm:w-auto';
	const gutter = 'flex w-7 flex-none items-center justify-end';
</script>

<div class="overflow-hidden rounded-xl border border-line bg-raised">
	<!-- The whole row is the control. Shut, the fields unmount; the values live
	     in `draft`. -->
	<Disclosure
		id={`${service.name}-fields`}
		{open}
		ontoggle={() => (open = !open)}
		class="group relative flex w-full items-center gap-3 overflow-hidden px-3 py-3 text-left"
		mark={15}
		turn="half"
		press
		panelClass=""
	>
		{#snippet summary(chevron)}
			<ServiceIcon name={service.name} />
			<span class="min-w-0 flex-1">
				<span class="flex items-center gap-2">
					<span class="text-sm font-semibold">{service.label}</span>
					{#if edited}
						<span class="h-1.5 w-1.5 flex-none rounded-full bg-accent"></span>
						<span class="sr-only">Unsaved changes</span>
					{/if}
				</span>
				<span
					class={`mt-0.5 block truncate text-[12.5px] ${line.class} ${line.mono ? 'font-mono' : ''}`}
				>
					{line.text}
				</span>
			</span>
			<span class={`flex-none text-[11px] font-medium ${pill.class}`}>{pill.label}</span>
			{@render chevron()}
		{/snippet}

		{#snippet panel()}
			<div class="border-t border-line px-3 pb-1">
				<div class="flex items-start justify-between gap-3 py-3">
					<p class="text-[13px] leading-snug text-pretty text-dim">{service.lead}</p>
					<button
						onclick={test}
						disabled={readOnly || !configured || checking}
						class={`flex-none ${button}`}
					>
						Test
					</button>
				</div>

				{#if result}
					<div class={`mb-3 ${noteBox} ${result.ok ? 'text-dim' : 'text-danger'}`}>
						<p>{result.detail}</p>
						{#if result.ok && webhookNote(result)}
							<p class={`mt-1 ${result.webhook === 'connected' ? 'text-ok' : 'text-dim'}`}>
								{webhookNote(result)}
							</p>
						{/if}
						{#if result.hint}
							<p class="mt-1 text-faint">{result.hint}</p>
						{/if}
					</div>
				{/if}

				<SettingRow
					name={service.url}
					label="Address"
					desc={`Where ${service.label} answers, as this container reaches it.`}
					stack
				>
					<!-- Four cards have an Address row, so the field's name says whose. -->
					{#snippet children({ describedBy })}
						<div class={entryRow}>
							<input
								value={(draft[service.url] as string) ?? ''}
								oninput={(event) => (draft[service.url] = event.currentTarget.value)}
								placeholder={service.placeholder}
								inputmode="url"
								autocapitalize="none"
								autocorrect="off"
								spellcheck="false"
								aria-label={`${service.label} address`}
								aria-describedby={describedBy}
								disabled={settings.envLocked(service.url)}
								class={entry}
							/>
							<div class={gutter}></div>
						</div>
					{/snippet}
				</SettingRow>

				<SettingRow
					name={service.key}
					label={service.keyLabel}
					desc={kept
						? 'One is stored. Type a new one to replace it, or clear it to switch the service off.'
						: `Found in ${service.label}'s own settings.`}
					stack
				>
					{#snippet children({ describedBy })}
						<div class={entryRow}>
							<input
								value={(draft[service.key] as string) ?? ''}
								oninput={(event) => {
									draft[service.key] = event.currentTarget.value;
									onclear(false);
								}}
								type="password"
								placeholder={kept ? '••••••••' : 'Not set'}
								autocapitalize="none"
								autocorrect="off"
								spellcheck="false"
								autocomplete="off"
								aria-label={`${service.label} ${service.keyLabel.toLowerCase()}`}
								aria-describedby={describedBy}
								disabled={settings.envLocked(service.key)}
								class={entry}
							/>
							<div class={gutter}>
								{#if kept}
									<button
										aria-label={`Clear the ${service.label} ${service.keyLabel.toLowerCase()}`}
										disabled={settings.envLocked(service.key)}
										onclick={() => {
											onclear(true);
											draft[service.key] = '';
										}}
										class={removeButton}
									>
										<Glyph name="cross" />
									</button>
								{/if}
							</div>
						</div>
					{/snippet}
				</SettingRow>

				{#if service.publicUrl}
					{@const name = service.publicUrl}
					<SettingRow
						{name}
						label="Public address"
						desc={`Where a title's "Open in ${service.label}" link points. Only needed when a browser cannot reach the address above.`}
						stack
					>
						{#snippet children({ describedBy })}
							<div class={entryRow}>
								<input
									value={(draft[name] as string) ?? ''}
									oninput={(event) => (draft[name] = event.currentTarget.value)}
									placeholder={(draft[service.url] as string) || service.placeholder}
									inputmode="url"
									autocapitalize="none"
									autocorrect="off"
									spellcheck="false"
									aria-label={`${service.label} public address`}
									aria-describedby={describedBy}
									disabled={settings.envLocked(name)}
									class={entry}
								/>
								<div class={gutter}></div>
							</div>
						{/snippet}
					</SettingRow>
				{/if}

				{#if service.map}
					{@const name = service.map}
					<!-- A list, so it drops below the description. -->
					<SettingRow
						{name}
						label="Path map"
						desc={`Only needed when ${service.label} sees the library at a different path. Ours on the left.`}
						full
					>
						{#snippet children({ labelledBy, describedBy })}
							<PathMapList
								{settings}
								{name}
								label={service.label}
								{labelledBy}
								{describedBy}
								{entry}
								{entryRow}
								{gutter}
							/>
						{/snippet}
					</SettingRow>
				{/if}
			</div>
		{/snippet}
	</Disclosure>
</div>
