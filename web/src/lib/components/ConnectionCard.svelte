<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import Disclosure from '$lib/components/Disclosure.svelte';
	import Glyph from '$lib/components/Glyph.svelte';
	import PathMapList from '$lib/components/PathMapList.svelte';
	import ServiceIcon from '$lib/components/ServiceIcon.svelte';
	import SettingRow from '$lib/components/SettingRow.svelte';
	import {
		fallbackName,
		nameProblem as validateName,
		testConnection,
		type ConnectionResult,
		type Service
	} from '$lib/connections';
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
		onclear,
		opened = false,
		onname,
		onremove
	}: {
		service: Service;
		settings: SettingsDraft;
		readOnly: boolean;
		// Whether the reader emptied the stored credential. The page's, since an
		// empty field means "leave it alone" and a clear is the draft sending null.
		cleared: boolean;
		onclear: (yes: boolean) => void;
		// Open on arrival, with the first field focused: the card just added.
		opened?: boolean;
		// The editable display name; identity stays fixed.
		onname?: (text: string) => void;
		// Absent for a card the environment pins, which has no Remove.
		onremove?: () => void;
	} = $props();

	const draft = $derived(settings.draft);
	const baseline = $derived(settings.baseline);

	// Shut on arrival, since the shut row says which services work. The card
	// just added opens: it has nothing to say yet.
	// svelte-ignore state_referenced_locally
	let open = $state(opened);
	let result = $state<ConnectionResult | null>(null);
	let checking = $state(false);

	// A credential is on file and the reader has not emptied it.
	const kept = $derived(!!baseline[service.key]?.set && !cleared);
	// A cleared key counts as gone at once, or Test would answer for it.
	const configured = $derived(!!(draft[service.url] as string) && (!!draft[service.key] || kept));

	// Edits to a shut card still count towards the save bar, so the row says so.
	// The optional names fall back to '', which no setting is called.
	const edited = $derived(
		settings.anyChanged([
			service.url,
			service.key,
			service.map ?? '',
			service.publicUrl ?? '',
			service.nameField ?? ''
		])
	);

	const nameText = $derived(String(draft[service.nameField ?? ''] ?? ''));
	const nameProblem = $derived(validateName(nameText));
	const nameDesc = $derived(`Optional. Shown instead of ${fallbackName(service)}.`);

	// A card just added starts in its address, the one field it cannot do without.
	let addressInput = $state<HTMLInputElement>();
	let pathMap = $state<PathMapList>();
	onMount(async () => {
		if (!opened) return;
		await tick();
		addressInput?.focus();
	});

	// A result belongs to the exact draft and saved callback it tested. Editing
	// invalidates it without issuing a request on every keystroke.
	const input = $derived(
		JSON.stringify([
			service.name,
			draft[service.url],
			draft[service.key],
			service.map ? draft[service.map] : null,
			service.map ? baseline[service.map]?.value : null,
			kept,
			service.group === 'source' ? draft.WEBHOOK_URL : null,
			service.group === 'source' ? baseline.WEBHOOK_URL?.value : null
		])
	);
	let testedInput = '';
	let testTicket = 0;
	function invalidate() {
		if (testedInput === input) return;
		testedInput = input;
		testTicket++;
		result = null;
		checking = false;
	}
	$effect(() => {
		void input;
		untrack(invalidate);
	});

	// An answer arriving after the card has gone is dropped.
	let gone = false;

	/** Ask the service whether it is there, if there is enough to ask with.
	 * `minimumMs` holds the busy cue open that long, so a fast answer still
	 * reads as work. The floor runs alongside the request, not after it. */
	export async function test(minimumMs = 0) {
		invalidate();
		if (!configured) return;
		const ticket = ++testTicket;
		const asked = input;
		const current = () => !gone && ticket === testTicket && asked === input;
		const feedback = minimumMs
			? new Promise<void>((resolve) => setTimeout(resolve, minimumMs))
			: undefined;
		checking = true;
		try {
			const answer = await testConnection(
				service.name,
				(draft[service.url] as string) ?? '',
				// Only what was typed: an untouched field sent blank reads as cleared.
				(draft[service.key] as string) ?? ''
			);
			if (current()) result = answer;
		} catch {
			if (current()) {
				result = {
					ok: false,
					detail: 'Could not reach the service.',
					hint: '',
					webhook: '',
					webhook_detail: ''
				};
			}
		} finally {
			await feedback;
			if (current()) checking = false;
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
			if (
				saved &&
				(service.url in saved ||
					service.key in saved ||
					(service.map !== undefined && service.map in saved) ||
					(service.group === 'source' && 'WEBHOOK_URL' in saved))
			) {
				// Even unchanged/redacted values can now refer to a new saved key.
				testTicket++;
				result = null;
				test();
			}
		})
	);

	const mappingEdited = $derived(!!service.map && settings.changed(service.map));

	const callbackEdited = $derived(service.group === 'source' && settings.changed('WEBHOOK_URL'));

	const pill = $derived.by(() => {
		if (checking) return { label: 'Checking', class: 'text-faint' };
		if (!configured) return { label: 'Not set', class: 'text-faint' };
		if (!result) return { label: 'Configured', class: 'text-dim' };
		if (!result.ok) return { label: 'No answer', class: 'text-danger' };
		// A green Connected on an *arr that cannot call back is the lie this
		// check exists to catch.
		if (callbackEdited) return { label: 'API connected', class: 'text-accent' };
		if (result.webhook === 'unreachable') return { label: 'No webhook', class: 'text-danger' };
		if (service.group === 'source' && result.webhook !== 'connected')
			return { label: 'API connected', class: 'text-accent' };
		return { label: 'Connected', class: 'text-ok' };
	});

	// What an *arr holding the wrong connection, or none, means for the reader.
	const WEBHOOK_STATE: Record<string, string> = {
		connected: 'Webhook connected',
		unreachable: 'Its webhook call never arrives',
		missing: 'No webhook connection yet',
		stale: 'Its webhook points somewhere else',
		unknown: 'Could not read its connections list'
	};

	// Unreachable is a fault, not a step before a save.
	function webhookTone(state: string): string {
		if (state === 'connected') return 'text-ok';
		return state === 'unreachable' ? 'text-danger' : 'text-dim';
	}

	function webhookNote(answer: ConnectionResult): string {
		if (callbackEdited) return 'Save the webhook address to test the new callback.';
		const state = WEBHOOK_STATE[answer.webhook] ?? '';
		if (!state || answer.webhook === 'connected' || answer.webhook === 'unknown') return state;
		// Saving would only rewrite what it already holds. The *arr's own words
		// below say what to change instead.
		if (answer.webhook === 'unreachable') return state;
		return `${state}. Trackstarr registers it on save.`;
	}

	// The shut card's line shows a connection problem or its address.
	const line = $derived.by(() => {
		if (result && !result.ok) return { text: result.detail, class: 'text-danger', mono: false };
		if (result?.ok && result.webhook && result.webhook !== 'connected') {
			return {
				text: WEBHOOK_STATE[result.webhook] ?? '',
				class: result.webhook === 'unreachable' ? 'text-danger' : 'text-accent',
				mono: false
			};
		}
		const address = (draft[service.url] as string) ?? '';
		if (address) return { text: address, class: 'text-dim', mono: true };
		return { text: '', class: 'text-faint', mono: false };
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
			<ServiceIcon
				name={service.type ?? (service.name as import('$lib/connections').ServiceName)}
			/>
			<span class="min-w-0 flex-1">
				<span class="flex items-center gap-2">
					<span class="text-sm font-semibold">{service.label}</span>
					{#if edited}
						<span class="h-1.5 w-1.5 flex-none rounded-full bg-accent-fill"></span>
						<span class="sr-only">Unsaved changes</span>
					{/if}
				</span>
				{#if line.text}
					<span
						class={`mt-0.5 block truncate text-[12.5px] ${line.class} ${line.mono ? 'font-mono' : ''}`}
					>
						{line.text}
					</span>
				{/if}
				{#if result?.paths === 'attention' || result?.paths === 'unknown'}
					<span class="mt-1 block text-[12px] text-accent">
						{result.paths === 'attention'
							? 'Library paths need attention'
							: 'Library paths could not be checked'}
					</span>
				{/if}
			</span>
			<span class={`flex-none text-[11px] font-medium ${pill.class}`}>{pill.label}</span>
			{@render chevron()}
		{/snippet}

		{#snippet panel()}
			<div class="border-t border-line px-3 pb-1">
				{#if result}
					<!-- Dimmed while a fresh answer is on its way, so a repeat press
					     landing on the same words still reads as having run. -->
					<div
						class={`my-3 ${noteBox} transition-opacity duration-150 ${result.ok ? 'text-dim' : 'text-danger'} ${checking ? 'opacity-(--disabled)' : ''}`}
					>
						<p>{result.detail}</p>
						{#if result.ok && webhookNote(result)}
							<p class={`mt-1 ${webhookTone(result.webhook)}`}>{webhookNote(result)}</p>
						{/if}
						<!-- Unattributed: it follows the line it explains, and the *arr
						     does not always give words to quote. -->
						{#if result.webhook_detail}
							<p class="mt-1 text-faint">{result.webhook_detail}</p>
						{/if}
						{#if mappingEdited}
							<p class="mt-1 text-faint">
								Path checks use the saved mapping. Save your changes to test the new mapping.
							</p>
						{/if}
						{#if result.hint}
							<p class="mt-1 break-words whitespace-pre-line text-faint">{result.hint}</p>
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
								bind:this={addressInput}
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

				{#if service.nameField}
					{@const name = service.nameField}
					<SettingRow {name} label="Name" desc={nameDesc} stack>
						{#snippet children({ describedBy })}
							<div class={entryRow}>
								<input
									value={nameText}
									oninput={(event) => onname?.(event.currentTarget.value)}
									placeholder={fallbackName(service)}
									maxlength="40"
									autocapitalize="words"
									autocorrect="off"
									spellcheck="false"
									aria-label={`${service.label} name`}
									aria-describedby={describedBy}
									aria-invalid={!!nameProblem}
									disabled={settings.envLocked(name)}
									class={entry}
								/>
								<div class={gutter}></div>
							</div>
							{#if nameProblem}<p role="alert" class="mt-1.5 text-[12.5px] text-danger">
									{nameProblem}
								</p>{/if}
						{/snippet}
					</SettingRow>
				{/if}

				{#if service.publicUrl}
					{@const name = service.publicUrl}
					<SettingRow
						{name}
						label="Public address"
						desc={`Optional address to use for ${service.label} links, instead of the service address.`}
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
					<SettingRow
						{name}
						label="Path map"
						desc={`Only needed when ${service.label} sees the library at a different path. Ours first.`}
					>
						<button
							type="button"
							onclick={() => pathMap?.add()}
							disabled={settings.envLocked(name)}
							aria-label={`Add a ${service.label} path pair`}
							class={button}
						>
							<Glyph name="plus" /> Add
						</button>
						{#snippet below({ labelledBy, describedBy })}
							<PathMapList
								bind:this={pathMap}
								{settings}
								{name}
								label={service.label}
								{labelledBy}
								{describedBy}
								{gutter}
							/>
						{/snippet}
					</SettingRow>
				{/if}

				<div class="flex justify-end gap-2 py-3">
					<!-- Held wide, so Checking does not shift it. -->
					<button
						type="button"
						onclick={() => test(600)}
						disabled={readOnly || !configured || checking}
						aria-label={checking ? `Checking ${service.label}` : `Test ${service.label}`}
						aria-busy={checking}
						class={`min-w-28 flex-none ${button}`}
					>
						<span class="flex" class:turning={checking}><Glyph name="refresh" /></span>
						<span role="status">{checking ? 'Checking…' : 'Test'}</span>
					</button>
					{#if onremove}
						<button
							type="button"
							class={button}
							disabled={readOnly}
							onclick={onremove}
							aria-label={`Remove ${service.label}`}>Remove</button
						>
					{/if}
				</div>
			</div>
		{/snippet}
	</Disclosure>
</div>
